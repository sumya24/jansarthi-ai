"""One-time migration: adds the `reason` column to `complaint_rejections`.

Every other new table this phase needs (`complaint_status_history`, `complaint_updates`,
`notifications`) is created automatically by `backend.database.init_db()`'s
`Base.metadata.create_all()` the next time the backend starts -- `create_all()` only creates
tables that don't exist yet, so those three (genuinely new, no existing rows to preserve) need no
script at all. `complaint_rejections` is the one exception: it already has rows, and
`create_all()` never alters an existing table's columns, so this script adds the column by hand,
matching the existing convention (see scripts/migrate_assignment_tracking.py).

Safe to run once; running it again is a no-op (checked via PRAGMA table_info before altering).

Usage: python scripts/migrate_worker_workflow.py
(backend must be stopped first, so the db file isn't locked)
"""

import sqlite3


def main() -> None:
    con = sqlite3.connect("jansarthi.db")
    cur = con.cursor()

    cur.execute("PRAGMA table_info(complaint_rejections)")
    existing_cols = {row[1] for row in cur.fetchall()}
    if "reason" in existing_cols:
        print("Already migrated (complaint_rejections.reason exists) — nothing to do.")
        return

    cur.execute("ALTER TABLE complaint_rejections ADD COLUMN reason TEXT")
    con.commit()
    print("Added complaint_rejections.reason (existing rows left NULL -- see model docstring).")


if __name__ == "__main__":
    main()
