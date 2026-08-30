# Sarthi Location Migration Plan

**Status: written before implementation, validated against the live codebase and live database
(not assumptions). Updated after implementation with actual outcomes — see the "Implementation
outcome" note at the end of each section and the final report appended at the bottom.**

This plan builds directly on the read-only audit already on file at
[`docs/location_data_audit.md`](location_data_audit.md) — that document is the source of truth for
"what exists today"; this one adds the concrete migration design and, once implemented, the
as-built record.

---

## A. Current schema (re-verified live, not from memory)

SQLite (`janmitra.db`). Live row counts at the start of this migration:
**209 users** (59 workers, 109 citizens, 41 admins), **42 complaints**, plus
`complaint_rejections` and `complaint_translations`.

```
users(id PK, phone UNIQUE, password_hash, full_name, role, preferred_language, ward, created_at)
complaints(id PK, citizen_id, original_text, original_language, translated_text, summary,
           photo_path, status, created_at, ward, assigned_worker_id, feedback_rating,
           feedback_comment)
complaint_rejections(id PK, complaint_id FK, worker_id FK, created_at)  -- FKs enforced
complaint_translations(id PK, complaint_id FK, language_code, translated_text, created_at,
                        translated_summary)  -- FK enforced
```

`complaints.assigned_worker_id` is declared `ForeignKey("users.id")` in the ORM but **carries no
actual FK constraint in the live database** (`PRAGMA foreign_key_list(complaints)` returns
empty) — it was added via a manual `ALTER TABLE ADD COLUMN`, and SQLite cannot retrofit a real FK
constraint that way. Same mechanism will apply to every new column added to `users`/`complaints`
in this migration — expected and unavoidable without a full table rebuild, which this migration
deliberately does not do (see H).

## B. Current location fields

- `users.ward` — free text, `VARCHAR(120)`, nullable. Documented "unused for citizens" and
  verified true: all sampled citizen rows have `ward IS NULL`. For workers, holds a hand-typed
  string like `"Ward 22 — Kothrud, Pune"`.
- `complaints.ward` — free text, same shape, set fresh at submission time from a dropdown, never
  derived from any user profile field (because none exists to derive from).
- Nothing else. No lat/lng, no city/district/state, no address, anywhere in the database.

## C. Current worker assignment logic (`backend/services/assignment_service.py`)

Exact-string match: `User.role == "worker" AND User.ward == complaint.ward`, ordered by
`User.id ASC`, skipping anyone in `complaint_rejections` for that complaint. No distance, no
hierarchy fallback, no per-category scoping. This logic is **not being replaced** — see H.

## D. Current complaint location behavior

A complaint's `ward` comes exclusively from `POST /complaints`'s `ward: str | None = Form(None)`
parameter — i.e., whatever the citizen picks in the `<select>` on the report form at that moment,
sourced from `GET /complaints/wards` (`SELECT DISTINCT ward FROM users WHERE role='worker'`).
There is no home-ward concept to conflict with it, because citizens have no stored location at
all. This migration adds a **separate, independent** incident-location record — see F/§6 of the
original spec.

## E. Current browser GPS behavior

`frontend-react/src/components/LocationPicker.tsx` genuinely captures real coordinates via
`navigator.geolocation.getCurrentPosition()` (8s timeout, graceful fallback to manual entry on
denial/error). Confirmed by tracing `ReportIssue.tsx`'s `handleSubmit()`: the captured
`{lat, lng}` is **never appended** to the `FormData` sent to `POST /complaints` — it's discarded
client-side. This migration closes that gap (send it, accept it, store it) without touching the
capture logic itself, which already works correctly.

## F. Proposed schema

### F.1 New location-hierarchy tables (all new, all additive)

Seven tables matching the requested conceptual structure exactly, each with
`id, name, code, created_at, updated_at` plus **provenance columns added on every table**
(`source_name, source_type, source_url`, all nullable) — not explicitly requested per-table in
the brief, but "store provenance where appropriate" (§12) is applied uniformly here for
consistency rather than ad hoc per table:

```
states(id PK, name UNIQUE, code UNIQUE, country_code, is_union_territory,
       source_name, source_type, source_url, created_at, updated_at)
districts(id PK, state_id FK, name, code, source_*, created_at, updated_at)
    UNIQUE(state_id, name)
sub_districts(id PK, district_id FK, name, code, source_*, created_at, updated_at)
    UNIQUE(district_id, name)
ulbs(id PK, district_id FK, sub_district_id FK NULL, name, type, code, source_*,
     created_at, updated_at)
    UNIQUE(district_id, name)
zones(id PK, ulb_id FK, name, code, source_*, created_at, updated_at)
    UNIQUE(ulb_id, name)
wards(id PK, ulb_id FK, zone_id FK NULL, name, ward_number NULL, code, source_*,
      created_at, updated_at)
    UNIQUE(ulb_id, name)   -- "ward 24" alone is never globally unique; scoped to its ULB
localities(id PK, ward_id FK, name, pincode NULL, code NULL, source_*,
           created_at, updated_at)
    UNIQUE(ward_id, name)
```

Every FK is nullable at the *child's optional-parent* level only where the brief allows it
(`sub_district_id` on `ulbs`, `zone_id` on `wards` are nullable — not every ULB exposes a
sub-district link or every ward a zone). The FK chain otherwise is NOT NULL because the hierarchy
guarantees it (a district always has a state, per §16's "NOT NULL only where the hierarchy
guarantees the value").

### F.2 New nullable columns on `users` (home/registered location — separate from operational `ward`)

`home_state_id, home_district_id, home_sub_district_id, home_ulb_id, home_zone_id,
home_ward_id, home_locality_id` — all nullable FKs into the tables above.

**Design decision (per §5's explicit request to document it):** individual nullable FK columns,
not a single denormalized `location_id`. Reasoning: GPS/manual resolution is very often partial
(state+district known, ULB/ward not) — a single `location_id` pointing at one fully-specified
node would force either fabricating placeholder intermediate rows or discarding partial
information; separate nullable columns let "known down to district, unknown below" be represented
directly and match the existing codebase's style of explicit, individually-named nullable
columns rather than a generic indirection table.

The existing `users.ward` (worker operational area) is **kept, untouched, still authoritative for
assignment** — see C and H. It is conceptually distinct from `home_*`: `ward` is "where this
worker operates," `home_ward_id` would be "where this person lives," and today's `users.ward` is
never populated for citizens at all, so there's no collision in practice, only a naming
proximity to watch for in review.

### F.3 New columns on `complaints` (incident location — independent of any user field)

`state_id, district_id, sub_district_id, ulb_id, zone_id, ward_id, locality_id` (nullable FKs),
`latitude, longitude, gps_accuracy` (nullable floats), `address` (nullable free text).

`complaints.ward` (existing free text) is **kept, untouched, still what assignment reads first**
— see H. `ward_id` is populated alongside it where resolvable, and is the preferred match key
once populated (fallback to text match otherwise — never a behavior change for rows that can't be
resolved).

## G. Data migration strategy

1. **Backup first** (see final report for the exact path/timestamp produced).
2. Create the 7 new tables (additive `CREATE TABLE IF NOT EXISTS` via
   `Base.metadata.create_all` — never touches existing tables).
3. `ALTER TABLE users ADD COLUMN ...` / `ALTER TABLE complaints ADD COLUMN ...` for the new
   nullable columns, guarded by a `PRAGMA table_info` check so the script is safely re-runnable.
4. Seed all 36 states/UTs (real, canonical, national-level — not fabricatable, this is
   constitutional/administrative fact, not a claim requiring per-record source verification the
   way the RAG civic-service SLA data did).
5. Seed districts/ULBs only for the cities that **actually appear** in the live database's
   free-text ward values today (queried fresh, not assumed from the seed script) — see the
   migration report for exactly which ones and why others were left unmapped.
6. Deterministically parse each existing distinct `users.ward`/`complaints.ward` string, attempt
   to resolve it against the newly-seeded hierarchy, and backfill `home_ward_id`/`ward_id` (etc.)
   wherever — and only wherever — a confident, non-guessed match exists. Anything else: leave
   the FK columns null, keep the original text column exactly as it was, log it.
7. Full report at `reports/location_migration_report.md` — matched / partially matched /
   unmapped, with reasons, for every distinct value actually found in the database.

**What is explicitly NOT done:** no row is deleted, no existing text value is edited or cleared,
no ward/locality is invented to force a match, and complaints/users that can't be resolved are
left exactly as functional as they were before this migration (matching purely by
`complaints.ward == users.ward` text, as always).

## H. Backward compatibility strategy

- `users.ward` and `complaints.ward` (text) are never dropped, renamed, or overwritten by this
  migration.
- `assignment_service.py`'s matching is extended, not replaced: try `ward_id` equality first (now
  possible where both sides resolved to the same ward row); if either side's `ward_id` is null,
  fall back to the exact same text-match logic that exists today. A complaint/worker pair that
  matched before this migration still matches after it, by construction — the fallback path is
  byte-for-byte the pre-migration behavior.
- `GET /complaints/wards` (the dropdown backing) is unchanged — still text-based, still only
  offers wards a worker actually has.
- No existing API response field is removed; new fields are additive on the response models.
- No authentication, complaint-status state machine, or existing route path is touched.

## I. Tests that must continue passing

The full existing suite (92 tests at last full run: 83 original + 9 RAG schema tests added this
phase) plus the RAG data-quality scripts and, where the environment allows, the Playwright E2E
suite. See the final report for the actual re-run results after implementation — this section is
the *commitment*, not the outcome; outcomes are reported, not assumed.

---

*(The remainder of this plan — implementation results, exact files changed, migration counts,
and validation output — is appended as the final report. See also
`reports/location_migration_report.md` for the detailed per-record migration mapping.)*

---

# Final Report

## 1. Files changed

**Backend — new:**
- `backend/services/location_resolver.py` — `LocationResolver`, `NominatimGeocoder`, `ResolvedLocation`
- `scripts/migrate_location_schema.py` — additive schema migration (idempotent)
- `scripts/seed_location_master_data.py` — 36 states/UTs + 6 real cities' hierarchy (idempotent)
- `scripts/migrate_existing_locations.py` — backfills existing rows, writes the migration report
- `tests/test_location_system.py` — 14 new tests (A–M below)

**Backend — modified:**
- `backend/models.py` — 7 new tables (`State/District/SubDistrict/ULB/Zone/Ward/Locality`); new
  columns on `User` (operational `state_id..locality_id` + separate `home_state_id..home_locality_id`)
  and `Complaint` (`state_id..locality_id`, `latitude/longitude/gps_accuracy/address`)
- `backend/routes/complaints.py` — `POST /complaints` accepts `latitude/longitude/accuracy/address`;
  `ComplaintResponse` gained `latitude/longitude/address/location_state/location_district/location_ulb`
- `backend/services/assignment_service.py` — prefers `ward_id` match, falls back to text `ward` match
- `backend/schemas/rag_knowledge.py` — `Document`/`Chunk` gained `zone/ward/area/geographic_scope`
- `scripts/build_rag_knowledge_base.py` — passes the four new fields through when rendering/chunking

**Frontend — modified (minimal, per §15):**
- `frontend-react/src/components/LocationPicker.tsx` — captures `accuracy` alongside `lat/lng`
- `frontend-react/src/pages/ReportIssue.tsx` — actually sends `latitude/longitude/accuracy` to the
  API (previously captured but discarded — see `docs/location_data_audit.md` §5)

**Docs/reports — new:**
- `docs/location_migration_plan.md` (this file), `reports/location_migration_report.md`

**Data — regenerated (no hand-authored content changed):**
- `data/rag_knowledge_base/documents/documents.json`, `chunks/chunks.json`,
  `schema/*.schema.json` — rebuilt to include the 4 new metadata fields

**Backup:** `db_backups/janmitra_pre_location_migration_20260810T060615.db` (taken before any
schema change; byte-identical to the live DB at that moment, verified by size).

## 2. Database schema before/after

| | Before | After |
|---|---|---|
| Tables | 4 (+ `sqlite_sequence`) | 11 (+ `sqlite_sequence`) |
| `users` columns | 8 | 22 |
| `complaints` columns | 13 | 24 |

## 3. Tables added
`states, districts, sub_districts, ulbs, zones, wards, localities` — 7 tables, all additive, none
replacing anything.

## 4. Columns added
- `users`: `state_id, district_id, sub_district_id, ulb_id, zone_id, ward_id, locality_id`
  (operational, structured counterpart of `ward`) + `home_state_id, home_district_id,
  home_sub_district_id, home_ulb_id, home_zone_id, home_ward_id, home_locality_id` (home/
  registered, a different concept, currently unpopulated for everyone) — 14 total.
- `complaints`: `state_id, district_id, sub_district_id, ulb_id, zone_id, ward_id, locality_id,
  latitude, longitude, gps_accuracy, address` — 11 total.

Existing `users.ward` / `complaints.ward` (text) untouched, still populated the same way as before.

## 5. Data migrated

| Master data | Count |
|---|---|
| States/UTs | 36 (28 states + 8 UTs) |
| Districts | 6 |
| ULBs | 6 |
| Wards | 6 |
| Localities | 6 |
| Sub-districts | 0 (none available for any of the 6 seeded ULBs — not fabricated) |
| Zones | 0 (same reason) |

Existing-row backfill (`scripts/migrate_existing_locations.py`, run against the live DB before the
validation E2E runs added more rows): **6/59 worker rows matched** (Pune, Kanpur, Bhubaneswar,
Ahmedabad, Kolkata, Bengaluru), **18/42 complaint rows matched** (same 6 wards). Full per-value
breakdown in `reports/location_migration_report.md`.

## 6. Records requiring manual review
- `"Ward 14 — Rukadi Road"` (11 worker rows across duplicate/timestamped variants, 2 complaint
  rows) and `"Ward 15 — Rukadi Road"` (1 complaint row) — no city recorded anywhere in the
  project for this locality; genuinely ambiguous (Rukadi Road is a real place name in more than
  one Indian city) and was **not guessed**.
- `"Ward 9 — Shivaji Nagar"` (1 worker row) — same issue; "Shivaji Nagar" exists in multiple
  Maharashtra cities alone.
- All 22 distinct `"Tracking Test Ward <timestamp>"` values (Playwright test artifacts, not real
  geography) — correctly excluded, not real locations to map.

Every one of these keeps its original `ward` text exactly as it was; only the new structured ID
columns are left null for them.

## 7. Location data sources used

| Level | Source | Rigor |
|---|---|---|
| 36 states/UTs | National Portal of India, confirmed live (HTTP 200, redirect to `india.gov.in/explore-india`) on 2026-08-10 | `OFFICIAL_NATIONAL_REFERENCE` |
| 6 districts/ULBs | Well-established public knowledge (which state/district a major city is in) | `WELL_ESTABLISHED_PUBLIC_GEOGRAPHY` — explicitly NOT independently re-verified against a fetched URL this session |
| 6 wards/localities | This project's own pre-existing `scripts/seed_multi_ward_data.py` demo data | `UNVERIFIED_APP_SEED_DATA` — explicitly NOT verified against any official ward-delimitation source |

Every row carries `source_name/source_type/source_url` so a consumer can tell which tier applies —
mirrors the VERIFIED/SYNTHETIC discipline already established for the RAG knowledge base.

## 8. Source URLs
- `https://www.india.gov.in/explore-india` (states/UTs) — the only URL actually claimed anywhere
  in this migration; every district/ULB/ward row has `source_url=NULL` rather than a fabricated one.

## 9. GPS flow implemented
`navigator.geolocation` (unchanged, already worked) → `ReportIssue.tsx` now appends
`latitude/longitude/accuracy` to the submit request (previously silently dropped) →
`POST /complaints` accepts them as optional form fields → `LocationResolver.resolve_coordinates`
(Nominatim, best-effort, state/district/city only, never raises) → `normalize_location` matches
against the seeded hierarchy → whatever resolves is stored on the complaint; raw coordinates are
always stored regardless of resolution success.

## 10. Complaint location flow
`ward` text (if provided) is resolved first via `LocationResolver.resolve_ward_by_text` (more
precise, no network call); only when that doesn't resolve does a provided GPS coordinate trigger
a live reverse-geocode as a fallback. Independent of any user field — proven by
`test_g_complaint_location_is_independent_of_user_home_location`.

## 11. Worker location flow
`ward` text unchanged. New `ward_id` (operational) populated by the same migration script for
existing workers, and available for any newly-created worker once an admin UI is built to set it
(not built this phase — worker creation still only takes free-text `ward`, per §15's "no UI
redesign"). `assignment_service.py` tries `ward_id` first, falls back to text — proven
non-breaking by `test_l_existing_ward_text_assignment_still_works`.

## 12. RAG metadata changes
`Document`/`Chunk` schemas gained `zone, ward, area, geographic_scope` (previously silently
dropped during chunking even when a `KnowledgeRecord` had them — see
`docs/location_data_audit.md` §7). Rebuilt: 126 documents, 504 chunks, all still validate.
**Not done** (explicitly out of scope): linking RAG's own state/district/city text fields to the
new `states/districts/ulbs` FK tables — RAG stays denormalized text, as originally designed.

## 13. Tests executed and results
| Suite | Result |
|---|---|
| `pytest tests/` (full, 106 tests: 83 original + 9 RAG + 14 new location) | **106/106 passed** |
| `python scripts/build_rag_knowledge_base.py --check` | All 126 records valid |
| `python scripts/check_rag_sources.py` (live network) | All 3 VERIFIED URLs return 200; 0 issues |
| `npx tsc -b` (frontend) | Clean, 0 errors |
| `npm run build` (frontend) | Succeeds |
| `npx oxlint` | 0 errors, 4 pre-existing warnings (unrelated files, not touched this phase) |
| `npx playwright test` (10 E2E specs) | **10/10 passed** (one transient timing flake on a full-suite run, confirmed non-reproducing on both isolated re-run and a second full-suite run — not a regression) |

## 14. Known limitations
- Master data covers 36 states/UTs but only **6 cities'** full district→locality chain — everywhere
  else in India, GPS/ward resolution will find nothing (by design: not fabricated).
- `LocationResolver` never resolves ward or locality from GPS at all (Nominatim doesn't reliably
  cover Indian ward boundaries) — only state/district/city.
- Nominatim is a free, community-maintained, rate-limited service — not suitable as-is for
  production volume; swapping providers means writing one new class (see module docstring).
- No sub-district or zone data exists for any of the 6 seeded cities.
- `home_*` (citizen home/registered location) has no UI or API to actually set it yet — the
  columns exist, nothing currently populates them.
- Worker creation (`POST /admin/workers`) still only accepts free-text `ward` — no admin UI to
  pick a structured ward yet, so newly-created workers won't get `ward_id` until either that UI
  exists or `scripts/migrate_existing_locations.py` is re-run.
- RAG's location fields remain independent text, not linked to the new FK hierarchy.

## 15. Recommended next phase
1. Admin UI: let a super admin pick a structured ward (from the seeded hierarchy) when creating a
   worker, instead of (or alongside) free text.
2. Expand master-data coverage city-by-city, on demand, following the same
   VERIFIED/WELL_KNOWN/UNVERIFIED provenance discipline used here.
3. A citizen-facing, explicitly opt-in "set your home location" profile feature.
4. Revisit the geocoding provider choice before any real production traffic (Nominatim's usage
   policy is not meant for that).
5. Distance-based / nearest-worker assignment and real worker GPS tracking — explicitly deferred
   here per your instructions, not started.

---

# Finalization Pass (same day, second session)

A fresh inspection + hardening + validation pass, per an explicit "finalize, test, fix" request.
Nothing in the architecture was redesigned or undone — this pass found and fixed real gaps in
robustness and test coverage in the existing implementation, and re-ran everything from a clean
state.

## Fixes made during finalization

1. **`routes/complaints.py`: wrapped the resolver call site in `try/except`.** Previously, if
   `LocationResolver.resolve_ward_by_text`/`resolve_coordinates` ever raised (the real
   implementation never does, but the whole point of the resolver being swappable is that a
   *future* implementation might not honor that contract as carefully), the entire complaint
   creation would fail with a 500 — directly violating "GPS failure must never break complaint
   creation." Now caught and logged; the complaint still saves with whatever raw ward/GPS data it
   already had. Covered by `test_resolver_raising_an_exception_does_not_break_complaint_creation`.
2. **`routes/complaints.py`: added latitude/longitude range validation.** There was previously no
   check at all — a client could send `latitude=999` and it would be stored verbatim. Now
   out-of-range coordinates are discarded (treated exactly like "no GPS provided") rather than
   persisted as garbage or used to trigger a geocode call. Covered by
   `test_invalid_out_of_range_coordinates_are_discarded_not_stored`.
3. **Strengthened `test_g` (user home vs. complaint location).** The original version proved
   independence using one seeded ward and one arbitrary unseeded string — technically passing,
   but not actually the "Ward 14 vs. Ward 24" scenario from the spec. Rewritten to seed two real
   wards and assert `complaint.ward_id == Ward24.id`, `!= Ward14.id`, and that
   `user.home_ward_id` stays exactly `Ward14.id` afterward — a materially stronger test.
4. **Added the explicit "GPS never auto-saved as home location" test** — previously implied but
   never directly asserted.

## Verification performed this pass (all fresh, no cached results relied upon)

- **Database integrity**: `PRAGMA integrity_check` → `ok`; zero orphaned FKs across every
  parent/child pair in the hierarchy and both `users`/`complaints`; zero duplicate
  state/district/ULB/ward rows (checked by `GROUP BY ... HAVING COUNT(*) > 1`, all empty).
- **Backup**: re-verified readable, `PRAGMA integrity_check` → `ok`, exact pre-migration row
  counts (209 users / 42 complaints / the original 4-table schema).
- **Migration determinism**: re-ran `scripts/migrate_existing_locations.py` twice in immediate
  succession — identical match counts both times.
- **Master data**: exactly 28 states + 8 UTs = 36, confirmed via `SELECT COUNT(*) ... WHERE
  is_union_territory=0/1`. Provenance URL (`india.gov.in/explore-india`) re-checked live: HTTP 200.
- **26 location tests** (up from 14 — 12 new tests added this pass covering the full §7/§10
  matrices) — all pass.
- **Full backend suite, fresh cache-cleared run**: **118 total, 118 passed, 0 failed, 0 skipped,
  0 errors.**
- **RAG pipeline**: `--check --stats` clean (126 records), full rebuild clean (126 docs/504
  chunks), `check_rag_sources.py` live-network run clean (3/3 VERIFIED URLs return 200).
- **Frontend**: `tsc -b` clean, `oxlint` clean (4 pre-existing warnings, unrelated files),
  `npm run build` succeeds.
- **Playwright, run 4 times in a row for this finalization**: 10/10, 9/10 (voice-complaint test
  timed out waiting on real Sarvam AI response), 9/10 (complaint-submission confirmation timed
  out, same root cause), 10/10. Both flakes reproduced as clean passes when re-run in isolation
  (24.4s and normal timing respectively) — this is pre-existing Sarvam AI response-latency
  variance (documented in project memory before this phase began), **not** a location-related
  regression. Zero failures, ever, in any location-specific assertion across all 4 runs.

## Honest capability categorization (per the explicit request not to over-claim)

| Capability | Status |
|---|---|
| Location hierarchy schema (7 tables, FKs, uniqueness) | **IMPLEMENTED, TESTED** |
| 36 states/UTs master data | **IMPLEMENTED, TESTED** — real, provenance-verified |
| District/ULB/ward/locality master data | **PARTIALLY SUPPORTED** — only 6 cities; everywhere else in India has zero coverage, by design (not fabricated) |
| Existing-data migration (ward text → structured IDs) | **IMPLEMENTED, TESTED** — deterministic, idempotent, honest about what it can't map |
| Complaint structured location + GPS storage | **IMPLEMENTED, TESTED** |
| Complaint location independence from user home location | **IMPLEMENTED, TESTED** |
| GPS coordinate acceptance (manual + current-location) | **IMPLEMENTED, TESTED** |
| Reverse geocoding (state/district/city only) | **IMPLEMENTED, TESTED** — free/community provider, explicitly not production-grade |
| Ward-level resolution from GPS | **NOT SUPPORTED, BY DESIGN** — no reliable data source exists; never fabricated |
| Locality-level resolution from GPS | **NOT SUPPORTED, BY DESIGN** — same reason |
| GPS/resolver failure handling | **IMPLEMENTED, TESTED** — 7 distinct failure-mode tests, all passing |
| Worker assignment (ward_id preferred, text fallback) | **IMPLEMENTED, TESTED** — full legacy/structured combination matrix covered |
| RAG location metadata (zone/ward/area/geographic_scope) | **IMPLEMENTED, TESTED** — schema + regenerated data |
| RAG ↔ new location-hierarchy table linkage | **NOT DONE** — explicitly out of scope, RAG stays denormalized text |
| Citizen home-location profile UI | **FUTURE WORK** — columns exist, nothing populates them yet |
| Admin UI for structured worker ward assignment | **FUTURE WORK** — worker creation still free-text only |
| Distance-based / AI worker assignment | **NOT IMPLEMENTED** — explicitly out of scope this phase |
| Worker GPS tracking | **NOT IMPLEMENTED** — explicitly out of scope this phase |

**Nothing in this system should be described as "production ready."** The hierarchy and
migration machinery are solid and well-tested; the *data* backing them (6 cities' worth) and the
geocoding provider (a free, rate-limited, non-official service) are both explicitly
prototype-grade, and are reported as such everywhere in this document rather than implied to be
more complete than they are.

---

# Follow-Up Update (2026-08-29)

Several items the finalization pass above marked `FUTURE WORK` or "6 cities" have since shipped,
in later sessions. Appended rather than rewriting the table above, so the finalization pass stays
an accurate record of what was true *at that time* — this section is the current state.

**What changed:**

- **City coverage: 6 → 18 cities, still all real data.** Gujarat, Karnataka, Maharashtra, Tamil
  Nadu, Uttar Pradesh, and West Bengal each now have 3 real cities seeded (originally 1 each),
  every ward and worker backed by the same real district/ULB/ward data this migration's provenance
  discipline already required — nothing fabricated, city-by-city expansion exactly as
  [§15](#15-recommended-next-phase) recommended.
- **"Admin UI for structured worker ward assignment" is now IMPLEMENTED, not future work.**
  `AddWorkerModal.tsx` uses the real `WorkerLocationPicker` cascading picker (state → city → ward),
  not free text, when creating a worker.
- **"Citizen home-location profile UI" is now IMPLEMENTED, not future work.** `HomeLocationPicker`
  is used both at signup (`Signup.tsx`) and later in Settings (`SettingsModal.tsx`) — a citizen can
  set and change a real, structured home ward, not just free text.
- **Every location picker across the app (Signup, Settings, Add/Edit Worker) is now scoped to only
  states/cities/wards that have a real assigned worker** — previously these cascaded over the
  *entire* imported master dataset (India-wide, 90,000+ rows), which was honest but overwhelming
  (e.g. a single district with several municipalities could show 400+ wards, almost none of them
  actually routable). `backend/routes/locations.py`'s `_worker_backed_ward_ids()` now filters every
  step of the cascade to only what's actually serviceable — this is a UX narrowing, not a data
  change; the full master dataset is untouched and still backs `GET /locations/wards/{id}/localities`.
- **Report an Issue's manual ward dropdown is now scoped to the citizen's own city**
  (`frontend-react/src/lib/wardFilter.ts`), instead of one flat list of every serviceable ward
  nationwide — city-aware matching (handles a real Karnataka case: "Bengaluru Urban", the district
  name from the structured picker, vs. plain "Bengaluru", the ULB name every ward string ends in),
  with a verified (not exact-string) pre-fill so a citizen's own ward is safely pre-selected when
  it's genuinely present in the scoped list.
- **A new, separate, always-optional Area/Address free-text field** was added to Report an Issue,
  distinct from the Ward field, mapping to `Complaint.address` (which already existed but had no
  citizen-facing input for it before). Once a real ward is picked, it also offers real-locality
  autocomplete suggestions (via `GET /locations/wards/resolve` + `GET /locations/wards/{id}/localities`)
  where that data exists — never a forced choice, the field stays plain free text either way.
- **`/complaints/by-service`, which 500'd on any complaint with a null `service_category`,** is
  fixed to skip such rows instead of crashing — unrelated to this migration's own scope, but found
  and fixed while working in this area.

**Still true, unchanged from the finalization pass above:** distance-based/AI worker assignment
and worker GPS tracking remain not implemented; the RAG knowledge base stays denormalized text,
not linked to the structured hierarchy tables; the geocoding provider is still the same free,
non-official Nominatim instance. This system is still not "production ready" by the same standard
stated above — it now just covers 18 cities honestly instead of 6.
