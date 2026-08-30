# The Database — SQLAlchemy, SQLite, and Schema Design

*Written for someone who wants to actually understand this, not just skim it — including "why did you build it this way" answers you could give in an interview.*

> Part of the JanSarthi AI documentation set. See [`README.md`](../README.md) for the full index of every document.

---

## 1. What an ORM is, and why this project uses one

A database like SQLite or PostgreSQL speaks **SQL** — a query language (`SELECT * FROM complaints WHERE status = 'pending'`). You *could* write raw SQL strings throughout the backend. This codebase doesn't; it uses **SQLAlchemy**, an **ORM** (Object-Relational Mapper).

An ORM lets you work with the database using normal Python objects and classes instead of SQL strings. `db.query(Complaint).filter(Complaint.status == "pending").all()` (real code, from [`assignment_service.py`](../backend/services/assignment_service.py)) gets translated into the equivalent SQL by SQLAlchemy, and the rows that come back are real `Complaint` Python objects, not raw tuples.

**Why an ORM over raw SQL, specifically here:**
- **Type safety and autocomplete** — `Complaint.status` is a real Python attribute your editor knows about; a typo like `Complaint.staatus` is caught before you even run the code, unlike a typo inside a SQL string.
- **No manual SQL-injection defense** — building SQL strings by hand from user input (`f"SELECT * WHERE phone = '{phone}'"`) is a classic security hole; an ORM parameterizes queries for you by construction.
- **Database independence** — the same SQLAlchemy code would work against PostgreSQL with essentially no changes (see [§5](#5-why-sqlite-and-when-thatd-need-to-change)), because SQLAlchemy is the layer that knows how to speak each database's specific SQL dialect.

The honest trade-off, worth being able to state in an interview: an ORM adds a layer of abstraction that can hide what's actually happening at the SQL level, and for very complex queries, hand-written SQL can sometimes be more efficient. For an app this size, that trade-off clearly favors the ORM.

---

## 2. The schema, table by table

**This started as four tables** (`users`, `complaints`, `complaint_rejections`, `complaint_translations`) and has since grown to **22**, as [`backend/models.py`](../backend/models.py) grew with the app — location hierarchy tables, a fuller complaint lifecycle, notifications, AI observability, and auth all arrived later. Each is a Python class inheriting from `Base` (SQLAlchemy's declarative base — the thing that turns a plain class into something that maps to a real database table). The original four below are still the ones most worth understanding deeply for an interview — the reasoning behind them (single-table-inheritance, caching, no `relationship()`) applies just as much to everything added since. The newer groups are summarized after them rather than given the same essay-length treatment each, since most of them are straightforward extensions of the same patterns already explained here.

**Newer table groups, briefly:**
- **Location hierarchy** — `states`, `districts`, `sub_districts`, `ulbs`, `zones`, `wards`, `localities`: a real, ID-based `state → district → sub_district → ulb → zone → ward → locality` chain (see [`docs/archive/location_migration_plan.md`](archive/location_migration_plan.md)), added so a ward/city could be referenced by a stable ID instead of only ever a free-text string. `users.ward` and `complaints.ward` (the original free-text columns) are kept alongside the new `*_id` columns for backward compatibility — nothing about §2's original two tables was removed, only added to.
- **Fuller complaint lifecycle** — `complaint_status_history` (an audit trail of every status transition), `complaint_updates` + `complaint_update_translations` (a worker's initial assessment/progress notes/completion note, and their cached translations — same caching pattern as [§4](#4-the-translation-cache--a-real-caching-pattern)), `complaint_evidence` (one row per attached photo, replacing the original single `photo_path` column with support for multiple photos per complaint or update).
- **Notifications & AI observability** — `notifications` (in-app alerts for workers/citizens), `rag_answer_cache` (the same "compute once, cache, serve from cache" pattern as [§4](#4-the-translation-cache--a-real-caching-pattern), applied to Ask Sarthi's AI-generated answers), `ai_request_logs` + `ai_alert_states` (per-request cost/latency/error logging and alerting for the AI monitoring dashboard).
- **Auth** — `refresh_tokens` (backs the rotation/reuse-detection scheme described in [`docs/AUTHENTICATION.md`](AUTHENTICATION.md#5b-refresh-token-rotation-and-reuse-detection)), `email_otps` + `signup_email_verifications` (one-time codes for email verification, separate from the phone+password login itself).

### `users`

Every account — citizen, worker, or admin — is one row here, distinguished by a `role` column (`"citizen"` / `"worker"` / `"admin"`), rather than three separate tables.

**Why one table, not three?** This is a real, debatable design decision, and a good one to be able to defend. The alternative (`Citizen`, `Worker`, `Admin` as separate tables, or a proper "table inheritance" setup) would avoid `ward` being meaningless for citizens/admins. But every role shares almost every other field (name, phone, password, preferred language), and the app very often needs to answer "who is this person, regardless of role" (e.g., during login, before you even know the role) — a single table with a `role` discriminator column keeps that simple. This pattern is called **single table inheritance**, and it's a completely standard, well-known trade-off — simpler queries and migrations, at the cost of some columns being unused for some rows.

### `complaints`

The core record. Two important design choices baked into its columns:

- **Both `original_text` and `translated_text` are stored, permanently, side by side.** `original_text` is exactly what the citizen wrote or said, in their own language, and it is *never* modified after creation — it's the source-of-truth record of what was actually reported. `translated_text` is the canonical English version everything downstream (summaries, further translations) is built from. Keeping both, rather than translating-and-discarding the original, matters for trust: if a translation is ever wrong, there's still an unaltered record of what the citizen actually said.
- **`status` is a plain string column** (`"pending"` / `"assigned"` / `"accepted"` / `"in_progress"` / `"resolved"`), not a foreign key to a separate `statuses` table. For a small, fixed set of states known at build time, a string (or in a stricter setup, a database `ENUM`) is the standard, simple choice — a separate table would only pay off if statuses needed to be added/configured at runtime, which they don't here.

### `complaint_rejections`

A join-table-shaped record: one row per (complaint, worker) pair where that specific worker rejected that specific complaint, with a **unique constraint** on that pair (`UniqueConstraint("complaint_id", "worker_id", ...)`) — the database itself refuses to let the same worker reject the same complaint twice, rather than relying on application code to remember to check.

### `complaint_translations`

A **cache table** — see [§4](#4-the-translation-cache--a-real-caching-pattern). Also unique-constrained on `(complaint_id, language_code)`, since there should only ever be one cached translation per complaint/language pair; a second write to the same pair is an *update*, not a new row (see `complaint_translation_cache.py`'s `if cached is not None: ... else: db.add(...)`).

---

## 3. Relationships, and how they're enforced

`assigned_worker_id` on `Complaint` and `worker_id`/`complaint_id` on `ComplaintRejection` are **foreign keys** (`ForeignKey("users.id")`, `ForeignKey("complaints.id")`) — the database-level guarantee that these columns can only ever point at a row that actually exists in the referenced table. This is enforced by the database itself, not just trusted application code — you cannot insert a `ComplaintRejection` pointing at a `worker_id` that doesn't exist in `users`, even if there were a bug in the Python code that tried to.

Note that this codebase doesn't use SQLAlchemy's `relationship()` feature (which would let you write `complaint.assigned_worker.full_name` directly, auto-loading the related row) — relationships here are resolved manually with explicit queries (`db.query(User).filter(User.id == complaint.assigned_worker_id).first()`, see `routes/complaints.py`'s `_to_response`). This is a real, visible trade-off: more explicit and easier to reason about exactly when a query happens, at the cost of being more verbose than `relationship()` would be. Worth mentioning if asked "how would you improve this."

---

## 4. The translation cache — a real caching pattern

`complaint_translation_cache.py` is worth understanding well, because it's a clean, small example of a genuinely common real-world pattern: **compute once, cache, serve from cache thereafter, until proven wrong.**

- First time complaint #12 is viewed in Hindi: no row exists in `complaint_translations` for `(12, "hi")` → call Sarvam's translation API for real → **store** the result → return it.
- Every subsequent view of complaint #12 in Hindi: the row already exists → return it directly, **no AI call at all.**
- If a live translation call ever fails on a genuine cache miss, the failed attempt is **not** cached — so the next view retries it, instead of permanently serving a broken result (or no result) for that complaint/language forever. This "don't cache failures" rule is a small but important detail: a lazily-computed cache that caches errors as if they were successes is a classic, easy-to-make bug.

**Why this matters practically:** before this cache existed, a complaint would get re-translated by Sarvam on *every single view*, in every language, forever — real API cost and real latency, repeated pointlessly for content that never changes. This is exactly the kind of thing worth bringing up if asked about performance optimization you've actually done, not just read about.

---

## 5. Why SQLite, and when that'd need to change

SQLite stores the entire database as **one file on disk** (`janmitra.db`) — no separate database server process to install, configure, or keep running. For local development and a small pilot deployment, that's a real advantage: `git clone` and run, no infrastructure setup.

**The real limitation, worth stating plainly:** SQLite handles concurrent *writes* poorly — the whole database file is locked during a write, so many simultaneous writers will queue up and slow each other down. It's genuinely fine for a low-to-moderate-traffic pilot; it is **not** what you'd want in production for a real city's worth of concurrent citizens and workers.

**The honest answer to "what would you change for production":** swap to **PostgreSQL**, a real client-server database designed for concurrent access. Because this project goes through SQLAlchemy rather than talking to SQLite directly, that swap is mostly a one-line change to `DATABASE_URL` plus installing a PostgreSQL driver — not a rewrite. That portability is, concretely, one of the payoffs of having used an ORM in the first place (see [§1](#1-what-an-orm-is-and-why-this-project-uses-one)).

---

## 6. A known, real limitation: no migrations

Worth being upfront about, because it's a real gap and a fair thing to be asked about: **this project has no migration framework** (no Alembic, no Django-style migrations). `database.py`'s `init_db()` calls `Base.metadata.create_all()`, which creates any table that's *entirely missing* — but if you add a new column to an *existing* table, nothing automatically adds that column to a database file that already has real data in it.

The two ways this gets handled here, both already used in this codebase:
1. For local development: delete `janmitra.db` and let it recreate from scratch (fine when you don't care about existing data).
2. For a real change against existing data: write a small, one-off script that runs a manual `ALTER TABLE` — see [`scripts/migrate_assignment_tracking.py`](../scripts/migrate_assignment_tracking.py) for a real example, which backfilled `assigned_worker_id` on complaints created before that column existed.

**If asked "why not just use Alembic":** the honest answer is that this project hasn't needed enough schema changes yet to justify the setup overhead, not that migrations aren't valuable — a real production app of any size should have a real migration tool, and Alembic (SQLAlchemy's own migration library) is the natural choice given the stack already in use.

---

## Likely interview questions about this part of the project

**"Why an ORM instead of raw SQL?"** — type safety, injection safety by construction, and database portability, at the cost of an abstraction layer that can obscure exactly what SQL is being run. See [§1](#1-what-an-orm-is-and-why-this-project-uses-one).

**"Walk me through your schema design."** — four tables: `users` (single-table, role-discriminated), `complaints` (storing both original and translated text permanently, for auditability), `complaint_rejections` (a uniqueness-constrained join record), `complaint_translations` (a lazy cache). See [§2](#2-the-schema-table-by-table).

**"How do you handle caching?"** — `complaint_translations` is a real example: compute-on-first-request, store, serve from storage thereafter, never cache a failure. See [§4](#4-the-translation-cache--a-real-caching-pattern).

**"What's the biggest weakness in your data layer right now?"** — Two honest ones to name: no migration framework (see [§6](#6-a-known-real-limitation-no-migrations)), and SQLite's concurrent-write limits meaning this isn't production-scale as-is (see [§5](#5-why-sqlite-and-when-thatd-need-to-change)). Being able to name your own system's real weaknesses, unprompted, reads far better in an interview than pretending there aren't any.

---

*Related reading: [`docs/BACKEND.md`](BACKEND.md), [`docs/AUTHENTICATION.md`](AUTHENTICATION.md), [`docs/PROJECT_OVERVIEW.md`](PROJECT_OVERVIEW.md).*
