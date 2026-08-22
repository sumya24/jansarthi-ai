"""Imports REAL districts and ULBs (Urban Local Bodies) for the 25 states/UTs that already have
verified RAG civic-service coverage -- scoped deliberately, not a blind national import (see
DATA_COVERAGE_TRACKER.md / RAG_REAL_VS_SYNTHETIC_RESEARCH_PREP.md for which 25).

Source: a community-maintained, government-license-compliant mirror of India's official Local
Government Directory (LGD, lgdirectory.gov.in) -- github.com/ramSeraph/opendata (districts/states,
current as of Jun 2025) and github.com/planemad/india-local-government-directory
(municipal-directory.csv, the ULB-to-district mapping, retrieved 11 Mar 2022 per that repo's own
README). NOT a live fetch from the government site itself -- lgdirectory.gov.in's own bulk
download is session/CSRF-protected and its API requires a registered key this project doesn't
have. Labeled source_type="OFFICIAL_LGD_MIRROR_DATASET" throughout, distinct from this project's
other source_type tiers, so nothing downstream mistakes this for a live-government-site fetch.

Deliberately does NOT set `ULB.type` (Municipal Corporation / Municipality / etc.) -- the LGD
"Localbody Type Code" field has no confirmed public legend (checked; not documented anywhere
locatable), so guessing would risk a wrong label. Left null rather than fabricated.

Deliberately does NOT touch Wards or Localities -- that's a separate, much larger next step
(~90,000 rows for these same 25 states), scoped out of this pass on purpose.

Idempotent: every insert checks for an existing (state_id, name) / (district_id, name) row first,
so re-running adds nothing new on a second run. Does not touch the 6 existing
WELL_ESTABLISHED_PUBLIC_GEOGRAPHY-sourced districts/ULBs from scripts/seed_location_master_data.py
-- if an LGD row's name matches an existing one exactly, it's skipped, not overwritten.

Usage:
    python scripts/import_lgd_districts_and_ulbs.py --dry-run
    python scripts/import_lgd_districts_and_ulbs.py
"""

from __future__ import annotations

import csv
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "janmitra.db"
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "lgd_import"

DISTRICTS_CSV = DATA_DIR / "districts.30Jun2025.csv"
ULBS_CSV = DATA_DIR / "urban_local_bodies.30Apr2026.csv"
MUNICIPAL_MAPPING_CSV = DATA_DIR / "municipal-directory.csv"

TARGET_STATES = [
    "Andhra Pradesh", "Assam", "Bihar", "Chandigarh", "Chhattisgarh", "Delhi", "Goa", "Gujarat",
    "Haryana", "Himachal Pradesh", "Jammu and Kashmir", "Jharkhand", "Karnataka", "Kerala",
    "Madhya Pradesh", "Maharashtra", "Odisha", "Puducherry", "Punjab", "Rajasthan", "Tamil Nadu",
    "Telangana", "Uttar Pradesh", "Uttarakhand", "West Bengal",
]

SOURCE_NAME = (
    "India Local Government Directory (LGD) -- community mirror, github.com/ramSeraph/opendata "
    "(districts, Jun 2025) and github.com/planemad/india-local-government-directory "
    "(municipal-directory.csv ULB-to-district mapping, retrieved 11 Mar 2022), both under the "
    "Government Open Data License - India. Not a live fetch from lgdirectory.gov.in itself "
    "(session/CSRF-protected bulk download; API requires a registered key)."
)
SOURCE_TYPE = "OFFICIAL_LGD_MIRROR_DATASET"
SOURCE_URL = "https://github.com/ramSeraph/opendata/releases/tag/lgd-archive"


def norm(s: str) -> str:
    return " ".join(s.strip().split()).lower()


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    now = datetime.now(timezone.utc).isoformat()

    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()

    cur.execute("SELECT id, name FROM states")
    state_by_name = {norm(name): sid for sid, name in cur.fetchall()}

    target_state_ids = {}
    for s in TARGET_STATES:
        sid = state_by_name.get(norm(s))
        if sid is None:
            print(f"WARNING: target state {s!r} not found in states table, skipping")
            continue
        target_state_ids[norm(s)] = sid

    target_norm_set = {norm(s) for s in TARGET_STATES}

    with open(DISTRICTS_CSV, encoding="utf-8-sig") as f:
        district_rows = list(csv.DictReader(f))
    district_rows = [r for r in district_rows if norm(r["State Name (In English)"]) in target_norm_set]

    cur.execute("SELECT id, state_id, name FROM districts")
    existing_districts = {(state_id, norm(name)): did for did, state_id, name in cur.fetchall()}

    districts_to_insert = []
    district_lookup: dict[tuple[str, str], int | None] = {}  # (lgd_district_code) -> planned id (None until inserted)
    code_to_key: dict[str, tuple[int, str]] = {}
    for r in district_rows:
        state_name = r["State Name (In English)"].strip()
        sid = target_state_ids.get(norm(state_name))
        if sid is None:
            continue
        dname = r["District Name (In English)"].strip()
        key = (sid, norm(dname))
        code_to_key[r["District Code"].strip()] = key
        if key in existing_districts:
            continue
        districts_to_insert.append((sid, dname, r["District Code"].strip()))

    print(f"Districts matched to our 25 states: {len(district_rows)}")
    print(f"Districts already existing (skipped): {len(district_rows) - len(districts_to_insert)}")
    print(f"Districts to insert: {len(districts_to_insert)}")

    # ULB -> district code mapping
    ulb_district_code: dict[str, str] = {}
    with open(MUNICIPAL_MAPPING_CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = r["Localbody Code"].strip()
            dcode = r.get("District Code", "").strip()
            if code and dcode and code not in ulb_district_code:
                ulb_district_code[code] = dcode

    with open(ULBS_CSV, encoding="utf-8-sig") as f:
        ulb_rows = list(csv.DictReader(f))
    ulb_rows = [r for r in ulb_rows if norm(r["State Name"]) in target_norm_set]

    cur.execute(
        "SELECT u.id, u.district_id, u.name FROM ulbs u"
    )
    existing_ulbs = {(district_id, norm(name)): uid for uid, district_id, name in cur.fetchall()}

    ulbs_to_insert = []
    ulbs_no_district_mapping = 0
    ulbs_district_not_in_our_set = 0
    for r in ulb_rows:
        lgd_code = r["Local Body Code"].strip()
        dcode = ulb_district_code.get(lgd_code)
        if dcode is None:
            ulbs_no_district_mapping += 1
            continue
        # dcode here is the OLD (2022) district code; match it back to a district by
        # cross-referencing through district name, since district codes are stable identifiers
        # in LGD (rarely reissued) -- find the (state,name) key that had this code in the fresh
        # Jun-2025 district file.
        key = code_to_key.get(dcode)
        if key is None:
            ulbs_district_not_in_our_set += 1
            continue
        uname = r["Local Body Name (In English)"].strip()
        ulbs_to_insert.append((key, uname, lgd_code))

    print(f"ULBs matched to our 25 states: {len(ulb_rows)}")
    print(f"ULBs with no district mapping available (skipped): {ulbs_no_district_mapping}")
    print(f"ULBs whose mapped district code isn't in our fresh district list (skipped): {ulbs_district_not_in_our_set}")

    if dry_run:
        con.close()
        print("\n--dry-run: no writes made.")
        return

    # Insert districts first, tracking new ids
    for sid, dname, dcode in districts_to_insert:
        cur.execute(
            "INSERT INTO districts (state_id, name, code, source_name, source_type, source_url, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, dname, dcode, SOURCE_NAME, SOURCE_TYPE, SOURCE_URL, now, now),
        )
        existing_districts[(sid, norm(dname))] = cur.lastrowid

    # Insert ULBs, resolving district_id via existing_districts (now includes newly inserted ones)
    ulbs_inserted = 0
    ulbs_skipped_existing = 0
    ulbs_district_row_missing = 0
    for key, uname, lgd_code in ulbs_to_insert:
        did = existing_districts.get(key)
        if did is None:
            ulbs_district_row_missing += 1
            continue
        if (did, norm(uname)) in existing_ulbs:
            ulbs_skipped_existing += 1
            continue
        cur.execute(
            "INSERT INTO ulbs (district_id, name, type, code, source_name, source_type, source_url, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (did, uname, None, lgd_code, SOURCE_NAME, SOURCE_TYPE, SOURCE_URL, now, now),
        )
        existing_ulbs[(did, norm(uname))] = cur.lastrowid
        ulbs_inserted += 1

    con.commit()
    print(f"\nCommitted. Districts inserted: {len(districts_to_insert)}. "
          f"ULBs inserted: {ulbs_inserted} (skipped as already-existing: {ulbs_skipped_existing}, "
          f"district row missing: {ulbs_district_row_missing}).")
    con.close()


if __name__ == "__main__":
    main()
