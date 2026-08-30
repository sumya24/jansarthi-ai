# Error Monitoring — What Was Actually Done (Work Summary)

A plain record of the work performed for this feature, in the order it happened, including the
real problems found and how each was actually fixed. For the concept explanations, see
`docs/ERROR_MONITORING_GUIDE.md`. For the technical reference, see `docs/ERROR_MONITORING.md`.

---

## The request

Production-readiness pass, item 1: "how would we know if the app broke in front of a real
citizen?" Nothing existed to answer that — no error alerting, no way to know short of a citizen
complaining or someone manually reading server logs.

## What was built

**Backend** (`backend/main.py`, `backend/config.py`) — Sentry wired up as five independently
switchable products, all off by default:

| Product | Setting | What it does |
|---|---|---|
| Error monitoring | `SENTRY_DSN` | Core feature — every unhandled exception captured and sent automatically. |
| Logs | `SENTRY_ENABLE_LOGS` | Forwards this app's existing `logging.*` calls to Sentry too. |
| Application Metrics | `SENTRY_ENABLE_METRICS` | 3 real counters: `complaint.created` (by ward), `ask_janmitra.request` (by channel), `rate_limit.exceeded` (by limiter). |
| Tracing | `SENTRY_TRACES_SAMPLE_RATE` | Per-request performance timelines. |
| Profiling | `SENTRY_PROFILE_SESSION_SAMPLE_RATE` | Code-level "which line was slow," coupled to Tracing. |

**Frontend** (`frontend-react/src/main.tsx`, `CrashFallback.tsx`) — Error monitoring + Tracing,
plus a `Sentry.ErrorBoundary` that shows a real "Something went wrong / Reload" screen on a
crash instead of a blank page (works even without a DSN configured).

**CI/CD** (`.github/workflows/cd.yml`, `frontend-react/Dockerfile`) — `VITE_SENTRY_DSN` threaded
through as a Docker build-arg, since frontend env vars bake in at build time, not read at
container startup like the backend's.

## Bugs found and fixed, in the order they were caught

### 1. Wrong parameter name (`tags` vs `attributes`)

The first metrics implementation used `tags=` to label counters, matching Sentry's own online
example code. The installed SDK version actually uses `attributes=`. Would have crashed 3 real
features (filing a complaint, asking Sarthi, hitting a rate limit) the instant Metrics was
turned on. **Caught by:** running the full test suite — 48 tests failed at once. **Fixed by:**
correcting the parameter name at all 4 call sites.

### 2. Test-isolation leak

`init_error_monitoring()` was originally called unconditionally at module-import time. Since
every test file imports `backend.main`, a real `SENTRY_DSN` in `.env` would have triggered a
real Sentry init on every test run — any test deliberately exercising an error path could then
report as a genuine bug in the real Sentry project. **Caught by:** noticing an unexpected
`"Sentry is attempting to send 2 pending events"` message after a supposedly-mocked test run.
**Fixed by:** moving the call inside `lifespan()`, matching `init_db()`'s own existing
real-vs-test split (this suite's `TestClient` is deliberately not used as a context manager, so
`lifespan` never fires for a plain test request).

### 3. Logs and Metrics silently ignored their own on/off switches

The most serious one — **not caught by this session's own testing**. A second, independent
live-verification pass against the real Sentry backend found it: `SENTRY_ENABLE_METRICS=false`
did not actually stop metrics from sending (proven live: tripped a real rate limit with the
switch off, watched a metric get sent to the real project anyway), and `SENTRY_ENABLE_LOGS` sent
nothing in either position.

**Root cause, traced directly in the installed SDK** (`sentry_sdk/client.py`): `enable_logs=`
and `enable_metrics=` are deprecated no-ops in this version — both pipelines are constructed
unconditionally in `Client.__init__` regardless of the flag; passing them only produces a
`"...has no effect and will be removed"` warning.

**Fixed by:**
- **Metrics** — new `backend/services/metrics.py`, a wrapper that checks `SENTRY_ENABLE_METRICS`
  itself before calling `sentry_sdk.metrics.count()`. All 6 call sites now go through it instead
  of calling the SDK directly.
- **Logs** — the real, still-functional gate is an explicitly-constructed
  `LoggingIntegration(capture_sentry_logs=True)`, added to `integrations=[...]` only when the
  setting is on — not the top-level kwarg.

**Re-verified live, in both directions**, not just "doesn't crash": real DSN, real signup + real
complaint creation, `sentry_sdk`'s own `debug=True` temporarily enabled to watch actual envelope
delivery in the console. Confirmed `Sending envelope (log_item)` firing with Logs on, confirmed
`[Sentry Metrics] [counter] complaint.created: 1.0` + a sent `trace_metric` envelope with Metrics
on, then confirmed a repeat with `SENTRY_ENABLE_METRICS=false` produced zero metric activity in
the same real flow. The temporary `debug=True` and verification scripts were removed before
committing.

## Verification performed (full list)

- Full backend test suite run after every change: currently 608/609 passing (the one failure,
  `test_chroma_collection_opens_and_reports_expected_size`, is a pre-existing, unrelated local
  RAG-index content-drift issue — not caused by this work).
- `tsc -b`, `oxlint`, `vite build` clean on the frontend.
- A deliberately-injected frontend crash (temporary, reverted before commit) confirmed the
  `CrashFallback` screen renders correctly — screenshot-checked, not just asserted in code.
- A deliberately-triggered backend 500 (temporary `/sentry-debug` route, removed before commit)
  confirmed real error delivery.
- Real signup + real complaint creation, run twice (Metrics on, then Metrics off), with Sentry's
  own debug output on, confirming both Logs and Metrics genuinely respect their switches now.
- GitHub Actions CI green on every push to the PR branch (backend tests + frontend lint/
  typecheck/build).

## Current status

- Branch: `ops/error-monitoring`.
- PR: [#23](https://github.com/sumya24/janmitra-ai/pull/23) — open, CI passing, not merged (per
  the standing workflow for this project: pushed and verified, left for manual review/merge).
- The real Sentry DSN provided during this work is already set in the local `.env` (gitignored,
  never committed) with all 5 products currently switched on for continued testing.
- Not yet done, and out of scope for this PR: a frontend Sentry project/DSN of its own (the
  backend DSN was the only one created so far), automated database backups (the next item on the
  original production-readiness list), and Logs/Metrics/Profiling on the frontend side (only
  Error monitoring + Tracing are wired there for now).

## Files touched

```
backend/main.py                          -- init_error_monitoring(), moved into lifespan()
backend/config.py                        -- SENTRY_* settings
backend/services/metrics.py              -- new: the real Metrics on/off gate
backend/routes/complaints.py             -- complaint.created metric
backend/routes/ask_janmitra.py           -- ask_janmitra.request metric (x3 endpoints)
backend/middleware.py                    -- rate_limit.exceeded metric (general)
backend/deps.py                          -- rate_limit.exceeded metric (login, ai)
frontend-react/src/main.tsx              -- Sentry.init(), ErrorBoundary
frontend-react/src/components/CrashFallback.tsx  -- new: crash screen
frontend-react/Dockerfile                -- VITE_SENTRY_* build args
.github/workflows/cd.yml                 -- VITE_SENTRY_DSN build-arg wiring
.env.example / frontend-react/.env.example       -- documented settings, off by default
requirements.txt                         -- sentry-sdk 2.68.0
docs/ERROR_MONITORING.md                 -- new: technical reference
docs/ERROR_MONITORING_GUIDE.md           -- new: plain-language guide
docs/ERROR_MONITORING_WORK_SUMMARY.md    -- new: this file
tests/test_error_monitoring.py           -- new
tests/test_metrics.py                    -- new
```
