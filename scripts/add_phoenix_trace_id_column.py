"""Adds `ai_request_logs.phoenix_trace_id`, needed for the Arize Phoenix tracing addition (see
PHOENIX_TRACING_PLAN.md). This repo has no Alembic/migration tooling -- `Base.metadata.create_all()`
only creates missing tables, never adds columns to existing ones, so this one-off script does the
`ALTER TABLE` directly.

Idempotent: checks the column doesn't already exist before altering, so re-running is a safe no-op.

Usage:
    python scripts/add_phoenix_trace_id_column.py            # local jansarthi.db
    docker exec janmitra-ai-backend-1 python3 scripts/add_phoenix_trace_id_column.py   # production
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "jansarthi.db"


def main() -> None:
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    cur.execute("PRAGMA table_info(ai_request_logs)")
    existing_columns = {row[1] for row in cur.fetchall()}
    if "phoenix_trace_id" in existing_columns:
        print("phoenix_trace_id already exists on ai_request_logs -- nothing to do.")
        con.close()
        return

    cur.execute("ALTER TABLE ai_request_logs ADD COLUMN phoenix_trace_id VARCHAR(64)")
    con.commit()
    print("Added ai_request_logs.phoenix_trace_id.")
    con.close()


if __name__ == "__main__":
    main()
