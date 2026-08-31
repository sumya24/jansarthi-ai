"""One-time migration: adds User.super_admin and backfills every EXISTING admin account to
super_admin=True.

LIVE-REPORTED GAP this closes: there was no route anywhere in the app that let an admin create
another admin account (only workers) -- production had no way to onboard a second admin without
hand-editing the database. Fixed by adding a super-admin-only POST /admin/admins, gated on this
new column (see backend/deps.py's require_super_admin and backend/models.py's own docstring on
this field for why it's a flag, not a whole second role tier).

Backfilling every pre-existing "admin" row to super_admin=True (not False) is deliberate: this app
previously had a single flat "admin" role where every admin could already do everything an admin
can do. Defaulting existing admins to False would silently take away an ability they already had
the moment this migration ran -- a real regression, not a security tightening. Only NEW admin
accounts created after this ships default to super_admin=False (see CreateAdminRequest), so the
"not every admin can create more admins" restriction only ever applies going forward.

Safe to run once; running it again is a no-op (guarded on the column already existing, matching
scripts/migrate_complaint_category.py's own convention).

Usage: python scripts/migrate_super_admin.py
(backend must be stopped first, so the db file isn't locked)
"""

import sqlite3


def main() -> None:
    con = sqlite3.connect("jansarthi.db")
    cur = con.cursor()

    cur.execute("PRAGMA table_info(users)")
    existing_cols = {row[1] for row in cur.fetchall()}
    if "super_admin" in existing_cols:
        print("Already migrated (super_admin exists) — nothing to do.")
        return

    cur.execute("ALTER TABLE users ADD COLUMN super_admin BOOLEAN NOT NULL DEFAULT 0")
    con.commit()
    print("Column added.")

    cur.execute("UPDATE users SET super_admin = 1 WHERE role = 'admin'")
    con.commit()
    print(f"Backfilled {cur.rowcount} existing admin account(s) to super_admin=1.")


if __name__ == "__main__":
    main()
