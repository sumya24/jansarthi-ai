"""One-time migration: adds the assignment/rejection/feedback tracking columns and table, then
backfills existing complaints so they don't silently disappear from every worker's queue under
the new assignment-based (not ward-text-based) visibility rule.

Backfill logic per existing complaint:
- If it has a ward AND that ward has at least one worker (lowest id first, matching
  assignment_service.py's own ordering): assign to that worker.
    - Old status "resolved" -> stays "resolved".
    - Old status "open" -> becomes "accepted" (it was already visible/actionable to a worker
      under the old model, so skip the accept step rather than making it look brand new).
- Otherwise (no ward, or no worker in that ward): status becomes "pending", no assigned worker
  — matches how it actually behaved before (invisible to every worker).

Safe to run once; running it again is a no-op for already-migrated columns (ALTER TABLE ADD
COLUMN fails loudly on a column that already exists, which is the intended guard here).

Usage: python scripts/migrate_assignment_tracking.py
(backend must be stopped first, so the db file isn't locked)
"""

import sqlite3


def main() -> None:
    con = sqlite3.connect("jansarthi.db")
    cur = con.cursor()

    cur.execute("PRAGMA table_info(complaints)")
    existing_cols = {row[1] for row in cur.fetchall()}
    if "assigned_worker_id" in existing_cols:
        print("Already migrated (assigned_worker_id exists) — nothing to do.")
        return

    cur.execute("ALTER TABLE complaints ADD COLUMN assigned_worker_id INTEGER")
    cur.execute("ALTER TABLE complaints ADD COLUMN feedback_rating INTEGER")
    cur.execute("ALTER TABLE complaints ADD COLUMN feedback_comment TEXT")
    cur.execute("""
        CREATE TABLE complaint_rejections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            complaint_id INTEGER NOT NULL REFERENCES complaints(id),
            worker_id INTEGER NOT NULL REFERENCES users(id),
            created_at DATETIME NOT NULL,
            CONSTRAINT uq_complaint_rejection UNIQUE (complaint_id, worker_id)
        )
    """)
    con.commit()
    print("Columns and table added.")

    cur.execute("SELECT id, ward, status FROM complaints")
    complaints = cur.fetchall()

    backfilled_assigned = 0
    backfilled_pending = 0
    for complaint_id, ward, old_status in complaints:
        worker_id = None
        if ward:
            cur.execute(
                "SELECT id FROM users WHERE role = 'worker' AND ward = ? ORDER BY id ASC LIMIT 1",
                (ward,),
            )
            row = cur.fetchone()
            if row:
                worker_id = row[0]

        if worker_id is not None:
            new_status = "resolved" if old_status == "resolved" else "accepted"
            cur.execute(
                "UPDATE complaints SET assigned_worker_id = ?, status = ? WHERE id = ?",
                (worker_id, new_status, complaint_id),
            )
            backfilled_assigned += 1
        else:
            cur.execute(
                "UPDATE complaints SET assigned_worker_id = NULL, status = 'pending' WHERE id = ?",
                (complaint_id,),
            )
            backfilled_pending += 1

    con.commit()
    print(f"Backfilled {backfilled_assigned} complaints to an assigned worker (accepted/resolved).")
    print(f"Backfilled {backfilled_pending} complaints to 'pending' (no ward or no worker in it).")


if __name__ == "__main__":
    main()
