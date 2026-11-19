"""Company (tenant) entity, demo seed, and create_all + seed_if_empty wiring."""

from __future__ import annotations

import re
from datetime import datetime

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import db as db_module
from app.db import Base
from app.models import AppUser, Company, Role
from app.seed import DEMO_COMPANIES, DEMO_USERS, seed_if_empty

EXPECTED = {
    "campus-ops": "Campus Ops",
    "harbor-yard": "Harbor Yard",
}

# Tenant presentation is resolved from files by slug, never from brand columns.
BRAND_COLUMN = re.compile(r"colou?r|logo|brand|css|font|accent|palette|hex|favicon|banner")


def _companies(session) -> list[Company]:
    return list(session.scalars(select(Company).order_by(Company.slug)))


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


# --- seed ---------------------------------------------------------------------


def test_exactly_two_demo_companies_are_seeded(db_session):
    companies = _companies(db_session)
    assert {c.slug: c.display_name for c in companies} == EXPECTED
    for company in companies:
        assert company.active is True
        assert company.description
        assert isinstance(company.created_at, datetime)
        assert isinstance(company.updated_at, datetime)


def test_demo_companies_describe_their_kind():
    by_slug = {slug: description.lower() for slug, _, description in DEMO_COMPANIES}
    assert "office" in by_slug["campus-ops"]
    assert "yard" in by_slug["harbor-yard"] and "logistics" in by_slug["harbor-yard"]


def test_seed_is_idempotent(db_session):
    roles, users = _count(db_session, Role), _count(db_session, AppUser)
    seed_if_empty(db_session)
    seed_if_empty(db_session)
    assert _count(db_session, Company) == 2
    assert (_count(db_session, Role), _count(db_session, AppUser)) == (roles, users)


OLD_APP_USER = """
CREATE TABLE app_user (
    id INTEGER NOT NULL PRIMARY KEY,
    employee_number VARCHAR(30) UNIQUE,
    first_name VARCHAR(120) NOT NULL,
    last_name VARCHAR(120) NOT NULL,
    email VARCHAR(180) NOT NULL UNIQUE,
    phone VARCHAR(40),
    role_id INTEGER NOT NULL,
    active BOOLEAN NOT NULL,
    created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL
)
"""


def _use_engine(monkeypatch, engine):
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", sessionmaker(bind=engine))


def test_outdated_sqlite_file_is_set_aside_and_recreated(tmp_path, monkeypatch, caplog):
    """A DB from an older version (app_user without company/account) must not crash startup."""
    path = tmp_path / "maintenance.db"
    old = create_engine(f"sqlite:///{path}")
    with old.begin() as conn:
        conn.execute(text(OLD_APP_USER))
        conn.execute(
            text("INSERT INTO app_user (first_name, last_name, email, role_id, active) VALUES ('Old','Row','o@x',1,1)")
        )
    old.dispose()
    assert db_module.outdated_tables(create_engine(f"sqlite:///{path}")) == [
        "app_user (missing account_id, company_id)"
    ]

    engine = create_engine(f"sqlite:///{path}")
    _use_engine(monkeypatch, engine)
    with caplog.at_level("WARNING"):
        db_module.init_db()

    backups = list(tmp_path.glob("maintenance.db.outdated-*"))
    assert len(backups) == 1  # old data kept, not deleted
    assert "schema is outdated" in caplog.text
    with sessionmaker(bind=db_module.engine)() as session:
        assert {c.slug for c in _companies(session)} == set(EXPECTED)
        assert _count(session, AppUser) == len(DEMO_USERS)
    assert db_module.outdated_tables(db_module.engine) == []
    db_module.engine.dispose()


def test_outdated_non_file_database_stops_with_clear_error(monkeypatch):
    engine = create_engine("sqlite://")  # in-memory: nothing to set aside
    with engine.begin() as conn:
        conn.execute(text(OLD_APP_USER))
    _use_engine(monkeypatch, engine)
    with pytest.raises(RuntimeError, match="schema is outdated"):
        db_module.init_db()


def test_init_db_creates_companies_on_fresh_database(tmp_path, monkeypatch):
    """Exercise the real startup path: create_all + seed_if_empty via db.init_db()."""
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    _use_engine(monkeypatch, engine)

    db_module.init_db()
    db_module.init_db()  # second startup is a no-op

    with sessionmaker(bind=engine)() as session:
        assert {c.slug: c.display_name for c in _companies(session)} == EXPECTED
        assert _count(session, Role) == 3
        assert _count(session, AppUser) == len(DEMO_USERS)
    engine.dispose()


def test_get_db_yields_and_closes_a_session(db_session):
    gen = db_module.get_db()
    session = next(gen)
    assert _count(session, Company) == 2
    with pytest.raises(StopIteration):
        next(gen)


# --- entity rules -------------------------------------------------------------


def test_slug_is_unique(db_session):
    db_session.add(Company(slug="campus-ops", display_name="Duplicate"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize(
    "slug",
    ["", "Campus-Ops", "campus_ops", "campus ops", "-campus", "campus-", "campus--ops", "ops/..", "a" * 61],
)
def test_slug_format_is_enforced(slug):
    with pytest.raises(ValueError):
        Company(slug=slug, display_name="X")


def test_slug_is_stable(db_session):
    company = db_session.scalar(select(Company).where(Company.slug == "harbor-yard"))
    company.slug = "harbor-yard"  # re-assigning the same value is fine
    with pytest.raises(ValueError):
        company.slug = "harbor-port"
    db_session.rollback()


def test_display_name_required_and_trimmed():
    with pytest.raises(ValueError):
        Company(slug="acme", display_name="   ")
    assert Company(slug="acme", display_name="  Acme  ").display_name == "Acme"


def test_active_flag_defaults_true_and_can_be_toggled(db_session):
    company = Company(slug="north-depot", display_name="North Depot")
    db_session.add(company)
    db_session.commit()
    assert company.active is True
    company.active = False
    db_session.commit()
    db_session.expire_all()
    assert db_session.scalar(select(Company.active).where(Company.slug == "north-depot")) is False


# --- schema guard -------------------------------------------------------------


def test_company_has_only_generic_columns():
    assert set(Company.__table__.columns.keys()) == {
        "id",
        "slug",
        "display_name",
        "description",
        "active",
        "theme_tokens",  # generic design tokens (app/theme_tokens.py), not brand columns
        "created_at",
        "updated_at",
    }


def test_no_brand_specific_sql_columns_anywhere():
    offenders = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if BRAND_COLUMN.search(column.name)
    ]
    assert not offenders
