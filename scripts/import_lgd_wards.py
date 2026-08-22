"""Imports REAL wards for every ULB already in the database (the 4,489 imported by
import_lgd_districts_and_ulbs.py, plus the 6 original seeded ULBs, whose real LGD codes were
separately looked up and backfilled onto their existing rows so this script can find them too --
see DATA_COVERAGE_TRACKER.md's ward-import entry for the exact 6 codes and how they were found).

This is the deliberately-deferred "next step" flagged in the district/ULB import: a citizen
files a complaint by picking their WARD, not their district or even their city -- that's the
level this app actually operates on (assignment_service.py routes by ward), so this closes the
real remaining gap for citizen-facing usefulness, not just the district/ULB scaffolding.

Source: same as import_lgd_districts_and_ulbs.py -- github.com/ramSeraph/opendata's
urban_local_body_wards dataset (current as of 30 Apr 2026), Government Open Data License - India.
Labeled source_type="OFFICIAL_LGD_MIRROR_DATASET", same as the district/ULB rows.

Deliberately does NOT touch localities -- the ward file has no locality-level breakdown; that
stays a separate, even-deeper future step, same reasoning as sub-districts/zones.

Idempotent: checks for an existing (ulb_id, name) row before inserting, so re-running adds
nothing new. Does not remove or alter the 6 original UNVERIFIED_APP_SEED_DATA example wards --
if a real ward's name happens to collide with one of those, the existing row wins and the new one
is skipped (not overwritten), consistent with every other import in this project.

Usage:
    python scripts/import_lgd_wards.py --dry-run
    python scripts/import_lgd_wards.py
"""

from __future__ import annotations

import csv
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "janmitra.db"
WARDS_CSV = Path(__file__).resolve().parent.parent / "data" / "lgd_import" / "urban_local_body_wards.30Apr2026.csv"

SOURCE_NAME = (
    "India Local Government Directory (LGD) -- community mirror, "
    "github.com/ramSeraph/opendata (urban_local_body_wards, 30 Apr 2026), under the "
    "Government Open Data License - India. Not a live fetch from lgdirectory.gov.in itself."
)
SOURCE_TYPE = "OFFICIAL_LGD_MIRROR_DATASET"
SOURCE_URL = "https://github.com/ramSeraph/opendata/releases/tag/lgd-latest"


def norm(s: str) -> str:
    return " ".join(s.strip().split()).lower()


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    now = datetime.now(timezone.utc).isoformat()

    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()

    cur.execute("SELECT id, code FROM ulbs WHERE code IS NOT NULL")
    ulb_by_code = {code: uid for uid, code in cur.fetchall()}

    cur.execute("SELECT ulb_id, name FROM wards")
    existing_wards = {(ulb_id, norm(name)) for ulb_id, name in cur.fetchall()}

    with open(WARDS_CSV, encoding="utf-8-sig") as f:
        ward_rows = list(csv.DictReader(f))

    matched = 0
    no_ulb = 0
    to_insert = []
    seen_this_run: set[tuple[int, str]] = set()
    for r in ward_rows:
        lgd_code = r["Local Body Code"].strip()
        ulb_id = ulb_by_code.get(lgd_code)
        if ulb_id is None:
            no_ulb += 1
            continue
        matched += 1
        wname = r["Ward Name"].strip()
        wnum = r["Ward Number"].strip()
        wcode = r["Ward Code"].strip()
        key = (ulb_id, norm(wname))
        if key in existing_wards or key in seen_this_run:
            continue
        seen_this_run.add(key)
        to_insert.append((ulb_id, wname, wnum, wcode))

    print(f"Ward rows in source file: {len(ward_rows)}")
    print(f"Matched to a ULB already in our database: {matched}")
    print(f"No matching ULB (not one of our imported/seeded ULBs): {no_ulb}")
    print(f"New wards to insert (after dedup against existing + within-file duplicates): {len(to_insert)}")

    if dry_run:
        con.close()
        print("\n--dry-run: no writes made.")
        return

    for ulb_id, wname, wnum, wcode in to_insert:
        cur.execute(
            "INSERT INTO wards (ulb_id, zone_id, name, ward_number, code, source_name, source_type, source_url, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ulb_id, None, wname, wnum, wcode, SOURCE_NAME, SOURCE_TYPE, SOURCE_URL, now, now),
        )

    con.commit()
    print(f"\nCommitted. Wards inserted: {len(to_insert)}.")
    con.close()


if __name__ == "__main__":
    main()
