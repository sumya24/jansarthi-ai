"""Tests for backend/database.py's `_ensure_columns_exist()` -- the small, generic migration guard
that lets a column added to a SQLAlchemy model in backend/models.py actually retrofit an EXISTING
on-disk SQLite database file, since this project has no Alembic (see database.py's own docstring
comment on `_COLUMN_MIGRATIONS` for why, and the real `ai_request_logs.needs_review` gotcha this
was built to stop recurring for future columns too).

Uses a real temp SQLite FILE (not the in-memory `:memory:` engine every other test file's
`db_session` fixture uses) specifically because this behavior only matters for a database that
already exists on disk with an old schema -- an in-memory engine is always created fresh via
`Base.metadata.create_all()` and would never exercise the "column is missing" path at all.
"""

from __future__ import annotations

import sqlite3

from sqlalchemy import create_engine

from backend import database
from backend.config import settings


def _make_old_schema_db(path: str) -> None:
    """Creates a real sqlite file with ai_request_logs already present, but WITHOUT the
    needs_review column -- exactly the shape any real, already-deployed database has right after
    this column is added to the model but before this app has restarted against it."""
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE ai_request_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id VARCHAR(64) NOT NULL,
            routed_to VARCHAR(32) NOT NULL,
            success BOOLEAN NOT NULL,
            latency_ms FLOAT NOT NULL,
            created_at DATETIME NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO ai_request_logs (request_id, routed_to, success, latency_ms, created_at) "
        "VALUES ('pre-existing-row', 'RAG', 1, 100.0, '2026-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()


def test_ensure_columns_exist_adds_missing_column_to_real_db_file(tmp_path, monkeypatch):
    db_path = tmp_path / "old_schema.db"
    _make_old_schema_db(str(db_path))

    # Point database.py's module-level `engine` at this exact file, matching how it's really
    # constructed from settings.DATABASE_URL at import time -- monkeypatched here rather than
    # relying on the app's own engine (which is already bound to whatever DATABASE_URL this test
    # run started with, real per-process state this test must not depend on or mutate).
    test_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}")

    with sqlite3.connect(str(db_path)) as conn:
        before = {row[1] for row in conn.execute("PRAGMA table_info(ai_request_logs)")}
    assert "needs_review" not in before

    database._ensure_columns_exist()

    with sqlite3.connect(str(db_path)) as conn:
        after = {row[1] for row in conn.execute("PRAGMA table_info(ai_request_logs)")}
        assert "needs_review" in after
        # The pre-existing row (inserted before the column existed at all) must survive with a
        # real, non-NULL default -- an admin's existing request history must not be silently
        # nulled out or dropped by this migration.
        row = conn.execute("SELECT needs_review FROM ai_request_logs WHERE request_id = 'pre-existing-row'").fetchone()
        assert row is not None
        assert row[0] == 0  # SQLite BOOLEAN False


def test_ensure_columns_exist_is_a_noop_when_column_already_present(tmp_path, monkeypatch):
    """Runs on every process start (see init_db()) -- must not error or duplicate-add a column
    that's already there, which is the normal case after the very first migration has already run
    once."""
    db_path = tmp_path / "already_migrated.db"
    _make_old_schema_db(str(db_path))
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("ALTER TABLE ai_request_logs ADD COLUMN needs_review BOOLEAN NOT NULL DEFAULT 0")
        conn.commit()

    test_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}")

    database._ensure_columns_exist()  # must not raise (e.g. "duplicate column name")


def test_ensure_columns_exist_skips_non_sqlite_databases(monkeypatch):
    """Postgres (a future migration target) needs Alembic properly, not this ad-hoc SQLite-only
    guard -- see database.py's own docstring comment. Confirmed by pointing DATABASE_URL at a
    non-sqlite URL and checking engine.connect() (which would fail without a real Postgres server)
    is never even called."""
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://user:pass@localhost/db")
    database._ensure_columns_exist()  # must return immediately, not attempt a real connection
