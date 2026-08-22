"""One-off cleanup: removes the leftover e2e-test worker/citizen accounts (and their complaints)
whose `ward` value is Playwright test-fixture debris (e.g. "Evidence Test Ward 1786616640264"),
not a real place -- see LOCATION_DATA_MOCK_VS_REAL_FINDINGS.md section 2 for the investigation
that found these. Also fixes ONE real, non-junk row: citizen "Priya Singh"'s complaint had a
text-encoding-corrupted em dash in an otherwise-real, correct ward string.

Every account/complaint deleted here was individually verified (not pattern-matched blindly) to
belong to one of these confirmed-test-fixture name groups: Evidence Test Worker/Citizen, Invalid
Upload Test Worker/Tester, Notif Worker/Other Worker/Test Citizen, Track Worker One/Two/Tracking
Test Citizen, "Ramesh Kadam" (8 duplicate accounts sharing one real-sounding name + a timestamped
ward -- a giveaway of automated generation despite the plausible name), Voice User, Assign Test
Pune, Kanpur Repro Test, Sentry E2E Test, Metrics Verify, Metrics Off Verify.

Explicitly NOT touched: the 6 real seeded workers/wards, and citizen "Priya Singh" (id 6, real
seed data -- only her ward string's corrupted character is fixed, nothing deleted).

Run with --dry-run first to see exactly what would change with no writes made.

Usage:
    python scripts/cleanup_junk_ward_test_data.py --dry-run
    python scripts/cleanup_junk_ward_test_data.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "janmitra.db"

JUNK_WORKER_IDS = [
    108, 142, 171, 203, 243, 269, 290, 300, 316, 324,  # Evidence Test Worker
    307, 310, 313, 318, 321, 326,  # Invalid Upload Test Worker
    114, 148, 177, 210, 249, 272, 296, 346,  # Notif Worker
    115, 149, 178, 211, 250, 273, 297, 347,  # Other Worker ("(unrelated)")
    88, 123, 153, 184, 222, 266, 277, 329,  # "Ramesh Kadam" x8 -- automated, despite the real-looking name
    104, 138, 167, 199, 235, 239, 259, 286, 337, 341,  # Track Worker One
    105, 139, 168, 200, 236, 240, 260, 287, 338, 342,  # Track Worker Two
]

JUNK_CITIZEN_IDS = [
    109, 143, 172, 204, 244, 270, 291, 301, 317, 325,  # Evidence Test Citizen
    308, 314, 319, 322, 327,  # Invalid Upload Tester
    116, 150, 179, 212, 251, 274, 298, 348,  # Notif Test Citizen
    106, 140, 169, 201, 237, 241, 261, 288, 339, 343,  # Tracking Test Citizen
    207, 208, 247, 294,  # Voice User (filed under the reused Evidence-Test-Ward string)
    353, 354, 355,  # Sentry E2E Test / Metrics Verify / Metrics Off Verify
    60,  # Assign Test Pune
    30,  # Kanpur Repro Test
]

# Real seed data -- NOT deleted. Only this complaint's corrupted ward-string character is fixed.
ENCODING_FIX = {
    "complaint_id": 89,
    "old_ward_prefix": "Ward 8",  # corrupted em dash sits between "Ward 8" and "Civil Lines"
    "new_ward": "Ward 8 — Civil Lines, Kanpur",
}


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()

    assert len(JUNK_WORKER_IDS) == len(set(JUNK_WORKER_IDS)) == 60
    assert len(JUNK_CITIZEN_IDS) == len(set(JUNK_CITIZEN_IDS)) == 42

    worker_q = ",".join("?" * len(JUNK_WORKER_IDS))
    citizen_q = ",".join("?" * len(JUNK_CITIZEN_IDS))
    citizen_ids_str = [str(c) for c in JUNK_CITIZEN_IDS]

    cur.execute(
        f"SELECT id FROM complaints WHERE citizen_id IN ({citizen_q}) "
        f"OR assigned_worker_id IN ({worker_q})",
        citizen_ids_str + JUNK_WORKER_IDS,
    )
    complaint_ids = [r[0] for r in cur.fetchall()]
    complaint_q = ",".join("?" * len(complaint_ids)) if complaint_ids else "NULL"

    plan = []
    for table, col in [
        ("complaint_evidence", "complaint_id"),
        ("complaint_updates", "complaint_id"),
        ("complaint_status_history", "complaint_id"),
        ("complaint_rejections", "complaint_id"),
        ("complaint_translations", "complaint_id"),
        ("notifications", "complaint_id"),
    ]:
        if complaint_ids:
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} IN ({complaint_q})", complaint_ids)
            plan.append((table, cur.fetchone()[0]))

    # complaint_update_translations hangs off complaint_updates, one join deeper
    if complaint_ids:
        cur.execute(
            f"SELECT COUNT(*) FROM complaint_update_translations WHERE complaint_update_id IN "
            f"(SELECT id FROM complaint_updates WHERE complaint_id IN ({complaint_q}))",
            complaint_ids,
        )
        plan.append(("complaint_update_translations", cur.fetchone()[0]))

    plan.append(("complaints", len(complaint_ids)))

    # notifications addressed to a junk user directly (not just complaint-linked)
    cur.execute(f"SELECT COUNT(*) FROM notifications WHERE recipient_id IN ({worker_q}) OR recipient_id IN ({citizen_q})",
                JUNK_WORKER_IDS + citizen_ids_str)
    plan.append(("notifications (direct to junk user)", cur.fetchone()[0]))

    cur.execute(f"SELECT COUNT(*) FROM refresh_tokens WHERE user_id IN ({worker_q}) OR user_id IN ({citizen_q})",
                JUNK_WORKER_IDS + citizen_ids_str)
    plan.append(("refresh_tokens", cur.fetchone()[0]))

    cur.execute(f"SELECT COUNT(*) FROM email_otps WHERE user_id IN ({worker_q}) OR user_id IN ({citizen_q})",
                JUNK_WORKER_IDS + citizen_ids_str)
    plan.append(("email_otps", cur.fetchone()[0]))

    plan.append(("users (workers)", len(JUNK_WORKER_IDS)))
    plan.append(("users (citizens)", len(JUNK_CITIZEN_IDS)))

    print(f"{'DRY RUN — ' if dry_run else ''}Rows that will be removed:")
    for table, n in plan:
        print(f"  {table:45s} {n}")

    cur.execute("SELECT ward FROM complaints WHERE id = ?", (ENCODING_FIX["complaint_id"],))
    current = cur.fetchone()
    print(f"\nEncoding fix — complaint #{ENCODING_FIX['complaint_id']}'s ward:")
    print(f"  before: {current[0]!r}")
    print(f"  after:  {ENCODING_FIX['new_ward']!r}")

    if dry_run:
        con.close()
        return

    if complaint_ids:
        cur.execute(f"DELETE FROM complaint_update_translations WHERE complaint_update_id IN "
                    f"(SELECT id FROM complaint_updates WHERE complaint_id IN ({complaint_q}))", complaint_ids)
        cur.execute(f"DELETE FROM complaint_evidence WHERE complaint_id IN ({complaint_q})", complaint_ids)
        cur.execute(f"DELETE FROM complaint_updates WHERE complaint_id IN ({complaint_q})", complaint_ids)
        cur.execute(f"DELETE FROM complaint_status_history WHERE complaint_id IN ({complaint_q})", complaint_ids)
        cur.execute(f"DELETE FROM complaint_rejections WHERE complaint_id IN ({complaint_q})", complaint_ids)
        cur.execute(f"DELETE FROM complaint_translations WHERE complaint_id IN ({complaint_q})", complaint_ids)
        cur.execute(f"DELETE FROM notifications WHERE complaint_id IN ({complaint_q})", complaint_ids)
        cur.execute(f"DELETE FROM complaints WHERE id IN ({complaint_q})", complaint_ids)

    cur.execute(f"DELETE FROM notifications WHERE recipient_id IN ({worker_q}) OR recipient_id IN ({citizen_q})",
                JUNK_WORKER_IDS + citizen_ids_str)
    cur.execute(f"DELETE FROM refresh_tokens WHERE user_id IN ({worker_q}) OR user_id IN ({citizen_q})",
                JUNK_WORKER_IDS + citizen_ids_str)
    cur.execute(f"DELETE FROM email_otps WHERE user_id IN ({worker_q}) OR user_id IN ({citizen_q})",
                JUNK_WORKER_IDS + citizen_ids_str)
    cur.execute(f"DELETE FROM users WHERE id IN ({worker_q})", JUNK_WORKER_IDS)
    cur.execute(f"DELETE FROM users WHERE id IN ({citizen_q})", citizen_ids_str)

    cur.execute("UPDATE complaints SET ward = ? WHERE id = ?",
                (ENCODING_FIX["new_ward"], ENCODING_FIX["complaint_id"]))

    con.commit()
    print("\nCommitted.")
    con.close()


if __name__ == "__main__":
    main()
