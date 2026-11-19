"""Front resolver: company override first, then _default; missing overrides fall back."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app import deps, fronts
from app.fronts import DEFAULT_FRONT, FRONTS_DIR, resolved_front, search_path, templates_for


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- lookup order -------------------------------------------------------------


def test_search_path_puts_company_override_first():
    assert search_path("harbor-yard") == [
        FRONTS_DIR / "harbor-yard" / "templates",
        FRONTS_DIR / DEFAULT_FRONT / "templates",
    ]


@pytest.mark.parametrize("slug", [None, "", "_default", "../etc", "harbor-yard/../x", "Harbor-Yard", "a" * 61])
def test_invalid_or_missing_slug_uses_only_default(slug):
    assert search_path(slug) == [FRONTS_DIR / DEFAULT_FRONT / "templates"]


def test_resolved_front_per_template():
    assert resolved_front("harbor-yard", "partials/brand.html") == "harbor-yard"
    assert resolved_front("harbor-yard", "home.html") == DEFAULT_FRONT  # not overridden
    assert resolved_front("campus-ops", "partials/brand.html") == DEFAULT_FRONT  # no folder
    assert resolved_front("harbor-yard", "does/not/exist.html") == DEFAULT_FRONT


def test_environments_are_cached_per_slug():
    assert templates_for("harbor-yard") is templates_for("harbor-yard")
    assert templates_for("harbor-yard") is not templates_for("campus-ops")
    assert templates_for(None) is templates_for("../evil")  # both: default only


def test_every_override_has_a_default_counterpart():
    """A company-only template would break every other company; overrides must shadow a default."""
    default = FRONTS_DIR / DEFAULT_FRONT / "templates"
    overrides = [
        p for p in FRONTS_DIR.glob("*/templates/**/*.html") if p.relative_to(FRONTS_DIR).parts[0] != DEFAULT_FRONT
    ]
    assert overrides, "expected at least the harbor-yard demo override"
    for path in overrides:
        relative = Path(*path.relative_to(FRONTS_DIR).parts[2:])
        assert (default / relative).is_file(), relative


# --- real fronts through the app ------------------------------------------------


def test_harbor_yard_gets_its_brand_override(client, login_as, ui):
    login_as("hana.tech@harbor-yard.example")
    body = client.get("/").text
    assert 'data-front="harbor-yard"' in body
    assert ui("front.harborYard.title") in body
    assert ui("home.heading") in body  # home.html itself comes from _default


def test_campus_ops_and_login_page_use_default_front(client, login_as, ui):
    assert 'data-front="_default"' in client.get("/login").text
    login_as("morgan.manager@facility.example")
    body = client.get("/").text
    assert 'data-front="_default"' in body
    assert ui("front.harborYard.title") not in body


def test_same_person_sees_the_front_of_the_company_signed_in_to(client, login_as):
    login_as("sam.rivera@mail.example", company="harbor-yard")
    assert 'data-front="harbor-yard"' in client.get("/").text
    client.post("/logout")
    login_as("sam.rivera@mail.example", company="campus-ops")
    assert 'data-front="_default"' in client.get("/").text


@pytest.mark.parametrize("path", ["/", "/requests", "/requests/new", "/reports"])
def test_harbor_pages_render_through_fallback(client, login_as, path):
    login_as("sam.rivera@mail.example", company="harbor-yard")
    response = client.get(path)
    assert response.status_code == 200
    assert 'data-front="harbor-yard"' in response.text


# --- isolated fronts tree: fallback and extends --------------------------------


@pytest.fixture()
def tmp_fronts(tmp_path, monkeypatch):
    """A full copy of _default plus test overrides; the app renders from it."""
    root = tmp_path / "fronts"
    shutil.copytree(FRONTS_DIR / DEFAULT_FRONT, root / DEFAULT_FRONT)
    monkeypatch.setattr(deps, "templates_for", lambda slug: fronts.templates_for(slug, root=root))
    return root


def test_override_of_one_partial_falls_back_for_everything_else(client, login_as, tmp_fronts):
    _write(tmp_fronts / "campus-ops" / "templates" / "partials" / "brand.html", '<div data-front="campus-test">CAMPUS OVERRIDE</div>')
    login_as("morgan.manager@facility.example")
    for path in ("/", "/requests", "/requests/new", "/reports"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "CAMPUS OVERRIDE" in response.text, path


def test_empty_override_folder_falls_back_completely(client, login_as, tmp_fronts):
    (tmp_fronts / "campus-ops" / "templates").mkdir(parents=True)
    login_as("morgan.manager@facility.example")
    response = client.get("/requests")
    assert response.status_code == 200
    assert 'data-front="_default"' in response.text


def test_override_page_extends_the_company_base(tmp_fronts):
    """{% extends "base.html" %} in a default page picks up an overridden base."""
    _write(tmp_fronts / "acme" / "templates" / "base.html", "ACME-BASE {% block content %}{% endblock %}")
    _write(tmp_fronts / DEFAULT_FRONT / "templates" / "probe.html", '{% extends "base.html" %}{% block content %}probe{% endblock %}')
    acme = fronts.templates_for("acme", root=tmp_fronts).env.get_template("probe.html").render()
    other = fronts.templates_for("other-co", root=tmp_fronts).env.get_template("probe.html")
    assert acme.strip() == "ACME-BASE probe"
    assert other.name == "probe.html"


def test_resolved_front_with_custom_root(tmp_fronts):
    _write(tmp_fronts / "acme" / "templates" / "home.html", "x")
    assert resolved_front("acme", "home.html", root=tmp_fronts) == "acme"
    assert resolved_front("acme", "login.html", root=tmp_fronts) == DEFAULT_FRONT
