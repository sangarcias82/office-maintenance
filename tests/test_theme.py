"""Company look: generic theme tokens (CSS variables) + front files (brand partial, theme.css)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.fronts import resolved_front
from app.models import Company
from app.seed import DEMO_THEME_TOKENS
from app.theme_tokens import THEME_TOKENS, css_variables, validate_theme_tokens
from theme_helpers import body_attrs, effective_tokens, root_vars

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


def _company(db_session, slug) -> Company:
    return db_session.scalar(select(Company).where(Company.slug == slug))


def _token_block(html: str) -> str | None:
    match = re.search(r'<style id="company-tokens">(.*?)</style>', html, flags=re.S)
    return match.group(1) if match else None


# --- validation -----------------------------------------------------------------


def test_valid_tokens_are_normalized():
    assert validate_theme_tokens({"accent": "#1F5F8B", "bg-glow": "#1f5f8b24", "line": "#abc"}) == {
        "accent": "#1f5f8b",
        "bg-glow": "#1f5f8b24",
        "line": "#abc",
    }
    assert validate_theme_tokens(None) == {}


@pytest.mark.parametrize(
    "tokens",
    [
        {"logo-url": "#fff"},  # unknown token name
        {"accent": "red"},
        {"accent": "#12"},
        {"accent": "#1f5f8b; } body { display: none"},
        {"accent": "url(https://evil.example/x.png)"},
        {"accent": 123},
        ["accent", "#fff"],
        "accent=#fff",
    ],
)
def test_invalid_tokens_are_rejected(tokens):
    with pytest.raises(ValueError):
        validate_theme_tokens(tokens)
    with pytest.raises(ValueError):
        Company(slug="x-co", display_name="X", theme_tokens=tokens)


def test_empty_tokens_are_stored_as_null():
    assert Company(slug="x-co", display_name="X", theme_tokens={}).theme_tokens is None


def test_css_variables_drop_anything_invalid():
    dirty = {"accent": "#ABCDEF", "bg": "red;}", "evil": "#fff", "ink": None}
    assert css_variables(dirty) == [("accent", "#abcdef")]
    assert css_variables("not a mapping") == []


def test_every_token_is_a_css_variable_in_app_css():
    root = re.search(r":root\s*\{(.*?)\}", (STATIC / "css" / "app.css").read_text(), flags=re.S).group(1)
    defined = set(re.findall(r"--([\w-]+)\s*:", root))
    assert set(THEME_TOKENS) <= defined


def test_token_names_are_generic():
    for name in THEME_TOKENS:
        assert not re.search(r"campus|harbor|yard|logo|brand", name), name


def test_background_glow_is_a_token_not_hardcoded():
    css = (STATIC / "css" / "app.css").read_text()
    assert "radial-gradient(circle at top left, var(--bg-glow)" in css
    assert "rgba(196, 92, 38" not in css


def test_charts_follow_the_theme():
    js = (STATIC / "js" / "charts.js").read_text()
    assert 'cssVar("--accent"' in js and 'cssVar("--panel"' in js


# --- per-company look: distinct, stable hooks ----------------------------------------

HARBOR_THEME = Path(__file__).resolve().parent.parent / "app" / "fronts" / "harbor-yard" / "templates" / "partials" / "theme.css"

# What each demo company must look like. These are the stable hooks: body attributes
# plus the accent and background the browser ends up using.
COMPANY_LOOKS = {
    "campus-ops": {
        "login": "morgan.manager@facility.example",
        "data_theme": "_default",
        "accent": "#c45c26",  # today's orange (app.css default)
        "bg": "#f3efe6",      # today's warm beige
    },
    "harbor-yard": {
        "login": "hana.tech@harbor-yard.example",
        "data_theme": "harbor-yard",
        "accent": "#1f5f8b",  # navy, from the Harbor Yard front file
        "bg": "#e9eff4",      # steel blue
    },
}


@pytest.mark.parametrize("slug", list(COMPANY_LOOKS))
def test_each_company_has_a_distinct_stable_hook(client, login_as, slug):
    look = COMPANY_LOOKS[slug]
    login_as(look["login"], company=slug)
    for path in ("/", "/requests", "/requests/new" if slug == "campus-ops" else "/maintenance/new"):
        html = client.get(path).text
        body = body_attrs(html)
        assert body["data-company"] == slug, path
        assert body["data-theme"] == look["data_theme"], path
        assert f"theme-{look['data_theme']}" in body["class"].split(), path
        tokens = effective_tokens(html)
        assert (tokens["accent"], tokens["bg"]) == (look["accent"], look["bg"]), path


def test_the_two_companies_look_different(client, login_as):
    seen = {}
    for slug, look in COMPANY_LOOKS.items():
        login_as(look["login"], company=slug)
        html = client.get("/").text
        tokens = effective_tokens(html)
        seen[slug] = (body_attrs(html)["data-theme"], tokens["accent"], tokens["bg"], tokens["bg-deep"], tokens["panel"])
        client.post("/logout")
    campus, harbor = seen["campus-ops"], seen["harbor-yard"]
    assert all(a != b for a, b in zip(campus, harbor)), seen  # theme hook, accent, both backgrounds, panels


def test_harbor_palette_lives_in_its_front_file(db_session):
    """The front file itself is the theme hook (not only the brand copy, not the database)."""
    palette = root_vars(HARBOR_THEME.read_text())
    assert palette["accent"] == "#1f5f8b" and palette["bg"] == "#e9eff4"
    assert set(palette) <= set(THEME_TOKENS)  # same generic token names as theme_tokens
    assert 'body[data-theme="harbor-yard"]' in HARBOR_THEME.read_text()  # page-level treatment keyed on the hook
    for slug in COMPANY_LOOKS:  # demo companies need no database tokens
        assert _company(db_session, slug).theme_tokens is None
    assert DEMO_THEME_TOKENS == {"campus-ops": {}, "harbor-yard": {}}


def test_login_page_has_the_neutral_hook(client):
    body = body_attrs(client.get("/login").text)
    assert body["data-company"] == "" and body["data-theme"] == "_default"
    assert effective_tokens(client.get("/login").text)["accent"] == COMPANY_LOOKS["campus-ops"]["accent"]


def test_hook_follows_the_company_signed_in_to(client, login_as):
    login_as("sam.rivera@mail.example", company="harbor-yard")
    html = client.get("/").text
    assert body_attrs(html)["data-theme"] == "harbor-yard" and effective_tokens(html)["accent"] == "#1f5f8b"
    client.post("/logout")
    login_as("sam.rivera@mail.example", company="campus-ops")
    html = client.get("/").text
    assert body_attrs(html)["data-theme"] == "_default" and effective_tokens(html)["accent"] == "#c45c26"


def test_company_tokens_override_the_front(client, login_as, db_session):
    """theme_tokens (generic DB field) still layer on top: campus gets a tweak, harbor's front is overridden."""
    _company(db_session, "campus-ops").theme_tokens = {"accent": "#2f6b4f"}
    _company(db_session, "harbor-yard").theme_tokens = {"bg": "#ffffff"}
    db_session.commit()
    login_as("morgan.manager@facility.example")
    assert effective_tokens(client.get("/").text)["accent"] == "#2f6b4f"
    client.post("/logout")
    login_as("hana.tech@harbor-yard.example")
    tokens = effective_tokens(client.get("/").text)
    assert tokens["bg"] == "#ffffff" and tokens["accent"] == "#1f5f8b"  # token wins; rest from the front


def test_no_company_tokens_block_without_tokens(client, login_as):
    for slug, look in COMPANY_LOOKS.items():
        login_as(look["login"], company=slug)
        assert _token_block(client.get("/").text) is None, slug
        client.post("/logout")


def test_tampered_tokens_are_not_rendered(client, login_as, db_session):
    """Even if a bad value bypasses the ORM validator, it never reaches the page."""
    db_session.execute(
        text("UPDATE company SET theme_tokens = :t WHERE slug = 'harbor-yard'"),
        {"t": '{"accent": "#123456", "bg": "red;} body{display:none", "evil": "#fff"}'},
    )
    db_session.commit()
    login_as("hana.tech@harbor-yard.example")
    html = client.get("/").text
    assert _token_block(html).strip() == ":root { --accent: #123456; }"
    assert "display:none" not in html


# --- front files: title treatment ---------------------------------------------------


def test_front_files_resolve_per_company():
    assert resolved_front("harbor-yard", "partials/theme.css") == "harbor-yard"
    assert resolved_front("harbor-yard", "partials/brand.html") == "harbor-yard"
    assert resolved_front("campus-ops", "partials/theme.css") == "_default"


def test_harbor_yard_title_treatment(client, login_as):
    login_as("hana.tech@harbor-yard.example")
    html = client.get("/").text
    assert '<div class="brand brand-harbor" data-front="harbor-yard">' in html
    style = re.search(r'<style id="front-theme">(.*?)</style>', html, flags=re.S).group(1)
    assert ".brand-harbor .brand-title" in style and "border-left: 0.45em solid var(--accent-2)" in style


def test_campus_ops_keeps_the_default_treatment(client, login_as):
    login_as("morgan.manager@facility.example")
    html = client.get("/").text
    assert '<div class="brand" data-front="_default">' in html
    assert "brand-harbor" not in html


@pytest.mark.parametrize("path", ["/", "/requests", "/requests/new", "/reports"])
def test_themed_pages_render(client, login_as, path):
    login_as("sam.rivera@mail.example", company="harbor-yard")
    response = client.get(path)
    assert response.status_code == 200 and "brand-harbor" in response.text
