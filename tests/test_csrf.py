"""Per-session CSRF token: issued by render(), posted by every form, verified on every POST."""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.csrf import tokens_match
from app.models import MaintenanceRecord, PhotoEvidence, ServiceRequest

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "fronts"  # _default + company overrides
TOKEN_FIELD = re.compile(r'name="csrf_token" value="([^"]*)"')


def _tokens_in(html: str) -> list[str]:
    return TOKEN_FIELD.findall(html)


def _count(db_session, model) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(model))


# --- token lifecycle ----------------------------------------------------------


def test_rendered_page_exposes_the_session_token(client, session_data):
    html = client.get("/login").text
    assert _tokens_in(html) == [session_data()["_csrf"]]
    assert len(session_data()["_csrf"]) >= 40  # 32 random bytes, url-safe base64


def test_token_is_stable_within_a_session(client, session_data):
    client.get("/login")
    first = session_data()["_csrf"]
    client.get("/login")
    assert session_data()["_csrf"] == first


def test_token_differs_per_session(client, session_data):
    client.get("/login")
    first = session_data()["_csrf"]
    client.cookies.clear()
    client.get("/login")
    assert session_data()["_csrf"] != first


def test_login_and_logout_rotate_the_token(client, login_as, session_data):
    client.get("/login")
    anonymous = session_data()["_csrf"]
    login_as("ada.employee@facility.example")
    assert "_csrf" not in session_data()  # cleared with the rest of the pre-login session
    client.get("/")
    signed_in = session_data()["_csrf"]
    assert signed_in != anonymous
    client.post("/logout", follow_redirects=False)
    assert session_data() == {}


# --- every form carries it, every POST route checks it -------------------------


def test_every_post_form_in_templates_has_the_hidden_field():
    forms = []
    for path in TEMPLATES.rglob("*.html"):
        for form in re.findall(r"<form\b.*?</form>", path.read_text(encoding="utf-8"), flags=re.S | re.I):
            if re.search(r'method="post"', form, flags=re.I):
                forms.append((path.name, form))
    assert len(forms) == 5  # login, logout, new request, assign, log work
    for name, form in forms:
        assert '<input type="hidden" name="csrf_token" value="{{ csrf_token }}"/>' in form, name


def test_every_post_route_rejects_a_missing_token(client, login_as, ui):
    """Behavioural check over the app's own route table (OpenAPI), so a new POST route
    without require_csrf fails here regardless of FastAPI's internal route layout."""
    post_paths = sorted(path for path, ops in client.app.openapi()["paths"].items() if "post" in ops)
    assert post_paths == ["/login", "/logout", "/maintenance", "/maintenance/", "/requests", "/requests/", "/requests/assign"]
    login_as("morgan.manager@facility.example")
    for path in post_paths:
        response = client.post(path, data={}, csrf=False, follow_redirects=False)
        assert response.status_code == 303, path
        assert ui("csrf.invalid") in client.get("/").text, path
    assert client.get("/", follow_redirects=False).status_code == 200  # /logout did not log out


@pytest.mark.parametrize(
    ("login", "path", "forms"),
    [
        (None, "/login", 1),
        ("ada.employee@facility.example", "/", 1),  # logout button
        ("ada.employee@facility.example", "/requests/new", 2),  # create + logout
        ("morgan.manager@facility.example", "/requests", None),  # one assign form per open ticket + logout
        ("tess.tech@facility.example", "/maintenance/new", 2),  # log work + logout
    ],
)
def test_rendered_forms_carry_the_current_token(client, login_as, session_data, login, path, forms):
    if login:
        login_as(login)
    html = client.get(path).text
    tokens = _tokens_in(html)
    assert tokens and set(tokens) == {session_data()["_csrf"]}
    if forms is not None:
        assert len(tokens) == forms
    else:
        assert len(tokens) == html.count('action="/requests/assign"') + 1


# --- rejected writes ------------------------------------------------------------

BAD_TOKENS = [
    pytest.param({}, id="missing"),
    pytest.param({"csrf_token": ""}, id="empty"),
    pytest.param({"csrf_token": "forged-token"}, id="wrong"),
    pytest.param({"csrf_token": "\u00e9\u00e8"}, id="non-ascii"),
]


def _reject(client, url, data, **kwargs):
    response = client.post(url, data=data, csrf=False, follow_redirects=False, **kwargs)
    assert response.status_code == 303
    return response


@pytest.mark.parametrize("token", BAD_TOKENS)
def test_login_without_valid_token_does_not_sign_in(client, session_data, ui, token):
    client.get("/login")
    data = {"company": "campus-ops", "login": "E-1001", "password": "OfficeDemo1!", **token}
    _reject(client, "/login", data)
    assert "user_id" not in session_data()
    assert ui("csrf.invalid") in client.get("/login").text


@pytest.mark.parametrize("token", BAD_TOKENS)
def test_logout_without_valid_token_keeps_session(client, login_as, session_data, token):
    login_as("ada.employee@facility.example")
    _reject(client, "/logout", token)
    assert "user_id" in session_data()
    assert client.get("/", follow_redirects=False).status_code == 200


@pytest.mark.parametrize("token", BAD_TOKENS)
def test_create_request_without_valid_token_writes_nothing(client, login_as, db_session, lookup, ui, token):
    login_as("ada.employee@facility.example")
    before = _count(db_session, ServiceRequest)
    data = {
        "location_id": lookup.location_id("OFC-A-1-ADM"),
        "priority_id": lookup.priority_id("LOW"),
        "maintenance_type_id": lookup.type_id("CORRECTIVE"),
        "problem_description": "CSRF probe",
        **token,
    }
    _reject(client, "/requests", data)
    assert _count(db_session, ServiceRequest) == before
    assert ui("csrf.invalid") in client.get("/").text


@pytest.mark.parametrize("token", BAD_TOKENS)
def test_assign_without_valid_token_writes_nothing(client, login_as, db_session, lookup, token):
    login_as("morgan.manager@facility.example")
    pending = lookup.request("REQ-2026-0001")
    data = {"requestId": pending.id, "technicianId": lookup.user("T-2001").id, **token}
    _reject(client, "/requests/assign", data)
    db_session.expire_all()
    refreshed = db_session.get(ServiceRequest, pending.id)
    assert refreshed.assigned_technician_id is None
    assert refreshed.status.code == "PENDING"


@pytest.mark.parametrize("token", BAD_TOKENS)
def test_log_work_without_valid_token_writes_nothing(client, login_as, db_session, lookup, token):
    """Multipart form with a photo: still rejected before anything is stored."""
    from app.config import get_settings

    login_as("tess.tech@facility.example")
    req = lookup.request("REQ-2026-0002")
    records, photos = _count(db_session, MaintenanceRecord), _count(db_session, PhotoEvidence)
    upload_root = Path(get_settings().upload_dir)
    files_before = sorted(upload_root.rglob("*"))
    data = {
        "request_id": req.id,
        "work_performed": "CSRF probe",
        "service_status_id": lookup.status_id("COMPLETED"),
        **token,
    }
    _reject(client, "/maintenance", data, files={"evidenceFile": ("p.png", io.BytesIO(b"\x89PNG"), "image/png")})
    assert _count(db_session, MaintenanceRecord) == records
    assert _count(db_session, PhotoEvidence) == photos
    assert db_session.get(ServiceRequest, req.id).status.code == "IN_PROGRESS"
    assert sorted(upload_root.rglob("*")) == files_before  # no file written


def test_token_from_another_session_is_rejected(client, db_session, login_as, lookup):
    """An attacker's own valid token does not work in the victim's session."""
    attacker_token = _tokens_in(client.get("/login").text)[0]  # valid, but for the attacker's session
    client.cookies.clear()
    login_as("ada.employee@facility.example")
    before = _count(db_session, ServiceRequest)
    _reject(
        client,
        "/requests",
        {
            "csrf_token": attacker_token,
            "location_id": lookup.location_id("OFC-A-1-ADM"),
            "priority_id": lookup.priority_id("LOW"),
            "maintenance_type_id": lookup.type_id("CORRECTIVE"),
            "problem_description": "cross-session",
        },
    )
    assert _count(db_session, ServiceRequest) == before


def test_rejection_message_is_translated_and_logged(client, login_as, set_lang, ui, caplog):
    login_as("ada.employee@facility.example")
    set_lang("es")
    with caplog.at_level("WARNING", logger="app.csrf"):
        _reject(client, "/logout", {})
    assert "CSRF check failed for POST /logout" in caplog.text
    assert ui("csrf.invalid", "es") in client.get("/").text


# --- where a rejected form goes -----------------------------------------------


@pytest.mark.parametrize(
    ("referer", "location"),
    [
        ("http://testserver/requests/new", "/requests/new"),
        ("http://testserver/requests?q=leak&statusId=1", "/requests?q=leak&statusId=1"),
        ("https://evil.example/requests/new", "/"),
        ("http://testserver//evil.example", "/"),
        (None, "/"),
    ],
)
def test_rejected_form_returns_to_same_site_page(client, login_as, referer, location):
    login_as("ada.employee@facility.example")
    headers = {"referer": referer} if referer else {}
    response = _reject(client, "/requests", {"problem_description": "x"}, headers=headers)
    assert response.headers["location"] == location


# --- the real browser flow (no automatic token) --------------------------------


def test_browser_flow_with_scraped_tokens(client, db_session, lookup, session_data):
    login_page = client.get("/login").text
    token = _tokens_in(login_page)[0]
    response = client.post(
        "/login",
        data={"csrf_token": token, "company": "campus-ops", "login": "E-1001", "password": "OfficeDemo1!"},
        csrf=False,
        follow_redirects=False,
    )
    assert response.headers["location"] == "/"

    form_page = client.get("/requests/new").text
    new_token = _tokens_in(form_page)[0]
    assert new_token != token  # rotated at login
    before = _count(db_session, ServiceRequest)
    response = client.post(
        "/requests",
        data={
            "csrf_token": new_token,
            "location_id": lookup.location_id("OFC-A-1-ADM"),
            "priority_id": lookup.priority_id("LOW"),
            "maintenance_type_id": lookup.type_id("CORRECTIVE"),
            "problem_description": "Submitted with the scraped token",
        },
        csrf=False,
        follow_redirects=False,
    )
    assert response.headers["location"] == "/requests"
    assert _count(db_session, ServiceRequest) == before + 1

    # The pre-login token is dead after login.
    _reject(client, "/logout", {"csrf_token": token})
    assert "user_id" in session_data()


# --- cookie and helpers -----------------------------------------------------------


def test_session_cookie_stays_samesite_lax(client):
    response = client.get("/login")
    cookie = response.headers["set-cookie"].lower()
    assert "samesite=lax" in cookie and "httponly" in cookie


@pytest.mark.parametrize(
    ("sent", "expected", "ok"),
    [("abc", "abc", True), ("abc", "abd", False), ("", "abc", False), (None, "abc", False), ("abc", None, False)],
)
def test_tokens_match(sent, expected, ok):
    assert tokens_match(sent, expected) is ok
