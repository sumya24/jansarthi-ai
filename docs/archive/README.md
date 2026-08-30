# Archive — historical, not current-state reference

The docs in this folder are kept for context (they explain *why* a past decision was made, or
record a specific past change), but they are **not** kept up to date and should not be treated as
describing the app's current behavior. If something here contradicts a doc in `docs/` itself, the
one in `docs/` is correct.

- **`ask_janmitra_response_behavior.md`** — the original Ask Sarthi spec, written before the real
  LangGraph agent existed. Superseded by `docs/ask_janmitra_orchestration.md`,
  `docs/ask_janmitra_rag_architecture.md`, and `docs/ask_janmitra_service_flow.md`.
- **`location_data_audit.md`** — a point-in-time audit of the database schema *before* the real
  location hierarchy (states/districts/wards/localities) was built. See `docs/DATABASE.md` for the
  current schema.
- **`location_migration_plan.md`** — the working plan/log for that same location-hierarchy
  migration, across several sessions.
- **`ERROR_MONITORING_WORK_SUMMARY.md`** — a chronological log of the Sentry error-monitoring
  feature's own development. See `docs/ERROR_MONITORING_GUIDE.md` (concepts) and
  `docs/ERROR_MONITORING.md` (technical reference) for the current, durable documentation.
- **`PR32_REPORT_TRANSLATION_AND_SUMMARY_MODAL.md`** — a change-log for one specific PR (translated
  PDF reports, the summary modal). The durable facts it documents (Noto Sans font registration for
  non-Latin scripts, auto-detecting translation) aren't yet duplicated into a proper reference doc
  — if you're looking for how PDF report translation actually works, this is still the best source
  until that gap is closed.
