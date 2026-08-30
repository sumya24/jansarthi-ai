# Error monitoring

Real-time alerting on unhandled errors, via [Sentry](https://sentry.io) -- backend and frontend
each report independently, both off by default (see `.env.example` /
`frontend-react/.env.example`), following the same "off unless explicitly configured" rule every
other optional integration in this project uses (LangSmith tracing, Sarvam itself).

Without this, an error in production is only visible if someone happens to be reading server
logs, or a citizen reports it. With it, an alert (email, Slack, whatever Sentry's own project
settings are pointed at) fires the moment something actually breaks.

## What's wired up

- **Backend** (`backend/main.py`'s `init_error_monitoring()`, called from inside `lifespan()` --
  see that function's own docstring for why it deliberately does NOT run at plain module-import
  time): every unhandled exception in any route is automatically captured and sent, with the
  FastAPI/Starlette route name attached. Four Sentry products, each its own on/off setting:
  - **Error monitoring** -- always on once `SENTRY_DSN` is set; this is the core feature.
  - **Logs** (`SENTRY_ENABLE_LOGS`) -- forwards this app's existing `logging.getLogger(...)`
    calls (already used throughout `backend/`) to Sentry's Logs product too. No new logging
    calls needed anywhere for this to work. **Not** wired via the top-level `enable_logs=`
    `sentry_sdk.init()` kwarg -- confirmed directly against the installed SDK
    (`sentry_sdk/client.py`) that it's a deprecated no-op there (the log pipeline is created
    unconditionally regardless of that flag). The real gate is an explicitly-constructed
    `LoggingIntegration(capture_sentry_logs=True)`, added to `integrations=[...]` only when this
    setting is on (`backend/main.py`).
  - **Application Metrics** (`SENTRY_ENABLE_METRICS`) -- a small set of business counters already
    wired into the code: `complaint.created` (tagged by ward, `routes/complaints.py`),
    `ask_sarthi.request` (tagged by channel: text/image/voice, `routes/ask_sarthi.py`), and
    `rate_limit.exceeded` (tagged by which limiter tripped: general/login/ai, `middleware.py` +
    `deps.py`). Every call site goes through `backend/services/metrics.py`, not
    `sentry_sdk.metrics` directly -- same reason as Logs above: the SDK's `enable_metrics=` init()
    kwarg is also a confirmed no-op (the metrics pipeline is created unconditionally), so
    `sentry_sdk.metrics.count()` would send regardless of the setting. `services/metrics.py` is a
    thin wrapper that actually checks `SENTRY_ENABLE_METRICS` before forwarding the call.
  - **Tracing + Profiling** (`SENTRY_TRACES_SAMPLE_RATE`, `SENTRY_PROFILE_SESSION_SAMPLE_RATE`)
    -- performance traces and code-level profiles. Profiling is coupled to tracing
    (`profile_lifecycle="trace"`, fixed): it only ever samples while there's an active trace, so
    it has no effect unless the traces rate is also `> 0`.
- **Frontend** (`frontend-react/src/main.tsx`): a top-level `Sentry.ErrorBoundary` wraps the
  whole app. Two independent things happen on a crash:
  1. The error is reported to Sentry (only if `VITE_SENTRY_DSN` is set).
  2. The citizen sees a plain "Something went wrong / Reload page" screen instead of a blank
     white page. This part always happens, DSN or not -- a crash-safety net and an alerting
     integration are two different concerns that happen to share one component.
  (Logs/Metrics/Profiling are backend-only for now -- the frontend side only wires up Error
  monitoring + Tracing, matching what `@sentry/react`'s `Sentry.init()` actually takes here.)

## Getting a DSN

1. Create a free account/project at [sentry.io](https://sentry.io) (or point at a self-hosted
   instance) -- one project for the backend (platform: Python/FastAPI), one for the frontend
   (platform: React), or one shared project for both.
2. Each project's Settings > Client Keys (DSN) page has the DSN string to copy.

## Turning it on

| Where | What to set | Effect |
|---|---|---|
| Server's `.env` (never committed, see `docker-compose.prod.yml`'s `env_file`) | `SENTRY_DSN`, `SENTRY_ENVIRONMENT=production`, plus optionally `SENTRY_ENABLE_LOGS`/`SENTRY_ENABLE_METRICS`/`SENTRY_TRACES_SAMPLE_RATE`/`SENTRY_PROFILE_SESSION_SAMPLE_RATE` | Backend reporting -- read at container **startup**, so a plain restart picks up a newly-added DSN, no rebuild needed. |
| GitHub repo Settings > Secrets and variables > Actions | `VITE_SENTRY_DSN` | Frontend reporting -- Vite env vars are baked into the JS bundle at **build** time (see `frontend-react/Dockerfile`), so this must be a CI secret, not just a server `.env` entry; `.github/workflows/cd.yml` threads it through as a Docker build-arg. Takes effect on the next deploy after the secret is added. |
| Local dev (`.env` / `frontend-react/.env`) | Same variables | Same effect, immediately, for local testing -- see `SENTRY_ENVIRONMENT=development` default so local errors never mix into the production project's event stream. |

Leaving any of these unset is a fully supported, intentional state -- the app runs identically
either way, just without alerting.

## PII

`send_default_pii` / `sendDefaultPii` are explicitly set `false` on both sides (already the SDK
default; set explicitly so a future SDK version can't silently change it unnoticed). Complaint
text and phone numbers are real citizen PII this app handles -- error reports should contain
enough to diagnose *what broke*, not a copy of what the citizen typed.

## Performance tracing

`SENTRY_TRACES_SAMPLE_RATE` / `VITE_SENTRY_TRACES_SAMPLE_RATE` default to `0` -- this feature is
scoped to error alerting, not request profiling. Raise it (e.g. `0.1` for 10% of requests) only
if performance tracing is specifically wanted later; it's a separate, additional cost on Sentry's
hosted tiers.

## A known SDK gotcha (and why the code looks the way it does)

A second live-verification pass against the real Sentry backend (not just the unit tests, which
mock `sentry_sdk.init` and so can't catch an SDK-internal behavior change like this) caught a
real bug in an earlier version of this integration: `sentry_sdk.init()`'s own `enable_logs=` and
`enable_metrics=` keyword arguments are **deprecated no-ops** in the installed SDK version --
each one's underlying pipeline (`log_batcher` / `metrics_batcher`) is constructed
unconditionally in `Client.__init__`, regardless of the flag's value. Passing them only produces
a `"...has no effect and will be removed in the next major"` warning. Concretely, this meant
Metrics used to send **even with `SENTRY_ENABLE_METRICS=false`**, and Logs used to send **in
neither state** (the pipeline existed but nothing was routing log records into it). Both are
fixed now, the way described above, and both were re-verified live (real DSN, real activity,
`debug=True` temporarily on to watch actual envelope delivery, then removed) -- confirmed off
stays silent and on actually delivers, in both directions, not just "doesn't crash."
