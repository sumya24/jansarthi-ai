"""SQLAlchemy engine, session, and declarative base setup."""

import logging
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import settings

logger = logging.getLogger(__name__)

connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Base class for all ORM models."""


def init_db() -> None:
    """Create all database tables if they do not already exist."""
    import backend.models  # noqa: F401 ensures models are registered on Base

    Base.metadata.create_all(bind=engine)
    _ensure_columns_exist()
    logger.info("Database initialized at %s", settings.DATABASE_URL)


# `Base.metadata.create_all()` above only creates missing TABLES -- it never adds a column to a
# table that already exists (this project has no Alembic/migration tooling, a deliberate choice
# for its scale -- see requirements.txt). A field added to a model in backend/models.py therefore
# silently does nothing for anyone's EXISTING database file (local dev's janmitra.db, production's
# jansarthi.db) until that file is manually ALTER TABLE'd or recreated -- hit once before with
# `complaints.ward` (fixed by hand at the time). Rather than repeat that one-off manual fix, this
# is a small, generic, idempotent guard: for each (table, column, DDL) entry below, check via
# SQLite's own PRAGMA whether the column already exists, and ALTER TABLE it in if not. Runs once
# per process start, here in init_db() (so it applies before any request can touch the DB), a
# no-op on every startup after the first time it actually adds a column. SQLite-only (guarded on
# settings.DATABASE_URL, same check connect_args above already uses) -- this project's only real
# deployment target; a future Postgres migration would need Alembic properly, not this.
_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    # (table, column, full ALTER TABLE ADD COLUMN clause)
    ("ai_request_logs", "needs_review", "ALTER TABLE ai_request_logs ADD COLUMN needs_review BOOLEAN NOT NULL DEFAULT 0"),
]


def _ensure_columns_exist() -> None:
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    with engine.connect() as conn:
        for table, column, ddl in _COLUMN_MIGRATIONS:
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.exec_driver_sql(ddl)
                conn.commit()
                logger.info("Migrated: added %s.%s (existing rows default to FALSE)", table, column)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session and closes it after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
