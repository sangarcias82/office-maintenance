from collections.abc import Generator

import logging
from pathlib import Path

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings
from app.timeutil import utcnow


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(settings.database_url, connect_args=connect_args)
    if settings.database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_fk(dbapi_connection, _connection_record):  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


logger = logging.getLogger(__name__)


def outdated_tables(bind) -> list[str]:
    """Existing tables that lack columns the models need (e.g. a DB from an older version).

    ``create_all`` creates missing tables but never alters existing ones, so an
    older database would otherwise fail at the first query.
    """
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())
    problems = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue
        have = {c["name"] for c in inspector.get_columns(table.name)}
        missing = sorted(set(table.columns.keys()) - have)
        if missing:
            problems.append(f"{table.name} (missing {', '.join(missing)})")
    return problems


def _set_aside_outdated_database(problems: list[str]) -> None:
    """Move an outdated SQLite file aside (kept as a backup) so a fresh one is created.

    Only for file-based SQLite (the demo setup). Anything else needs a real
    migration, so startup stops with a clear message instead of guessing.
    """
    url = engine.url
    database = url.database or ""
    if url.get_backend_name() != "sqlite" or database in ("", ":memory:"):
        raise RuntimeError(
            "Database schema is outdated: " + "; ".join(problems) + ". Migrate it or point DATABASE_URL at a new database."
        )
    path = Path(database)
    backup = path.with_name(f"{path.name}.outdated-{utcnow():%Y%m%d%H%M%S}")
    engine.dispose()
    path.rename(backup)
    logger.warning(
        "Database schema is outdated (%s). Moved %s to %s and created a fresh demo database.",
        "; ".join(problems),
        path,
        backup,
    )


def init_db() -> None:
    from app import models  # noqa: F401
    from app.seed import seed_if_empty

    problems = outdated_tables(engine)
    if problems:
        _set_aside_outdated_database(problems)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_if_empty(db)
    finally:
        db.close()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def reset_engine(database_url: str) -> None:
    """Rebuild the global engine/session for tests that need an isolated DB."""
    global engine, SessionLocal
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_fk(dbapi_connection, _connection_record):  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
