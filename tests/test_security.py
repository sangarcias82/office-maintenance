"""Hardening: no session forgery with the public placeholder secret, security headers,
request size limit."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy import func, select

from app.config import get_settings
from app.db import get_db
from app.models import MaintenanceRecord, PhotoEvidence
from app.security import (
    CONTENT_SECURITY_POLICY,
    PLACEHOLDER_SECRET,
    SECURITY_HEADERS,
    SecurityMiddleware,
    resolve_session_secret,
)

FRONTS = Path(__file__).resolve().parent.parent / "app" / "fronts"


def _forge(secret: str, data: dict) -> str:
    return TimestampSigner(secret).sign(base64.b64encode(json.dumps(data).encode())).decode()


# --- session secret --------------------------------------------------------------


@pytest.mark.parametrize("configured", [None, "", "   ", PLACEHOLDER_SECRET])
def test_placeholder_or_missing_secret_is_replaced(configured, caplog):
    with caplog.at_level(logging.WARNING, logger="app.security"):
        first = resolve_session_secret(configured)
        second = resolve_session_secret(configured)
    assert first != PLACEHOLDER_SECRET and len(first) >= 48
    assert first != second  # random per call/process
    assert "random per-process secret" in caplog.text


def test_short_secret_is_kept_but_warned(caplog):
    with caplog.at_level(logging.WARNING, logger="app.security"):
        assert resolve_session_secret("short-secret") == "short-secret"
    assert "shorter than 32" in caplog.text


def test_strong_secret_is_used_as_is(caplog):
    strong = "x" * 40
    with caplog.at_level(logging.WARNING, logger="app.security"):
        assert resolve_session_secret(strong) == strong
    assert caplog.text == ""


@pytest.fixture()
def placeholder_app(db_session, monkeypatch):
    """The app as started from .env.example / without .env: SECRET_KEY is the placeholder."""
    from app.main import create_app

    monkeypatch.setenv("SECRET_KEY", PLACEHOLDER_SECRET)
    get_settings.cache_clear()
    application = create_app()
    application.dependency_overrides[get_db] = lambda: db_session
    with TestClient(application) as client:
        yield client
    get_settings.cache_clear()


def test_forged_session_with_placeholder_secret_is_refused(placeholder_app, lookup):
    """Before the fix, this cookie signed the attacker in as the manager without a password."""
    cookie = _forge(PLACEHOLDER_SECRET, {"user_id": lookup.user("R-3001").id})
    placeholder_app.cookies.set("maintenance_session", cookie, domain="testserver.local", path="/")
    response = placeholder_app.get("/reports", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/login")


def test_placeholder_app_still_works_for_real_logins(placeholder_app):
    page = placeholder_app.get("/login").text
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    response = placeholder_app.post(
        "/login",
        data={"csrf_token": token, "company": "campus-ops", "login": "R-3001", "password": "OfficeDemo1!"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/"
    assert placeholder_app.get("/reports").status_code == 200


# --- security headers --------------------------------------------------------------


def _assert_security_headers(response):
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get(name) == value, name


def test_html_pages_get_security_headers_and_no_store(client, login_as):
    _assert_security_headers(client.get("/login"))
    assert client.get("/login").headers["cache-control"] == "no-store"
    login_as("morgan.manager@facility.example")
    for path in ("/", "/requests", "/reports"):
        response = client.get(path)
        _assert_security_headers(response)
        assert response.headers["cache-control"] == "no-store", path


def test_csp_forbids_framing_inline_scripts_and_foreign_forms():
    assert "frame-ancestors 'none'" in CONTENT_SECURITY_POLICY
    assert "script-src 'self'" in CONTENT_SECURITY_POLICY and "unsafe-inline' https://fonts" in CONTENT_SECURITY_POLICY
    assert "form-action 'self'" in CONTENT_SECURITY_POLICY
    assert SECURITY_HEADERS["X-Frame-Options"] == "DENY"


def test_templates_have_no_inline_scripts_or_handlers():
    """The CSP allows only script files from this site; templates must not rely on inline JS."""
    for path in FRONTS.rglob("*.html"):
        html = path.read_text(encoding="utf-8")
        for tag in re.findall(r"<script\b[^>]*>", html):
            assert "src=" in tag, f"inline <script> in {path}"
        assert not re.search(r"\son[a-z]+\s*=", html), f"inline event handler in {path}"


def test_static_files_get_headers_but_stay_cacheable(client):
    response = client.get("/static/css/app.css")
    _assert_security_headers(response)
    assert "no-store" not in response.headers.get("cache-control", "")


def test_evidence_keeps_its_own_cache_policy(client, login_as, db_session):
    login_as("morgan.manager@facility.example")
    photo = db_session.scalar(select(PhotoEvidence).order_by(PhotoEvidence.id))
    response = client.get(f"/evidence/{photo.id}")
    _assert_security_headers(response)
    assert response.headers["cache-control"] == "private, no-store"


def test_redirects_and_refusals_get_headers(client):
    _assert_security_headers(client.get("/", follow_redirects=False))
    _assert_security_headers(client.post("/requests", data={}, csrf=False, follow_redirects=False))


def test_no_hsts_unless_https_only(client):
    assert "strict-transport-security" not in client.get("/login").headers


# --- request size limit ---------------------------------------------------------------


def _counts(db_session):
    db_session.expire_all()
    return tuple(db_session.scalar(select(func.count()).select_from(m)) for m in (MaintenanceRecord, PhotoEvidence))


def test_oversized_request_is_refused_before_any_route(client, login_as, db_session, lookup):
    login_as("tess.tech@facility.example")
    before = _counts(db_session)
    huge = b"\x89PNG\r\n\x1a\n" + b"0" * (get_settings().max_request_bytes + 1)
    response = client.post(
        "/maintenance",
        data={"request_id": lookup.request("REQ-2026-0002").id, "work_performed": "big", "service_status_id": "2"},
        files={"evidenceFile": ("big.png", huge, "image/png")},
        follow_redirects=False,
    )
    assert response.status_code == 413
    _assert_security_headers(response)
    assert _counts(db_session) == before


def test_normal_sized_requests_pass(client, login_as):
    login_as("morgan.manager@facility.example")  # a regular form POST is well under the limit
    assert client.get("/").status_code == 200


def _call(middleware, scope) -> list[dict]:
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(middleware(scope, receive, send))
    return sent


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/html")]})
    await send({"type": "http.response.body", "body": b"ok"})


def _scope(headers=()):
    return {"type": "http", "method": "POST", "path": "/x", "headers": list(headers)}


@pytest.mark.parametrize(("length", "status"), [(b"abc", 400), (b"-5", 400), (b"2000", 413), (b"10", 200)])
def test_middleware_content_length_handling(length, status):
    middleware = SecurityMiddleware(_ok_app, max_body_bytes=1024)
    start = _call(middleware, _scope([(b"content-length", length)]))[0]
    assert start["status"] == status


def test_middleware_adds_hsts_when_enabled():
    start = _call(SecurityMiddleware(_ok_app, max_body_bytes=1024, hsts=True), _scope())[0]
    names = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    assert names["strict-transport-security"].startswith("max-age=")
    assert names["cache-control"] == "no-store"


def test_middleware_passes_non_http_scopes_through():
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["type"])

    asyncio.run(SecurityMiddleware(app, max_body_bytes=1)({"type": "lifespan"}, None, None))
    assert seen == ["lifespan"]
