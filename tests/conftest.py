from __future__ import annotations

import base64
import json
import os
import shutil
from pathlib import Path
from typing import Optional

import pytest
from fastapi.testclient import TestClient
from markupsafe import escape
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

# Isolate test DB before app imports bind the engine.
TEST_DB = Path(__file__).resolve().parent / "test_maintenance.db"
if TEST_DB.exists():
    TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["DEMO_PASSWORD"] = "OfficeDemo1!"
os.environ["UPLOAD_DIR"] = str(Path(__file__).resolve().parent / "test_uploads")
# Same PBKDF2 code path, lower work factor: the default (600k) costs ~0.3s per hash,
# and the suite hashes/verifies hundreds of times. Production keeps the default.
os.environ["PASSWORD_HASH_ITERATIONS"] = "1000"

from app.config import get_settings
from app.db import Base, get_db, reset_engine
from app.i18n import t
from app.main import create_app
from app.seed import DEMO_USERS, seed_if_empty

get_settings.cache_clear()
reset_engine(os.environ["DATABASE_URL"])


@pytest.fixture(scope="session")
def seeded_template(tmp_path_factory):
    """Create + seed one database (and its demo evidence files) per test session.

    Each test then gets a file copy of both, which is much faster than
    drop_all/create_all/seed per test and just as isolated.
    """
    from sqlalchemy import create_engine

    root = tmp_path_factory.mktemp("seeded-template")
    uploads = root / "uploads"
    uploads.mkdir()
    os.environ["UPLOAD_DIR"] = str(uploads)
    get_settings.cache_clear()
    db_file = root / "seeded.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine)() as session:
        seed_if_empty(session)
    engine.dispose()
    return db_file, uploads


@pytest.fixture()
def upload_dir(tmp_path, seeded_template):
    """Per-test upload folder holding a fresh copy of the seeded demo evidence."""
    folder = tmp_path / "uploads"
    shutil.copytree(seeded_template[1], folder)
    os.environ["UPLOAD_DIR"] = str(folder)
    get_settings.cache_clear()
    yield folder
    get_settings.cache_clear()


@pytest.fixture()
def db_session(upload_dir, seeded_template):
    """Fresh copy of the seeded database for every test."""
    from app import db as db_module

    db_module.engine.dispose()  # no pooled connection may keep the old file open
    shutil.copyfile(seeded_template[0], TEST_DB)
    Session = sessionmaker(bind=db_module.engine, autoflush=False, autocommit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()


SESSION_COOKIE = "maintenance_session"


def _signer():
    from itsdangerous import TimestampSigner

    return TimestampSigner(str(get_settings().secret_key))


def read_session(client) -> dict:
    """Decode the signed session cookie the app last set (empty dict if none)."""
    raw = client.cookies.get(SESSION_COOKIE)
    if not raw:
        return {}
    payload = _signer().unsign(raw.encode("utf-8"))
    return json.loads(base64.b64decode(payload))


def current_csrf_token(client) -> str:
    """The session's CSRF token, as a rendered form would carry it.

    Rendering any page issues a token; after login/logout the session is new, so
    load a page first (that is what a browser does before posting a form).
    """
    token = read_session(client).get("_csrf")
    if not token:
        client.get("/login", follow_redirects=True)
        token = read_session(client).get("_csrf")
    assert token, "no CSRF token issued"
    return token


class CsrfAwareClient(TestClient):
    """TestClient whose POSTs carry the session CSRF token like the rendered forms do.

    Pass ``csrf=False`` to send no token, or put your own ``csrf_token`` in ``data``
    (tests/test_csrf.py covers missing/wrong tokens and real form scraping).
    """

    def post(self, url, *args, data=None, csrf: bool = True, **kwargs):
        if csrf and (data is None or isinstance(data, dict)) and "csrf_token" not in (data or {}):
            data = {**(data or {}), "csrf_token": current_csrf_token(self)}
        return super().post(url, *args, data=data, **kwargs)


@pytest.fixture()
def client(db_session):
    application = create_app()

    def _override_db():
        try:
            yield db_session
        finally:
            pass

    application.dependency_overrides[get_db] = _override_db
    with CsrfAwareClient(application) as test_client:
        yield test_client
    application.dependency_overrides.clear()
    get_settings.cache_clear()


def company_of(login: str) -> str:
    """Company slug of a demo user, by email or employee number (campus-ops if unknown)."""
    key = login.strip().lower()
    for slug, emp, _first, _last, email, _phone, _role in DEMO_USERS:
        if key in (email, emp.lower()):
            return slug
    return "campus-ops"


@pytest.fixture()
def login_as(client):
    def _login(login: str, password: str = "OfficeDemo1!", company: Optional[str] = None) -> None:
        response = client.post(
            "/login",
            data={
                "company": company or company_of(login),
                "login": login,
                "password": password,
                "next": "/",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303, response.text
        assert response.headers["location"] == "/", "login rejected (CSRF or credentials)"
        assert "user_id" in read_session(client)

    return _login


@pytest.fixture()
def session_data(client):
    """Decode the signed session cookie the app last set (empty dict if none)."""
    return lambda: read_session(client)


@pytest.fixture()
def forge_session(client):
    """Install a validly signed session cookie with arbitrary contents."""

    def _write(data: dict) -> None:
        payload = base64.b64encode(json.dumps(data).encode("utf-8"))
        client.cookies.clear()
        # Same domain the app's cookie gets in the TestClient jar, so the app's
        # Set-Cookie replaces it instead of creating a second cookie.
        client.cookies.set(
            SESSION_COOKIE, _signer().sign(payload).decode("utf-8"), domain="testserver.local", path="/"
        )

    return _write


@pytest.fixture()
def set_lang(client):
    def _set(lang: str) -> None:
        response = client.get(f"/lang/{lang}?next=/login", follow_redirects=False)
        assert response.status_code == 303

    return _set


@pytest.fixture()
def ui():
    """UI text from the language files, HTML-escaped the way Jinja renders it.

    Tests assert on message keys (ui("login.invalid"), ui("nav.home", "es")) instead
    of copying literal strings, so wording changes live only in app/locales/*.json.
    """

    def _ui(key: str, lang: str = "en", **kwargs: str) -> str:
        return str(escape(t(lang, key, **kwargs)))

    return _ui


@pytest.fixture()
def lookup(db_session):
    """Resolve seed rows by stable business keys instead of hard-coded ids."""
    from app import models

    class _Lookup:
        def company(self, slug: str) -> models.Company:
            return db_session.scalar(select(models.Company).where(models.Company.slug == slug))

        def request(self, ticket: str, company: str = "campus-ops") -> models.ServiceRequest:
            """Ticket numbers repeat across companies, so the company is part of the key."""
            return db_session.scalar(
                select(models.ServiceRequest).where(
                    models.ServiceRequest.ticket_number == ticket,
                    models.ServiceRequest.company_id == self.company(company).id,
                )
            )

        def user(self, employee_number: str) -> models.AppUser:
            # Demo employee numbers are distinct across companies.
            return db_session.scalar(
                select(models.AppUser).where(models.AppUser.employee_number == employee_number)
            )

        def status_id(self, code: str) -> int:
            return db_session.scalar(
                select(models.RequestStatus.id).where(models.RequestStatus.code == code)
            )

        def priority_id(self, code: str) -> int:
            return db_session.scalar(select(models.Priority.id).where(models.Priority.code == code))

        def type_id(self, code: str) -> int:
            return db_session.scalar(
                select(models.MaintenanceType.id).where(models.MaintenanceType.code == code)
            )

        def location_id(self, code: str) -> int:
            return db_session.scalar(select(models.Location.id).where(models.Location.code == code))

    return _Lookup()
