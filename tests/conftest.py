from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

# Isolate test DB before app imports bind the engine.
TEST_DB = Path(__file__).resolve().parent / "test_maintenance.db"
if TEST_DB.exists():
    TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["DEMO_PASSWORD"] = "OfficeDemo1!"
os.environ["UPLOAD_DIR"] = str(Path(__file__).resolve().parent / "test_uploads")

from app.config import get_settings
from app.db import Base, get_db, reset_engine
from app.main import create_app
from app.seed import seed_if_empty

get_settings.cache_clear()
reset_engine(os.environ["DATABASE_URL"])


@pytest.fixture()
def db_session():
    from app import db as db_module

    Base.metadata.drop_all(bind=db_module.engine)
    Base.metadata.create_all(bind=db_module.engine)
    Session = sessionmaker(bind=db_module.engine, autoflush=False, autocommit=False)
    session = Session()
    seed_if_empty(session)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_session, tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    os.environ["UPLOAD_DIR"] = str(upload_dir)
    get_settings.cache_clear()

    application = create_app()

    def _override_db():
        try:
            yield db_session
        finally:
            pass

    application.dependency_overrides[get_db] = _override_db
    with TestClient(application) as test_client:
        yield test_client
    application.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture()
def login_as(client):
    def _login(login: str, password: str = "OfficeDemo1!") -> None:
        response = client.post(
            "/login",
            data={"login": login, "password": password, "next": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 303, response.text

    return _login
