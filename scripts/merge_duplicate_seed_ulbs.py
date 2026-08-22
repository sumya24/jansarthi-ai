"""One-off fix: 6 of the ULBs imported by import_lgd_districts_and_ulbs.py are duplicates of the
6 original hand-seeded ULBs -- LGD's own dataset lists each of these 6 real cities under a plain
name ("Pune", "Kanpur", "Bhubaneswar", "Ahmadabad", "Kolkata", "Bbmp") distinct from this
project's fuller/differently-spelled seed names ("Pune Municipal Corporation",
"Ahmedabad Municipal Corporation", etc.) -- confirmed by both sharing the exact same LGD code
(backfilled onto the 6 seed rows specifically so the ward import could find them). 2 of these 6
(Ahmadabad, Bbmp) were actually caught and deleted once already during the original district/ULB
import, but a later idempotent re-run (fixing the Jammu & Kashmir casing bug) silently
re-inserted them, since the manual deletion wasn't something the idempotent script's own
already-exists check could have known about.

This script re-points any wards already attached to the 6 duplicate ULB rows onto the correct
original seed ULB, then deletes the now-empty duplicate rows -- run AFTER
import_lgd_wards.py, whose first run inserted ~90,000 real wards, but attached each of these 6
cities' real wards to the wrong (duplicate) ULB row instead of the actual seed ULB workers are
assigned to.

Idempotent: re-running when the 6 duplicates no longer exist is a silent no-op.

Usage:
    python scripts/merge_duplicate_seed_ulbs.py --dry-run
    python scripts/merge_duplicate_seed_ulbs.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "janmitra.db"

# (duplicate ULB name, correct/seed ULB name) -- both share the same real LGD code.
DUPLICATE_PAIRS = [
    ("Pune", "Pune Municipal Corporation"),
    ("Kanpur", "Kanpur Municipal Corporation"),
    ("Bhubaneswar", "Bhubaneswar Municipal Corporation"),
    ("Ahmadabad", "Ahmedabad Municipal Corporation"),
    ("Kolkata", "Kolkata Municipal Corporation"),
    ("Bbmp", "Bruhat Bengaluru Mahanagara Palike (BBMP)"),
]


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()

    for dup_name, seed_name in DUPLICATE_PAIRS:
        cur.execute("SELECT id FROM ulbs WHERE name = ?", (dup_name,))
        dup_row = cur.fetchone()
        cur.execute("SELECT id FROM ulbs WHERE name = ?", (seed_name,))
        seed_row = cur.fetchone()
        if dup_row is None:
            print(f"{dup_name!r}: no duplicate found, nothing to do")
            continue
        if seed_row is None:
            print(f"WARNING: seed ULB {seed_name!r} not found -- skipping {dup_name!r}")
            continue
        dup_id, seed_id = dup_row[0], seed_row[0]

        cur.execute("SELECT id, name FROM wards WHERE ulb_id = ?", (dup_id,))
        dup_wards = cur.fetchall()
        cur.execute("SELECT name FROM wards WHERE ulb_id = ?", (seed_id,))
        seed_ward_names = {n for (n,) in cur.fetchall()}

        movable = [(wid, wname) for wid, wname in dup_wards if wname not in seed_ward_names]
        colliding = [(wid, wname) for wid, wname in dup_wards if wname in seed_ward_names]

        print(f"{dup_name!r} (id={dup_id}) -> {seed_name!r} (id={seed_id}): "
              f"{len(dup_wards)} real wards found, {len(movable)} will move, "
              f"{len(colliding)} name-collide with an existing ward (left on the duplicate, not moved)")

        if dry_run:
            continue

        for wid, _ in movable:
            cur.execute("UPDATE wards SET ulb_id = ? WHERE id = ?", (seed_id, wid))

        if not colliding:
            cur.execute("DELETE FROM ulbs WHERE id = ?", (dup_id,))
            print(f"  deleted duplicate ULB {dup_name!r}")
        else:
            print(f"  NOT deleting {dup_name!r} -- {len(colliding)} ward(s) still attached (name collision)")

    if dry_run:
        print("\n--dry-run: no writes made.")
        con.close()
        return

    con.commit()
    con.close()
    print("\nCommitted.")


if __name__ == "__main__":
    main()
