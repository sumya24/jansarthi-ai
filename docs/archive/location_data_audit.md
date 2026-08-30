# Sarthi Location Data Audit (Read-Only)

**Purpose:** ground the upcoming location-architecture design in exactly what exists today —
no assumptions, nothing inferred from what's planned. Every claim below was checked directly
against the live SQLite database, the ORM models, the actual route handlers, the frontend
components, and the RAG knowledge-base files — not recalled from memory or docs.

**Scope discipline:** this document is an audit only. No code, schema, or data was changed to
produce it.

---

## 1. Database structure

**Database type:** SQLite (`backend/config.py`: `DATABASE_URL` defaults to
`sqlite:///<repo_root>/janmitra.db`; swappable via env var, but SQLite is what's actually running).

**Tables (confirmed via live `PRAGMA table_info` against `janmitra.db`, matches `backend/models.py`
exactly — no drift):** `users`, `complaints`, `complaint_rejections`, `complaint_translations`,
plus SQLite's own `sqlite_sequence`. **There is no separate `assignment` table** — assignment is
represented by a single column (`complaints.assigned_worker_id`) plus a paper trail in
`complaint_rejections`, not its own entity. **There is no service/location table of any kind.**

### `users` (citizens, workers, and admins share one table, distinguished by `role`)

| column | type | null? | key | notes |
|---|---|---|---|---|
| id | INTEGER | NOT NULL | PK | autoincrement |
| phone | VARCHAR(20) | NOT NULL | unique, indexed | login identifier |
| password_hash | VARCHAR(255) | NOT NULL | | bcrypt |
| full_name | VARCHAR(120) | NOT NULL | | |
| role | VARCHAR(16) | NOT NULL | | `"citizen"` \| `"worker"` \| `"admin"` |
| preferred_language | VARCHAR(8) | NOT NULL | | e.g. `"mr"` |
| **ward** | VARCHAR(120) | NULL | | **the only location field on this table** — free-text; per the model's own docstring, "unused for citizens/admins", populated only for workers |
| created_at | DATETIME | NOT NULL | | |

No foreign keys. One index: `ix_users_phone`.

Example shape (no real data): `{id: 7, phone: "9XXXXXXXXX", full_name: "...", role: "worker", preferred_language: "hi", ward: "Ward 8 — Civil Lines, Kanpur", created_at: "..."}`

### `complaints`

| column | type | null? | key | notes |
|---|---|---|---|---|
| id | INTEGER | NOT NULL | PK | |
| citizen_id | VARCHAR(64) | NOT NULL | | stored as a string, not an FK-typed int, though it's always a `users.id` value |
| original_text | TEXT | NOT NULL | | |
| original_language | VARCHAR(8) | NOT NULL | | |
| translated_text | TEXT | NOT NULL | | canonical English |
| summary | TEXT | NOT NULL | | |
| photo_path | VARCHAR(512) | NULL | | |
| status | VARCHAR(16) | NOT NULL | | pending / assigned / accepted / resolved |
| created_at | DATETIME | NOT NULL | | |
| **ward** | VARCHAR(120) | NULL | | **the only location field on this table** |
| assigned_worker_id | INTEGER | NULL | *declared* FK → `users.id` in the ORM model | **not actually enforced at the SQLite level** — see finding below |
| feedback_rating | INTEGER | NULL | | |
| feedback_comment | TEXT | NULL | | |

**Finding — declared vs. enforced foreign key:** `backend/models.py` declares
`assigned_worker_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), ...)`, but running
`PRAGMA foreign_key_list(complaints)` against the live database returns an **empty list** — no FK
constraint actually exists in SQLite. Reason (confirmed via `PRAGMA table_info` column ordering and
matches the known no-migration-tooling limitation): this column was added later via a manual
`ALTER TABLE ... ADD COLUMN`, and SQLite cannot retroactively attach an FK constraint that way. Same
root cause applies to the `ward` columns on both tables — added the same way. Not a data-integrity
problem today (the ORM still enforces referential correctness in application code), but worth
knowing before layering more location columns on with the same ALTER-TABLE approach.

### `complaint_rejections`

| column | type | null? | key |
|---|---|---|---|
| id | INTEGER | NOT NULL | PK |
| complaint_id | INTEGER | NOT NULL | FK → complaints.id (enforced — this table was created fresh, not altered) |
| worker_id | INTEGER | NOT NULL | FK → users.id (enforced) |
| created_at | DATETIME | NOT NULL | |

Unique constraint on `(complaint_id, worker_id)`. No location fields.

### `complaint_translations`

Cache table for on-demand translated complaint text/summary per language. No location fields.

---

## 2. Current user location

**What's actually stored on a citizen's account: nothing location-related at all.**

- `SignupRequest` (`backend/routes/auth.py`) fields: `full_name, phone, password, preferred_language`.
  No ward, city, address, or coordinate field is accepted at signup.
- `User.ward` exists as a column but is documented in the model itself as "Unused for
  citizens/admins" — confirmed true in practice: nothing in `routes/auth.py` ever writes to it
  for a citizen.
- Citizens have **no persisted home/registered location whatsoever.**

Checking each requested field against the actual schema:

| field | present? | where |
|---|---|---|
| state | ❌ not stored | — |
| district | ❌ not stored | — |
| city | ❌ not stored | — |
| municipality/ULB | ❌ not stored | — |
| zone | ❌ not stored | — |
| ward | ⚠️ column exists on `users`, but unused for citizens | `users.ward` |
| area/locality | ❌ not stored | — |
| address | ❌ not stored | — |
| latitude | ❌ not stored anywhere in the DB | — |
| longitude | ❌ not stored anywhere in the DB | — |
| pincode | ❌ not stored | — |
| location_id | ❌ no such concept exists | — |

**Backend-only vs. frontend-only vs. mock:** none of these fields are backend-stored for citizens.
The frontend does capture GPS coordinates transiently in browser memory during complaint
submission (see §5) — that's frontend-only, in-memory, and is discarded before the API call is
made, not persisted anywhere.

---

## 3. Current worker location

**Workers have exactly one location attribute: `ward`, a free-text string set once by an admin at
worker-creation time.**

- `CreateWorkerRequest` (`backend/routes/admin.py`) fields: `full_name, phone, password, ward,
  preferred_language`. `ward` is required (`if not ward: raise ...`).
- The seed script (`scripts/seed_multi_ward_data.py`) shows the real shape of this data in
  practice: `"Ward 22 — Kothrud, Pune"`, `"Ward 8 — Civil Lines, Kanpur"` — **one opaque string
  combining ward number, locality, and city together**, not decomposed fields.

| field | present? |
|---|---|
| state | ❌ |
| district | ❌ |
| city | ❌ (embedded as free text inside the `ward` string, not queryable separately) |
| ward | ✅ `users.ward`, free text |
| area | ❌ (same as city — sometimes embedded in the ward string, not structured) |
| latitude/longitude | ❌ |
| availability (on/off duty) | ❌ no such field anywhere |
| service/category assignment | ❌ no such field — a worker is not scoped to WASTE_SANITATION vs. STREETLIGHTS etc., only to a ward. A single worker in a ward receives every complaint type for that ward. |

### How worker assignment actually works (`backend/services/assignment_service.py`)

Exact logic, no simplification:

1. If the complaint has no `ward` set at all → status becomes `"pending"`, no worker assigned. Stop.
2. Query `users` where `role = "worker" AND ward == complaint.ward` — **exact string equality**,
   case-sensitive, no fuzzy match, no distance calculation, no city/district fallback.
3. Order candidates by `id ASC` (i.e., whoever was created first in that ward).
4. Pick the first candidate not already present in `complaint_rejections` for this complaint.
5. If none qualify (no workers in that ward, or all have rejected), status → `"pending"`,
   `assigned_worker_id` → `None`.

There is no concept of "nearest worker," "least busy worker," or "worker specializing in this
complaint type" — routing is a single exact string match on `ward`, nothing else.

---

## 4. Current complaint location

**A complaint stores exactly one location field: `ward` (nullable free text). Nothing else.**

| field | present on `complaints`? |
|---|---|
| latitude | ❌ |
| longitude | ❌ |
| state | ❌ |
| district | ❌ |
| city | ❌ |
| ULB/municipality | ❌ |
| ward | ✅ (only field) |
| area | ❌ |
| address | ❌ |

**Is a complaint's location independent of the citizen's profile location?** Yes — trivially, in
fact, because **citizens have no profile location to begin with** (§2). The `ward` value on a
complaint comes exclusively from a per-submission form field
(`ward: str | None = Form(None)` in `POST /complaints`, `backend/routes/complaints.py`), populated
by a `<select>` dropdown on the report-issue form. That dropdown itself is backed by
`GET /complaints/wards`, which lists only wards that currently have at least one worker — so the
citizen always picks from a real, routable list, but picks it fresh, by hand, every single time.

**What happens if a citizen lives in Ward 14 but is physically in Ward 24 when filing?**
There is no "Ward 14" (home ward) recorded anywhere to conflict with — the question as posed
doesn't quite apply to the current system, because there is no stored home ward for a citizen at
all (§2). What *does* happen: the complaint is routed to whatever ward the citizen selects in the
dropdown at that moment, regardless of where they actually are. If they select "Ward 24" while
physically standing in Ward 14 (or vice versa, or from home reporting an issue somewhere else
entirely), the system has no way to detect or flag the mismatch — the dropdown selection is taken
as ground truth, unchecked against GPS or anything else. The GPS coordinates captured by the
frontend's "use current location" button (§5) are visually shown to the citizen but are **not
sent to the backend**, so today's system cannot even theoretically cross-check the two.

---

## 5. Current location feature ("Use Current Location")

**Yes, partially implemented — frontend-only, and the captured data is currently discarded before
submission.**

Found in `frontend-react/src/components/LocationPicker.tsx`, used by
`frontend-react/src/pages/ReportIssue.tsx` (the citizen complaint wizard).

- **Browser API used:** the native `navigator.geolocation.getCurrentPosition()` Web API — no
  third-party SDK.
- **Where lat/lng are obtained:** directly from the browser's `GeolocationPosition` object
  (`pos.coords.latitude`, `pos.coords.longitude`) into React component state
  (`LocationValue.coords: { lat, lng } | null`), with an 8-second timeout and a graceful fallback
  to manual ward selection on denial/error/unsupported-browser (confirmed: failure never blocks
  the flow, per the component's own docstring and code path).
- **Are coordinates sent to FastAPI? No.** Traced the full path: `ReportIssue.tsx`'s
  `handleSubmit()` builds a `FormData` and appends only `language`, `text`/`audio`, `ward`
  (`location.ward`), and `photo` — **`location.coords` is never appended.** The
  `POST /complaints` FastAPI endpoint itself has no `lat`/`lng` parameter to receive it even if it
  were sent.
- **Are they stored in the database? No** — follows directly from the above; nothing to store
  because nothing is transmitted.
- **Are they reverse-geocoded? No.** The component's own code comment states the reasoning
  explicitly: *"coords alone don't map to a ward name"* — after capturing GPS coordinates, the UI
  deliberately routes the user back into the manual ward `<select>` rather than attempting to
  derive a ward from the coordinates. No geocoding call, client- or server-side, exists anywhere
  in the codebase (`grep` for Nominatim/Google Maps/Mapbox/reverse-geocod* across the repo: zero
  matches outside this doc).
- **User-facing side effect worth flagging:** once coordinates are captured, the UI shows a
  "GPS attached" badge (`location.gpsAttached` i18n key) to the citizen — which, given the above,
  is currently a bit misleading: the badge implies the location was captured *for the complaint*,
  but those coordinates are dropped before the API call and never actually attached to anything
  persisted.

**Summary:** the capture half of "use current location" is real and works (permission handling,
timeout, graceful fallback are all solid). The transmission, storage, and geocoding halves do not
exist yet.

---

## 6. Location master data

**None exists.** Searched the full repository (`.py`, `.ts`, `.tsx`, `.json`, `.csv`) for anything
resembling states/districts/cities/ULBs/wards/pincode/boundary reference data outside the RAG
knowledge base — zero results.

- No states list, no districts list, no ULB list, no pincode table, no GeoJSON/boundary file
  anywhere in the codebase.
- The only "location list" that exists at all is the **dynamic, derived** ward list from
  `GET /complaints/wards` — which is not master data, it's just `SELECT DISTINCT ward FROM users
  WHERE role='worker'`. It only ever contains whatever ad hoc ward strings admins have typed in
  when creating workers (e.g. via `scripts/seed_multi_ward_data.py`'s hand-picked list of 6 areas).
  There is no canonical, independently-maintained list of valid wards — "valid" currently just
  means "a worker happens to exist with that exact string."

---

## 7. RAG location metadata

Inspected `backend/schemas/rag_knowledge.py` and the actual field population across all 126
records in `data/rag_knowledge_base/`.

**`KnowledgeRecord`** (the hand/generator-authored source layer) has the full intended hierarchy:
`state, district, city, municipality, zone, ward, area` + a `geographic_scope` enum
(`NATIONAL/STATE/DISTRICT/CITY/MUNICIPALITY/WARD`), plus the full source-citation block
(`source_id, source_title, source_organization, source_url, source_type, verification_status`,
etc.).

**But `Document` and `Chunk`** (the generated, retrieval-ready layer that a future retrieval
system would actually query) **only carry `state, district, city, municipality`** — `zone`,
`ward`, `area`, and `geographic_scope` are dropped during chunking
(`scripts/build_rag_knowledge_base.py`'s `render_document`/`chunk_document` simply don't pass them
through). This means even if ward/area-level data existed on a `KnowledgeRecord`, it would not
currently survive into the chunks a retrieval pipeline would filter on.

**In practice, checked live across all 126 records:** `zone`, `ward`, and `area` are **null on
every single record** (0/126 populated) — the fields exist in the schema but have never actually
been used. `district` is populated on only 2 of 126 records (the two Punjab city ones —
`S.A.S. Nagar`, `Patiala`); Odisha's state-wide records and all 112 synthetic records leave
`district` null.

**Can these fields currently be used as metadata filters during retrieval?** Structurally yes —
`state`/`city`/`municipality`/`service_id`/`source_type`/`verification_status` are present on
every chunk and would filter cleanly today (e.g. "only Punjab" or "only VERIFIED"). Ward/area/zone
filtering is not currently possible against the chunk layer, both because the fields are dropped
at chunking time and because no record has ever populated them anyway. No retrieval/embedding
code exists yet regardless (out of scope for this audit and not yet built).

---

## 8. (Instruction acknowledged)

Every finding above was verified against actual code/database output — `PRAGMA table_info`,
direct field enumeration via the live ORM classes, `grep` across the repo, and reading the actual
route handlers and components line by line. Nothing here was inferred from what's planned or
documented elsewhere; where documentation (e.g. a model docstring) made a claim, it was
cross-checked against the actual runtime behavior before being reported as fact.

---

## 9. Location architecture recommendation

*(Recommendation only — nothing here has been implemented.)*

**A. User registered/home location.** Add a structured home-location block to `User`
(`state, district, city, ulb, ward, area, pincode` — all nullable, since it doesn't exist today
and shouldn't be forced retroactively) captured optionally at signup or later via profile
settings. Keep it separate from `ward` used for worker routing today; consider renaming the
existing `users.ward` (worker-only, operational) vs. a new citizen home-location block to avoid
the two concepts colliding on one ambiguous column.

**B. User current location.** Session-scoped, not persisted to the user profile — captured
per-action (e.g. at complaint submission) the way `LocationPicker.tsx` already partially does.
Should be represented as raw `(lat, lng, accuracy, captured_at)`, resolved on the backend (not
trusted from a client-resolved ward name) into the administrative hierarchy via a location
resolver (see §10).

**C. Complaint/incident location.** This is the one that matters most for correctness and should
get the richest structure: `lat, lng` (nullable — not every citizen will grant permission),
`state, district, city, ulb, zone, ward, area`, and a free-text `address` line for anything the
hierarchy can't capture. Keep `ward` as the routing key (matches today's assignment logic
unchanged) but stop treating it as the *only* location fact worth storing — today, once a
complaint is routed, there is no way to later ask "where exactly was this" beyond one opaque
string.

**D. Worker current location.** Not present today at all (no availability/live-location concept).
If real-time dispatch is ever wanted, this needs its own lightweight, frequently-updated
structure (e.g. `lat, lng, updated_at`) — deliberately not the same table/cadence as the worker's
static operational-area assignment (E), since one changes every few seconds and the other almost
never.

**E. Worker service/operational area.** Today: one worker = one ward = every complaint category
in that ward. Recommend decomposing into `(worker_id, ward_id, service_category)` so a worker can
be scoped to specific categories per ward, and so a ward can have different specialists per
category rather than one generalist — a structural gap identified in §3 (no service/category
scoping exists today).

**F. Administrative location hierarchy.** Recommend a proper reference table set:
`states → districts → sub_districts/tehsils → ulbs/municipalities → zones → wards → localities`,
each row with a stable ID, so every other table (`users`, `complaints`, worker operational areas,
and the RAG `KnowledgeRecord`s already shaped for this) references location by ID rather than by
free-text string. This directly fixes the exact-string-match fragility in §3's assignment logic
(`"Ward 22 — Kothrud, Pune"` typed slightly differently would silently fail to match today) and
gives the RAG layer's already-defined `state/district/city/municipality/zone/ward/area` fields
something authoritative to validate against.

---

## 10. GPS-to-database flow (design only, not implemented)

```
Browser (navigator.geolocation)
  → { latitude, longitude, accuracy }
  → sent to FastAPI (new field(s) on the relevant endpoint — e.g. POST /complaints)
  → a location resolver service (new — does not exist today)
      resolves (lat, lng) against the administrative hierarchy from §9-F
      → { state, district, city, ulb, zone, ward, area }
  → the resolved ward feeds the EXISTING assignment_service.py unchanged
    (still exact ward match — just now backend-derived instead of hand-picked by the citizen)
  → the full resolved hierarchy is stored on the complaint (§9-C), not just the ward string
  → the same hierarchy fields can filter the RAG chunk layer (§7) once retrieval exists
```

Today, the chain stops at step 1: coordinates reach the browser and go no further (§5). Every
step from "sent to FastAPI" onward is new work, not a modification of something existing.

---

## What should NOT be changed (yet)

- `assignment_service.py`'s exact-ward-match logic — it's simple, correct for what it does today,
  and heavily exercised by the existing test suite (`tests/test_complaints_api.py`,
  `frontend-react/e2e/*.spec.ts`). Any new location hierarchy should feed *into* the existing
  `complaint.ward` value it already reads, not replace the matching logic itself in the same pass.
- The `users.ward` / `complaints.ward` columns themselves — still needed for backward
  compatibility with all existing data and tests; new structured fields should be additive.
- Nothing in the RAG knowledge-base data (126 records) — this audit found real gaps (§7) but per
  the standing constraint, fixing them is a separate, explicitly-scoped task.

## Dependencies/services likely needed later (not installed, not decided here)

- A geocoding/reverse-geocoding provider (e.g. an India-aware service — Google Maps Geocoding,
  Mapbox, or a self-hosted Nominatim instance) to implement the resolver in §10. None is
  currently installed (`requirements.txt` has no geocoding package; confirmed via the same repo
  search as §6).
- A canonical Indian administrative-boundary data source (state/district/ULB/ward boundaries) to
  seed §9-F's reference tables — not yet sourced; would need the same VERIFIED-sourcing rigor
  already applied to the RAG knowledge base's citizen-charter data.
