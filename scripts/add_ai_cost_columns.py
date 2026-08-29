"""Adds `ai_request_logs.ai_cost_inr`/`ai_model_name`/`ai_total_tokens`, needed for the Admin AI
Monitoring page's new per-request cost column (see models.py's AiRequestLog docstring). This repo
has no Alembic/migration tooling -- `Base.metadata.create_all()` only creates missing tables,
never adds columns to existing ones, so this one-off script does the `ALTER TABLE` directly.

Idempotent: checks each column doesn't already exist before altering, so re-running is a safe no-op.

Usage:
    python scripts/add_ai_cost_columns.py            # local jansarthi.db
    docker exec janmitra-ai-backend-1 python3 scripts/add_ai_cost_columns.py   # production
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "jansarthi.db"

_NEW_COLUMNS = {
    "ai_cost_inr": "FLOAT",
    "ai_model_name": "VARCHAR(64)",
    "ai_total_tokens": "INTEGER",
}


def main() -> None:
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    cur.execute("PRAGMA table_info(ai_request_logs)")
    existing_columns = {row[1] for row in cur.fetchall()}

    for column, sql_type in _NEW_COLUMNS.items():
        if column in existing_columns:
            print(f"{column} already exists on ai_request_logs -- skipping.")
            continue
        cur.execute(f"ALTER TABLE ai_request_logs ADD COLUMN {column} {sql_type}")
        print(f"Added ai_request_logs.{column}.")

    con.commit()
    con.close()


if __name__ == "__main__":
    main()
