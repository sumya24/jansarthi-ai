# Mock vs. Real vs. Synthetic Location Data — Findings

**What this file is:** answers one specific question — "where is the fake/mock data, and where
are actual real place names?" — checked directly against the live database on **2026-08-20**.
Nothing here is a plan or an implementation; it's the evidence to plan from.

**The three-way distinction that matters going forward, spelled out once clearly:**
- **Mock** — a fabricated value with no real place behind it at all (a test script's leftover
  string). Must be removed.
- **Real / Verified** — an actual, correct place name or fact, checked against a real source.
- **Synthetic** — a real place name, but the *content about it* (an SLA, a contact detail) is a
  clearly-labeled placeholder, not sourced from anywhere real. Acceptable for now, per your
  earlier direction — the name must still be real, only the content may be synthetic.

---

## 1. Good news: the structured location hierarchy has ZERO mock data already

The `states` / `districts` / `ulbs` / `wards` / `localities` tables (the ones behind
`DATA_COVERAGE_TRACKER.md`) already track exactly this distinction — every single row has a
`source_type` column, set when the row was created:

| Level | Row count | `source_type` on every row | What it actually means |
|---|---:|---|---|
| States | 36 | `OFFICIAL_NATIONAL_REFERENCE` | Checked live against India.gov.in — fully real |
| Districts | 6 | `WELL_ESTABLISHED_PUBLIC_GEOGRAPHY` | Real, common public knowledge (which state a city is in) |
| ULBs | 6 | `WELL_ESTABLISHED_PUBLIC_GEOGRAPHY` | Same — real |
| Wards | 6 | `UNVERIFIED_APP_SEED_DATA` | The ward **number** and locality **name** are each real, but this specific ward-to-locality **pairing** hasn't been checked against an official ward-delimitation record (ward boundaries get redrawn periodically) |
| Localities | 6 | `UNVERIFIED_APP_SEED_DATA` | Same caveat |

**None of this is mock.** Even the "weakest" tier (`UNVERIFIED_APP_SEED_DATA`) uses real ward
numbers and real locality names — the only open question is whether "Ward 22" is *still, today*
officially paired with "Kothrud." Nothing here needs deleting; at most, the 6
`UNVERIFIED_APP_SEED_DATA` ward↔locality pairings could be independently re-checked against an
official ward-delimitation source later, as a nice-to-have, not urgent. Full detail (every single
row) is in the Excel workbook (§4) → `Location_Hierarchy_Sources` sheet.

---

## 2. The actual mock-data problem: the OLD free-text ward field, still used by every worker

**Status: cleaned up on 2026-08-21** — see `scripts/cleanup_junk_ward_test_data.py`. The 60 junk
workers, 42 junk citizens, and their 46 complaints (and every cascading row: evidence, updates,
status history, rejections, notifications) were removed after a full dry-run review. Citizen
"Priya Singh" (real seed data) was NOT deleted — only her complaint's corrupted em-dash character
was repaired. A backup was taken first
(`janmitra.db.backup-pre-junk-ward-cleanup-20260821-050417`). Verified after: `PRAGMA
integrity_check` → `ok`, all 6 remaining worker ward values are clean, database went from 352→250
users and 110→64 complaints. The findings below are kept as the historical record of what was
found and why each row was judged junk vs. real.

**Also fixed on 2026-08-21:** the case-sensitive exact-match ward routing bug itself
(`backend/services/assignment_service.py: _candidates()`), not just the one example of it. Ward
text matching now compares case-insensitively (`func.lower(...)` on both sides) instead of exact
string equality, so `"Kolhapur"` and `"kolhapur"` (or any other casing difference) now match the
same real ward. Covered by a new regression test,
`test_ward_text_match_is_case_insensitive` in `tests/test_location_system.py` — full 27-test
suite plus `tests/test_complaints_api.py` (34 tests) both pass with no regressions.


Separate from the structured hierarchy above, there's an older, simpler field —
`users.ward` / `complaints.ward` — a plain text box, not linked to the hierarchy at all. **This is
what's actually still running worker assignment today** (0 of 66 workers use the new structured
`ward_id` — see `DATA_COVERAGE_TRACKER.md` §1).

Checked every value actually stored there:

| | Count | % |
|---|---:|---:|
| **Workers with a real, clean ward value** (matches the 6 seeded cities exactly) | 6 | 9% |
| **Workers with leftover test-fixture junk** (e.g. `"Evidence Test Ward 1786616640264"`, `"Notif Test Ward ... (unrelated)"`) | 60 | 91% |

**This is the mock data you're describing.** These aren't placeholder-but-plausible entries —
they're literal test-script output (a fixed prefix like `"Tracking Test Ward"` plus a millisecond
timestamp, so every test run gets a unique-but-meaningless string) that never got cleaned out of
what is otherwise a live-looking database.

Complaints show the same pattern, plus two smaller, separate issues worth knowing about:
- **Case inconsistency:** `"Kolhapur"` and `"kolhapur"` both appear as separate values — since
  worker matching is exact-string, case-sensitive (`DATA_COVERAGE_TRACKER.md` §1 doesn't cover
  this, it's a routing-logic detail in `assignment_service.py`), these would never match each
  other or any real worker.
- **Encoding corruption:** the 6 "clean" ward strings should all read `"Ward 22 — Kothrud, Pune"`
  (an em dash, `—`), but several stored copies show a corrupted character (`�` or `\x97`) in that
  exact spot — a text-encoding bug from whenever those specific rows were inserted, not a data
  problem you introduced. Also means `"Ward 22 — Kothrud, Pune"` and its corrupted twin currently
  count as two different ward strings to the exact-match router, not one.

Full row-by-row list (every worker, every complaint ward value, flagged Clean vs. Junk) is in the
Excel workbook → `Ward_Data_Quality` and `Complaint_Ward_Text_Quality` sheets.

---

## 3. The signup-page dropdown you asked about — already built, worth one decision

You asked whether the signup form should only offer states/districts/etc. that actually have
data behind them, instead of showing every option. **This already exists** —
`frontend-react/src/components/HomeLocationPicker.tsx`, live on the real Signup page today. It
already refuses to invent options: if a level has nothing under it, the next field becomes free
text instead of a fake dropdown.

One real decision point, though: the State dropdown currently lists **17 states**
(`backend/routes/locations.py: _COVERED_STATE_CODES`), not just the **6** that have full
District→ULB→Ward→Locality data. The other 11 were added in anticipation of a not-yet-merged
branch that brings in the RAG knowledge base's wider state coverage — today, picking one of those
11 shows an empty city list and falls straight to free text (graceful, not broken, but not fully
"real data only" either).

**Decision for you:** keep showing 17 (current behavior, ready for that future merge) or trim to
the 6 that are fully real right now? Not changed either way — flagging it since you specifically
asked about this exact dropdown.

---

## 4. Live Excel dashboard (the "real-time" sheet you asked for)

`Data_Coverage_Dashboard.xlsx` (project root) — **not a static export**: it's generated fresh from
`janmitra.db` + the knowledge-base JSON files every time you run:

```bash
python scripts/generate_data_coverage_workbook.py
```

Re-run it any time the underlying data changes and it reflects the current state — that's the
"real-time" part; a spreadsheet can't stay live on its own the way a running dashboard would, but
this stays one command away from current truth instead of going stale like a one-off export.

Six sheets, each with real Excel filter dropdowns (not just formatted text) and color coding
(green = real/clean, red = missing/junk, yellow = needs attention):

1. **Overview** — the headline numbers from this whole file, in one place.
2. **State_Coverage_Matrix** — the full 36-state table from `DATA_COVERAGE_TRACKER.md`, filterable.
3. **Location_Hierarchy_Sources** — every single state/district/ULB/ward/locality row, with its
   real `source_type` and a plain-language note on what that source type means (§1, in full).
4. **Ward_Data_Quality** — every worker's ward value, flagged Clean vs. junk (§2, worker half).
5. **Complaint_Ward_Text_Quality** — same, for every distinct complaint ward string, plus the
   case/encoding issues.
6. **RAG_City_Breakdown** — every city's real vs. synthetic knowledge-base record counts, flagged
   "100% real" / "100% synthetic" / "mostly real" / "mostly synthetic".

The earlier CSVs (`data_coverage_by_state.csv`, `location_hierarchy_drilldown.csv`,
`rag_city_breakdown.csv`) have been removed — the Excel workbook now covers everything they did,
plus the two new findings above, in one place.
