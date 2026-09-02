# Add Arize Phoenix as a second, self-hosted tracing backend alongside LangSmith

**Status: implemented and verified locally, 2026-08-26.** §1-4, 7 below are done (dependency,
config, `tracing.py` dual-write, DB column + admin/frontend link, tests). §5-6 (production
docker-compose/Caddy) are deliberately NOT done yet -- scoped to local only per the user's explicit
request ("we are doing that local stuff"). See "What was actually built" at the bottom of this file
for the real, current setup and how to run it.

**Second round, same day: Sessions, Metrics, Prompts, Evaluators added on top of basic tracing.**
See "Round 2" at the very bottom of this file -- Phoenix's Sessions/Metrics views are now real
(conversation grouping + Sarvam token counts), and two new one-off scripts
(`scripts/push_prompts_to_phoenix.py`, `scripts/phoenix_rag_evaluation.py`) mirror this project's
existing LangSmith equivalents. **Correction to the dependency guidance below**: `arize-phoenix-client`
(unlike the full `arize-phoenix` server package) is ALSO safe to install in this project's main
environment, confirmed directly -- see Round 2's own section for why.

**Third round, same day: real dollar cost + two Metrics gaps the user spotted live in Phoenix's
UI, plus a real bug (an incomplete tuple-signature sweep from Round 2, missed twice).** See
"Round 3" at the very bottom -- real Sarvam pricing (confirmed from Sarvam's own docs), a working
model-name tag, an explicit OK status, and a genuinely more defensive token/cost extraction that
can no longer discard a real successful answer if `usage` ever comes back malformed.

## Context

LangSmith's free tier (5,000 traces/month) was exhausted this month, which put the account into
rate-limiting ("Monthly unique traces usage limit exceeded" — confirmed both in
`backend_8000_v2.err.log` and live in the LangSmith UI screenshot). The user will create a **new**
LangSmith account/API key themselves (a plain env var swap, no code change) to get a fresh quota,
but also wants a second, genuinely free, self-hosted backend running in parallel — so tracing
never again fully stops just because one vendor's monthly cap was hit, and so there's a
zero-cost option going forward.

Explored and ruled out along the way (with the user, this session):
- **Langfuse self-hosted** — ruled out: as of 2026 it hard-requires Postgres + ClickHouse + Redis
  + S3-compatible storage, no SQLite mode exists. Too heavy to run alongside the app.
- **A separate dedicated VM** for the second backend — ruled out: user wants to stay on the single
  existing GCP VM, no new infrastructure/cost.
- **Arize Phoenix** — confirmed as the right fit: single container, OpenTelemetry-native, built
  specifically for LLM/RAG tracing, genuinely free self-hosted, and (checked live via SSH below)
  fits comfortably on the existing production VM's actual spare capacity.

**Live production VM, checked directly (2026-08-21, `ssh deploybot@8.235.38.110`):**
```
Machine type: e2-medium (2 vCPU, 3.8 GiB RAM) -- confirmed via GCP instance metadata
Memory:  3.8Gi total, 1.2Gi used, 2.0Gi free, 2.6Gi available (incl. reclaimable cache)
Swap:    4G total, only 250M used
Disk:    29G total, 15G used, 14G available
Current containers: caddy 14MB, backend 608MB -- both far under their (currently unset) limits
```
There is real headroom (~2.6GB available) for one Phoenix container capped with a conservative
memory limit (e.g. 768MB-1GB) -- it will not threaten the existing app.

**Hard constraint from the user: "the current workflow don't change."** This means every existing
LangSmith call site, span shape, redaction rule, and the annotation-queue review flow must keep
working byte-for-byte identically, whether or not Phoenix is configured. The design below achieves
this by making Phoenix support **purely additive, internal to `tracing.py`** — no other file that
currently calls into `tracing.py` (`graph.py`, `nodes.py`, `ask_janmitra_service.py`) changes its
call sites, arguments, or return-value handling at all.

## How the existing LangSmith integration works (so the addition slots in correctly)

`backend/services/observability/tracing.py` is hand-rolled, manual instrumentation (LangSmith
`RunTree`, not `@traceable` or the global LangChain callback tracer -- see its own module
docstring for why: avoids leaking raw `GraphState`/PII). Key existing functions, all fail-open
(never raise) and gated by `is_enabled()` (`LANGSMITH_TRACING` bool AND `LANGSMITH_API_KEY` both
set):

- `start_root_run(name, *, run_id=None, inputs=None, metadata=None, tags=None) -> RunTree | None`
- `start_child_run(parent, name, run_type=..., *, inputs=None, tags=None, metadata=None) -> RunTree | None`
- `end_run(run, *, outputs=None, error=None) -> None`
- `redact_text(text) -> str | None`
- `enqueue_for_review(run, reason) -> None` (annotation queue, gated by `insufficient_knowledge`/`NONE_OUT_OF_SCOPE` in `graph.py`'s `run_graph()`)
- `get_trace_url(trace_id) -> str | None` (pure string templating, no network call)

Every `RunTree` returned by `start_root_run`/`start_child_run` has a stable `.id` (a `uuid.UUID` --
either the caller-supplied `run_id`/`trace_id` or an auto-generated one). This `.id` is the hook
that lets Phoenix piggyback with **zero signature changes**: `nodes.py`/`graph.py`/
`ask_janmitra_service.py` keep passing around the same `RunTree` object as `root`/`parent`/
`trace_root` exactly as today; `tracing.py` internally keeps a parallel
`dict[uuid.UUID, otel.Span]` keyed by that same `.id`, so it always knows which Phoenix span
corresponds to which LangSmith run, without either object needing to know about the other.

Root/child lifecycle to preserve exactly: `graph.py`'s `run_graph()` creates+ends its own root run
unless one is passed in (`root_run` param); `ask_janmitra_service.py`'s `ask_with_image()`/
`ask_voice()` create the root run themselves (before vision/STT even runs) and sometimes defer
ending it until after a trailing TTS span (`ask_voice()`, `end_root_run=False` case). None of this
control flow changes -- Phoenix spans just start/end in lockstep with whatever LangSmith already
does, driven from inside the same `tracing.py` functions.

## Implementation

### 1. New dependencies (`requirements.txt`)
Add `arize-phoenix-otel` (pulls in compatible `opentelemetry-sdk` +
`opentelemetry-exporter-otlp-proto-http` transitively). Pin to latest stable at implementation
time. No other file needs a new dependency.

### 2. New settings (`backend/config.py`), mirroring the existing `LANGSMITH_*` / `SENTRY_*` "off unless explicitly configured" pattern exactly
```python
PHOENIX_TRACING: bool = os.getenv("PHOENIX_TRACING", "false").strip().lower() == "true"
PHOENIX_COLLECTOR_ENDPOINT: str = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://phoenix:6006/v1/traces")
PHOENIX_PROJECT_NAME: str = os.getenv("PHOENIX_PROJECT_NAME", "jansarthi-ai")
PHOENIX_TRACE_URL_TEMPLATE: str = os.getenv("PHOENIX_TRACE_URL_TEMPLATE", "")
```
Add matching commented block to `.env.example` next to the existing `LANGSMITH_*` section.
`PHOENIX_TRACING` defaults `false` so nothing changes for anyone who doesn't opt in (local dev,
CI, and production stay identical until this is deliberately turned on).

### 3. `backend/services/observability/tracing.py` -- the only file with real logic changes
- New lazy singleton, mirroring `_get_client()`'s pattern: `_get_phoenix_tracer()` — calls
  `phoenix.otel.register(endpoint=settings.PHOENIX_COLLECTOR_ENDPOINT, project_name=settings.PHOENIX_PROJECT_NAME, auto_instrument=False)`
  once, caches the resulting `tracer`, caches failure in a `_phoenix_unavailable` flag (same
  cache-the-failure approach as `_client_unavailable`).
- New module-level `_phoenix_spans: dict[uuid.UUID, Span] = {}` -- the RunTree-id-to-Phoenix-span
  map described above.
- `is_enabled()` stays untouched (LangSmith-only, as today -- callers that only care about
  LangSmith keep working unchanged). Add a separate `_phoenix_enabled() -> bool` following the
  identical shape (`PHOENIX_TRACING` bool AND tracer import succeeded).
- Inside `start_root_run`/`start_child_run`/`end_run`, after all existing LangSmith logic runs
  exactly as today, add an `if _phoenix_enabled(): ...` block (wrapped in its own try/except, so a
  Phoenix outage/misconfiguration can never affect the LangSmith path or the app's response) that:
  - `start_root_run`: starts a new OTel span (no parent context), stores it in `_phoenix_spans[run.id]`
    if `run` (the LangSmith RunTree) is not None.
  - `start_child_run`: looks up `_phoenix_spans.get(parent.id)`, starts a child span via
    `context=trace.set_span_in_context(parent_phoenix_span)` if found, stores under `child.id`.
  - `end_run`: pops `_phoenix_spans.pop(run.id, None)`, sets the same `inputs`/`outputs`/`metadata`
    dicts already computed for LangSmith as span attributes (already redacted -- reuse as-is, no
    new redaction logic needed), records the error via `span.record_exception()` +
    `span.set_status(...)` when `error` is passed, then `span.end()`.
- New `get_phoenix_trace_url(trace_id) -> str | None`: same pure-string-templating shape as
  `get_trace_url()`, using `PHOENIX_TRACE_URL_TEMPLATE`.
- Every new function/branch is additive -- nothing existing is deleted, renamed, or has its
  signature changed.

### 4. Persist a pointer, mirroring `langsmith_trace_id` exactly
- `backend/models.py`: add `phoenix_trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)` next to the existing `langsmith_trace_id` column on `AiRequestLog`.
- **DB migration reminder (no Alembic in this repo):** `Base.metadata.create_all()` will NOT add
  this column to the existing `janmitra.db` -- needs a small one-off idempotent script (matching
  this repo's established pattern, e.g. `scripts/add_phoenix_trace_id_column.py`, check-then-`ALTER
  TABLE ai_request_logs ADD COLUMN phoenix_trace_id VARCHAR(64)`), run once locally and once on
  production (`docker exec janmitra-ai-backend-1 python3 scripts/add_phoenix_trace_id_column.py`).
- `backend/repositories/ai_request_log_repository.py`: add `phoenix_trace_id: str | None` param to
  `record_ai_request()`, pass through to the `AiRequestLog(...)` constructor.
- `backend/services/ask_janmitra_service.py`: both existing `record_ai_request(...)` call sites
  (success and error paths) add `phoenix_trace_id=str(trace_id) if tracing._phoenix_enabled() else None`
  alongside the existing `langsmith_trace_id=...` line -- same `trace_id` UUID already generated,
  just also handed to Phoenix.
- `backend/routes/admin.py`: `AiRequestLogEntry` gets a new `phoenix_trace_url: str | None` field,
  populated via `tracing.get_phoenix_trace_url(row.phoenix_trace_id)` next to the existing
  `trace_url=tracing.get_trace_url(row.langsmith_trace_id)` line.
- `frontend-react/src/pages/AdminAiMonitoring.tsx`: add a second link/column next to the existing
  "View Trace" one, using the same fallback-to-"no link" pattern, for `phoenix_trace_url`.

### 5. Docker Compose -- add the Phoenix container without touching the existing two services
Add a new `phoenix` service to `docker-compose.prod.yml` (keeps the file additive/reviewable as
one diff, per the user's "current workflow doesn't change" constraint -- the existing `backend`
and `caddy` service definitions are not touched):
```yaml
phoenix:
  image: arizephoenix/phoenix:latest
  environment:
    PHOENIX_WORKING_DIR: /mnt/data
  volumes:
    - phoenix_data:/mnt/data
  mem_limit: 768m
  restart: unless-stopped
```
Add `phoenix_data` to the top-level `volumes:` block (alongside existing `backend_state`,
`caddy_data`, `caddy_config`). No published ports on the `phoenix` service itself -- it's reached
by (a) the `backend` container over the internal Docker network at `phoenix:6006`, and (b) admins'
browsers through Caddy, added next.

### 6. Caddy -- reverse-proxy the Phoenix UI so admins can open trace links, gated by Basic Auth
Add to `deploy/Caddyfile` (additive block, existing `@backend`/static-file handling untouched):
```
handle_path /phoenix/* {
	basicauth {
		{$PHOENIX_BASIC_AUTH_USER} {$PHOENIX_BASIC_AUTH_HASH}
	}
	reverse_proxy phoenix:6006
}
```
`PHOENIX_TRACE_URL_TEMPLATE` (env, §2) would then be something like
`https://jansarthi-ai.duckdns.org/phoenix/projects/{project}/traces/{trace_id}` -- exact path
segment confirmed against Phoenix's own UI once it's actually running (noted here rather than
guessed, since Phoenix's URL scheme should be checked against the live instance before wiring the
template).

### 7. Tests -- extend, don't rewrite, mirroring the existing dual-test-file split
- `tests/test_langsmith_tracing.py`'s pattern (mock the SDK client, assert on calls) gets a sibling
  set of Phoenix-specific unit tests in the same file or a new `tests/test_phoenix_tracing.py`
  (monkeypatch `tracing._get_phoenix_tracer`/the OTel span object similarly to how
  `_LangSmithClient` is faked today) -- covering: gating on/off, root/child span parent linkage via
  the `_phoenix_spans` dict, redaction reuse, error recording, and Phoenix-outage fail-open
  behavior (mirrors `_client_unavailable`'s test coverage).
- `tests/test_ask_janmitra_tracing.py`'s existing monkeypatches of `graph_module.tracing.start_root_run`/
  `end_run` and `nodes_module.tracing.start_child_run`/`end_run` stay **completely unchanged** --
  they patch the tracing module's public functions themselves, which is exactly why Phoenix
  support living inside those same functions (rather than a new dispatch layer) doesn't require
  touching a single line of these existing integration tests. Add one or two new integration tests
  confirming a Phoenix span is created/ended alongside the existing LangSmith fake calls when
  `PHOENIX_TRACING` is on, and that turning it on/off never changes any existing assertion's
  outcome.
- Full existing suite (`pytest tests/`) must stay green with `PHOENIX_TRACING` unset/false (the
  default), proving zero behavior change to the current workflow.

### 8. LangSmith account swap -- waiting on the user, no code change needed
**Not started yet -- blocked on the user creating the new LangSmith account and handing over its
API key.** Once that key is provided: update `LANGSMITH_API_KEY` in the local `.env` and in
production's server-only `.env` (per `docs/DEPLOYMENT_GCP.md`), then restart the backend
container. Nothing in this plan's other code changes are required for this part, and it can happen
independently, before or after the Phoenix work above.

**How long a fresh account will actually last, measured from real usage (checked directly, not
guessed):**
```
Local dev (this machine's janmitra.db):    746 AI requests over 8 days  (2026-08-13 -> 2026-08-21) = ~93/day
Production (live VM, via SSH):             217 AI requests over 11 days (2026-08-10 -> 2026-08-21) = ~20/day
```
The local and production `LANGSMITH_API_KEY` values share the same length (51 chars) and prefix
(`lsv2_p...`), strongly suggesting **both environments are burning through the same 5,000/month
quota together** -- local dev testing traffic is very likely most of what exhausted it this month,
not real citizen usage in production.
- **If a new key is used for both local dev and production, same as today:** combined rate is
  ~113 traces/day -> a fresh 5,000/month quota would last **~44 days** at this session's usage
  level (this was an unusually heavy multi-day dev/testing session, so a quieter month could last
  longer -- this is a ceiling estimate from actual recent behavior, not a guarantee).
- **If local dev is pointed only at Phoenix (or LangSmith disabled locally) and the new key is
  reserved for production traffic only:** ~20 traces/day -> a fresh quota would last **~250 days
  (~8 months)**, since real production traffic is far lighter than active development traffic.

Worth deciding alongside the account swap: keep both environments on the shared new key (simplest,
but the same exhaustion will repeat roughly every 1.5 months if dev testing continues at this
pace), or give local dev its own separate LangSmith project/key (or trace locally to Phoenix only)
so production's quota isn't consumed by testing.

## Verification
1. `pytest tests/` locally, `PHOENIX_TRACING` unset -- confirms zero regression to existing
   LangSmith behavior (the hard constraint).
2. `pytest tests/test_phoenix_tracing.py tests/test_ask_janmitra_tracing.py` with `PHOENIX_TRACING=true`
   and a local Phoenix container (`docker run -p 6006:6006 arizephoenix/phoenix:latest`) --
   confirms Phoenix spans actually appear in its UI at `localhost:6006`, with the right
   parent/child nesting, for a real `/ask-janmitra` request hit locally.
3. `docker compose -f docker-compose.prod.yml config` to sanity-check the new service/volume
   parse correctly before touching production.
4. Deploy to the VM, `docker stats` to confirm the `phoenix` container's real memory stays under
   its `mem_limit` and the existing `backend`/`caddy` containers' usage is unaffected.
5. Trigger one real Ask Sarthi request against production, then check both the LangSmith UI (new
   account) and `https://jansarthi-ai.duckdns.org/phoenix/...` (Basic Auth-gated) show the same
   trace, and confirm the Admin AI Monitoring page shows both trace links for that row.

## What was actually built (2026-08-26) -- read this before touching tracing.py again

**Scope actually done: §1-4 and §7 above, local only.** §5-6 (production docker-compose/Caddy
deployment) are NOT done -- deliberately deferred, the user asked for the local setup only this
round.

**Real deviations from the plan above, found while implementing (all now correct in code):**
- **No `docker run arizephoenix/phoenix` used locally.** Docker Desktop's engine wasn't actually
  running on this machine when this was built. Instead: the full `arize-phoenix` server package
  is installed in its own **isolated venv** at `.phoenix-venv/` (gitignored) in the project root --
  genuinely isolated from the backend's own Python environment. **Critical lesson, don't repeat
  it**: `pip install arize-phoenix` (the FULL server package) directly into the project's shared
  Python environment silently upgraded `fastapi` 0.115.6->0.141.1, `uvicorn`, and `sqlalchemy` away
  from this project's pinned `requirements.txt` versions (its dependency tree pulls in a totally
  unrelated FastAPI/SQLAlchemy/boto3/mcp/etc. stack). Caught and fixed by reinstalling
  `requirements.txt` and uninstalling `arize-phoenix`/`arize-phoenix-client`/`arize-phoenix-evals`/
  `arize-phoenix-sqlean` from the shared env, keeping ONLY the lightweight `arize-phoenix-otel`
  client package there (which imports cleanly alongside the pinned fastapi once the heavy server
  package is gone). **Never `pip install arize-phoenix` (no `-otel` suffix) into this project's own
  environment -- only `arize-phoenix-otel`.** The real server always runs from `.phoenix-venv/`:
  `"...\.phoenix-venv\Scripts\python.exe" -m phoenix.server.main serve`.
- **Phoenix's default gRPC OTLP port (4317) was already in use** on this machine by an unrelated
  process (`alloy-windows-amd64`, a Grafana Alloy collector -- not touched/killed, cause unknown,
  presumably something else on this machine). Phoenix started with `PHOENIX_GRPC_PORT=44317` set
  instead (the app only ever uses the HTTP OTLP endpoint on :6006 anyway, so this doesn't matter
  functionally).
- **The id-carrier design needed one addition not in the original plan**: Phoenix's own (OTel)
  trace id is a completely different id space from the `RunTree` uuid used as the `_phoenix_spans`
  dict key -- OTel generates it internally, it can't be forced to match. Added a second dict,
  `_phoenix_trace_ids: dict[uuid.UUID, str]`, populated at span-start time and deliberately NOT
  cleared on span end (unlike `_phoenix_spans`) -- callers need to read the real trace id AFTER the
  request already finished, to persist it as `AiRequestLog.phoenix_trace_id`. New public function:
  `tracing.get_phoenix_trace_id(run_id)`.
- **A real bug found by the existing test suite, fixed**: `tests/test_ask_janmitra_tracing.py`
  monkeypatches `graph_module.tracing.start_root_run` to return the literal string `"FAKE_ROOT"`
  (not a real `RunTree`) while leaving `nodes_module.tracing.start_child_run`/`end_run` as the REAL
  functions, to test the plumbing in isolation. The first version of this code did `parent.id`
  unguarded, which crashed on that string sentinel. Fixed to `getattr(parent, "id", None)` in both
  `start_child_run` and `end_run`, matching this module's existing "never raise" philosophy.
- **`janmitra.db` doesn't exist any more -- it's `jansarthi.db`** (project was renamed after this
  plan was first written; `backend/config.py`'s `DATABASE_URL` default confirms it). The migration
  script (`scripts/add_phoenix_trace_id_column.py`) was first written pointing at the old
  `janmitra.db` filename by mistake (copying an older script's convention) -- created a harmless
  empty stray `janmitra.db` file before being caught and fixed. Any NEW one-off DB scripts in this
  project should point at `jansarthi.db`.
- **Real Phoenix UI trace-link format, confirmed by reading Phoenix's own bundled JS** (not
  guessed): `/projects/{project_id}/traces/{trace_id}` -- NOT `/projects/{trace_id}` as originally
  guessed in this plan. The project id is fixed once the project first exists; find it via
  `GET http://localhost:6006/v1/projects`. Local `.env`'s working value:
  `PHOENIX_TRACE_URL_TEMPLATE=http://localhost:6006/projects/UHJvamVjdDoy/traces/{trace_id}`
  (that literal project id is specific to this machine's local Phoenix instance -- a fresh Phoenix
  instance/project will get a different one).

**Verified working end-to-end, live, 2026-08-26** (not just unit tests): a real `/ask-janmitra`
RAG request produced BOTH a real LangSmith trace AND a real Phoenix trace (correct
`ask_janmitra_graph` root -> `rag_retrieval`/`answer_generation`/`final_response_grounding`
children, checked directly via Phoenix's own REST API), `ai_request_logs` got both
`langsmith_trace_id` and a real 32-hex-char `phoenix_trace_id`, and `GET
/admin/ai-monitoring/requests` returned both `trace_url` and `phoenix_trace_url` correctly
populated for that row (older, pre-Phoenix rows correctly show `phoenix_trace_url: null`, not an
error).

**Regression check**: `tests/test_langsmith_tracing.py` + `tests/test_ask_janmitra_tracing.py` +
`tests/test_ai_monitoring.py` = 63 passed (both with Phoenix enabled and explicitly disabled).
`pytest tests/ --collect-only` = 886 tests, zero import/collection errors across the whole suite.
A full `pytest tests/` run was attempted but didn't finish in a reasonable time on this machine
(consistent with previously-documented Windows page-file/memory pressure under a long combined
test run, not something caused by this change) -- not re-attempted given the above already gives
strong confidence; worth a clean full run later when convenient.

**How to run this locally, from scratch, next time:**
```powershell
# 1. Start the isolated Phoenix server (separate terminal, stays running)
"C:\...\janmitra-ai\.phoenix-venv\Scripts\python.exe" -m phoenix.server.main serve
# (if port 4317 is taken by something else: $env:PHOENIX_GRPC_PORT=44317 first)

# 2. .env already has PHOENIX_TRACING=true / PHOENIX_COLLECTOR_ENDPOINT / PHOENIX_PROJECT_NAME /
#    PHOENIX_TRACE_URL_TEMPLATE set -- just start the backend normally.
python -m uvicorn backend.main:app --reload --port 8000

# 3. Open http://localhost:6006 to browse traces directly, or use the Admin AI Monitoring page's
#    new "View Phoenix trace" link next to the existing LangSmith one.
```

**§5-6 (production) written 2026-08-30, not yet deployed:** `docker-compose.prod.yml` now has a
`phoenix` service (pinned `arizephoenix/phoenix:version-20.4.0` -- matches the exact PyPI version
already verified working locally, confirmed against Docker Hub's real tag list, not guessed) with
`PHOENIX_DEFAULT_RETENTION_POLICY_DAYS=30` set from first boot (a real, documented Phoenix env var,
confirmed against its own source -- avoids the unlimited-retention memory-bloat incident from
Round 9 ever happening on a fresh instance).

**Access control revised 2026-08-30, same day, before this shipped**: the first version gated
Phoenix's UI behind a single fixed HTTP Basic Auth username/password (`PHOENIX_UI_USER`/
`PHOENIX_UI_PASSWORD_HASH`). User's real, live question surfaced the actual problem with that:
this app has multiple admins, and a fixed shared password doesn't map to that at all -- either
everyone shares one login (can't tell who did what, can't revoke just one person), or someone has
to hand-provision a separate Caddy credential per admin and keep it in sync by hand forever.
Checking production's real user table to plan the fixed-per-admin version surfaced a bigger,
unrelated finding instead: of 77 `role=admin` rows, only 2 (`Anjali Kulkarni`/`9999999999`,
`Vikram Desai`/`6192340986`, both created at initial seed time) look like real people -- the other
75 are unmistakably automated test/e2e-seed artifacts (literal names like "Tracking Test Admin",
tight-second timestamp clusters). Same pattern in `role=worker` (108 rows, ~7 look real). Reported
back as a table, explicitly NOT deleted without sign-off (production data, real risk of
misclassifying something as test when it's actually real) -- still awaiting that go-ahead as of
this writing.

**Final approach, once "make sure the admin role has access" was the actual ask**: gate Phoenix's
UI via Caddy's `forward_auth` directive instead of a fixed password at all -- it asks the BACKEND
itself, per request, "is this a real, currently logged-in admin?" (new `GET /admin/
phoenix-auth-check` in `backend/routes/admin.py`, just `require_role("admin")` wrapping a 204).
Whoever holds the app's own real `admin` role gets Phoenix access automatically, and losing that
role removes access the same way -- no separate credential to create, sync, or revoke, and no new
env vars needed for auth at all. The browser's own `access_token`/`refresh_token` cookies ride
along on the forwarded request as plain, unconfigured header forwarding (same as any proxied
request), so an admin already logged into the app in that browser reaches Phoenix's UI directly,
no separate password prompt at any point.

**Deliberately needed ZERO changes to `ci.yml`/`cd.yml`**: Phoenix is a public, pre-built image,
not one this repo builds itself -- the existing deploy step's `docker compose pull && docker
compose up -d` already fetches and starts anything listed in `docker-compose.prod.yml`, same
mechanism that already handles backend/frontend. Verified the compose file itself is valid two
ways without needing Docker running at all: `python -c "import yaml; yaml.safe_load(...)"` and
`docker compose -f docker-compose.prod.yml config --quiet` (works client-side, no daemon needed).

**One real manual step still required before this actually goes live**: the server's own `.env`
(never committed) needs `PHOENIX_TRACING=true` set once, same one-time-setup category as
`JWT_SECRET_KEY`/`SARVAM_API_KEY` already are. Until that happens, `PHOENIX_TRACING` defaults to
`false` there too, so merging this costs nothing and changes nothing in production on its own.
**Superseded below (2026-08-30) -- `cd.yml` now sets this itself on every deploy, so this manual
step is no longer needed at all.**

**Closed out 2026-08-30, same day: the login-redirect gap, verified live without Docker, then
merged (PR #49).** One more real round on the same PR before it went in:
- `phoenix_auth_check()` originally used `Depends(require_role("admin"))` like every other admin
  route -- but `forward_auth` copies back whatever this endpoint returns on any non-2xx status,
  verbatim, so a plain 401 (no session at all) showed the visitor a bare, unstyled JSON error
  instead of anywhere to go. Rewritten to call `get_current_user()` directly instead of depending
  on it, specifically so "no session at all" can become a real `302` redirect to `/login`, while a
  real session that just isn't an admin still gets a plain `403` (rare enough -- only ever an
  admin would have this URL -- not worth a friendlier path too).
- **Verified all 4 real cases end to end, live, without Docker at all** (the user specifically
  didn't want a local Docker Compose run -- too heavy for the machine) -- downloaded the real
  standalone `caddy` binary (~50MB, no daemon/VM, nothing like Docker's overhead), pointed a copy
  of the real `deploy/Caddyfile` at `localhost` instead of Docker's internal service names, and
  ran it against the real backend + real Phoenix + the real Vite dev server (for the actual login
  page) all as plain local processes. Real result: no session -> `302` to `/login`; logged in as a
  citizen -> `403`; logged in as a real admin -> `200` (Phoenix's actual dashboard loads); same
  admin, second visit -> still `200`, no re-prompt. **One real, non-obvious snag hit and fixed
  during this**: Vite on this machine listens on IPv6 (`[::1]:5173`), not `127.0.0.1:5173` --
  pointing Caddy's proxy at the IPv4 address specifically caused a `502` ("connection actively
  refused") even though `curl http://localhost:5173` worked fine (curl's `localhost` resolved to
  `::1` first). Fixed by proxying to `localhost:5173` instead of the IPv4 literal, letting Caddy's
  own resolution match. This whole technique (a bare `caddy` binary, no Docker) is the fast way to
  sanity-check any future `deploy/Caddyfile` change before it goes near production.
- **User's own explicit call, worth recording**: asked directly whether to just drop the access
  check entirely and make Phoenix's UI open with no login at all, "like local." Talked through
  why local and production aren't the same kind of "open" -- `localhost:6006` is only reachable
  from the one machine it runs on, while production's URL is public; removing the check there
  would mean real citizen questions and cost data become visible to anyone who finds the link, not
  just simpler config. Recommendation (keep the check) stood; nothing was removed.
- **PR #49 merged into `main` this same day** after: (1) CI passing (`backend-tests`,
  `frontend-build`) on the final commit, (2) updating the branch with `main` first (GitHub's own
  branch-protection rule required this -- the branch had fallen behind `main`, which had picked up
  PR #48's large "AI production hardening" merge -- MCP server, guardrails, reranker, hybrid
  search, RAGAS eval -- in the meantime; merged clean, zero conflicts, since none of that work
  touches any file this PR does).
- The LangSmith account-swap's own follow-up decision (local dev sharing the same key vs. its own)
  was never actually decided -- worth revisiting, especially since the *new* LangSmith account
  independently hit its own rate limit again during this same session (confirmed live via a 429
  during a local pytest run) -- the local/production key-sharing question from §8 above is still
  unresolved and still causing real pain.

**Same day, immediately after: the PR's own CI failure turned out to be real (not something to
bypass) and the "one manual step" above got automated away too.**
- `backend-tests` was failing on the exact commit merged above: `test_response_language_follows_the_actual_text_not_a_stale_ui_toggle`
  asserted a real Sarvam language-detection call would return `"mr"`, but Sarvam's account
  returned `402 Payment Required` (credits exhausted -- the same billing issue hit elsewhere this
  week). Initially reached for an admin-override merge to bypass this one check; asked directly
  "why are you merging fix this 1st" -- correct call. Real fix instead: the test now probes the
  same real `identify_language()` call first and `pytest.skip()`s with a clear reason if it comes
  back `None` (Sarvam's own fail-open contract for ANY failure -- see its docstring), so a real
  regression still fails the test but a billing/quota outage no longer looks identical to one.
  Verified locally (skipped, as expected, credits still exhausted) before pushing. CI went green
  for real (`backend-tests: pass`, `frontend-build: pass`) and PR #49 merged with no override.
- User then asked, correctly: why does turning Phoenix on in production need a manual SSH step at
  all when the repo already has CD? It doesn't need to. `.github/workflows/cd.yml`'s deploy script
  already upserts `BACKEND_IMAGE`/`FRONTEND_IMAGE` into the server's `.env` on every deploy --
  added one more line to that same block, `PHOENIX_TRACING=true`, upserted the same idempotent way
  (strip any existing line, then append). From this point on, every deploy to `main` keeps Phoenix
  tracing on by construction -- no server SSH, no hand-edited `.env`, ever, for this specific
  toggle. Access to the UI itself is untouched by this (still gated on the real admin login via
  `phoenix_auth_check()`) -- this only controls whether traces get sent.

---

## Round 2 (same day, 2026-08-26): Sessions, Metrics, Prompts, Evaluators

User noticed Phoenix's UI has far more than a Traces tab and asked to build out everything
genuinely usable at "production level." Implemented: Sessions (real conversation grouping),
Metrics (real Sarvam token counts), and two new one-off scripts mirroring the existing LangSmith
tooling (Prompts push, RAG evaluation). Datasets & Experiments beyond the eval script, and
Playground/REST/GraphQL, needed no work -- see the approved plan for why.

**Correction, confirmed directly this round: `arize-phoenix-client` is ALSO safe in the main env.**
Round 1's guidance ("only `arize-phoenix-otel` belongs in this project's environment") was
one step too cautious. `arize-phoenix-client` (REST client for Prompts/Datasets/Experiments) has
its own lightweight dependency tree (httpx, openinference-instrumentation,
openinference-semantic-conventions, opentelemetry-exporter-otlp, opentelemetry-sdk, tqdm) that's
the SAME family `arize-phoenix-otel` already pulls in -- installing it added exactly one new
package, zero new heavy ones, and `fastapi`/`starlette`/`sqlalchemy` stayed pinned throughout
(checked directly before and after). It's now a normal `requirements.txt` entry. The constraint
that's still real: the FULL `arize-phoenix` server package (and its `[evals]` extra,
`arize-phoenix-evals`) must never go in the main env -- that's the one with the actual
FastAPI/SQLAlchemy conflict, confirmed the hard way in Round 1.

### A. Sessions -- real, end-to-end

`AiRequestLog.conversation_id`/`GraphState`'s `conversation_id` existed as unused plumbing before
this round (always `None` -- nothing generated or sent one). Now real:
- `frontend-react/src/pages/AskJanMitra.tsx`: a `conversationId` state, generated via
  `crypto.randomUUID()`, persisted in `localStorage` alongside chat history (same key-per-user,
  same reload-survives lifecycle -- see `loadOrCreateConversationId()`'s docstring), regenerated
  only on "New chat" (`handleNewChat()`). Threaded through all three entry points (`askJanMitra`,
  `askJanMitraWithImage`, and via a new `conversationId` prop into `VoiceAssistantOverlay.tsx` for
  `askJanMitraVoice`) so a voice turn groups into the SAME session as the rest of the chat.
- `backend/schemas/ask_janmitra.py`: `AskJanMitraRequest.conversation_id: str | None = None` (new
  field). Both multipart routes (`/ask-janmitra/image`, `/ask-janmitra/voice`) gained a matching
  `Form(None)` param.
- `backend/services/ask_janmitra_service.py`: `_build_initial_state()` now sets
  `GraphState["conversation_id"]`; `ask()`/`ask_with_image()`/`ask_voice()` all pass it through.
  Also fixed a real, if minor, pre-existing gap while touching this: `_run()`'s exception path
  hardcoded `conversation_id=None` in its `AiRequestLog` write instead of reading it from
  `initial_state` -- fixed alongside this change.
- `backend/services/orchestration/graph.py`'s `root_run_inputs_and_metadata()`: added
  `conversation_id` to the metadata dict handed to `tracing.start_root_run()`.
- `backend/services/observability/tracing.py`: `_phoenix_start_span()` gained a `metadata` param
  (only ever passed by `start_root_run()`, never `start_child_run()` -- a child doesn't need its
  own session id, Phoenix groups the whole trace under whatever the ROOT span carries), reads
  `conversation_id` out of it and sets `SpanAttributes.SESSION_ID`.
- **Verified live**: two real `/ask-janmitra` requests with the same `conversation_id` both showed
  up under that same session in Phoenix's own span data (`attributes["session.id"]`), checked
  directly via the REST API. `AiRequestLog.conversation_id` also now populates correctly (checked
  in the DB directly) -- a free side benefit, that column existed but was always `None` before.

### B. Metrics -- real Sarvam token counts

- `backend/services/answer_generation_service.py`: `generate()`'s return type changed from
  `tuple[str, bool]` to `tuple[str, bool, dict[str, int] | None]` -- the third value is
  `{"prompt_tokens", "completion_tokens", "total_tokens"}` read straight from Sarvam's own
  `response.usage` (confirmed the SDK actually returns this, `sarvamai.types.completion_usage.
  CompletionUsage` -- real data, not estimated), `None` on the fallback/no-LLM path (nothing to
  report). **7 call sites updated for the new 3-tuple**: the real one in
  `backend/services/orchestration/nodes.py`'s `rag_flow_node`, plus 6 test-double fakes across
  `tests/test_ask_janmitra*.py`/`test_orchestration_graph.py`/`test_image_content_validation.py`/
  `test_rag_answer_cache_integration.py` that construct a 2-tuple return value.
- `backend/services/observability/tracing.py`: `_phoenix_end_span()` now special-cases
  `prompt_tokens`/`completion_tokens`/`total_tokens` keys in `outputs` (when present) to ALSO set
  the dedicated `SpanAttributes.LLM_TOKEN_COUNT_PROMPT`/`COMPLETION`/`TOTAL` attributes Phoenix's
  Metrics view actually reads -- they stay in the generic JSON `output.value` blob too (harmless
  duplication).
- **Verified the mechanism live, but couldn't visually confirm a populated number**: Sarvam's
  credits were exhausted for real during this round's live testing (`402 insufficient_quota_error`,
  confirmed directly) -- every live test either hit the RAG answer cache (a prior turn's real
  answer, correctly reported with NO token data since no fresh LLM call happened) or the fallback
  path (also correctly no token data). Both are the intended, honest behavior -- token counts
  should only appear for a genuinely fresh LLM call. Not re-verified with real numbers visible in
  the Phoenix UI; worth a quick recheck once Sarvam credits are available again.

### C. Prompts -- `scripts/push_prompts_to_phoenix.py`

Mirrors `scripts/push_prompts_to_langsmith.py` exactly (same two prompt files, same mirror-only
contract). Uses `phoenix.client.Client(base_url="http://localhost:6006").prompts.create(...)`.
`model_provider="OPENAI"` is a labeling compromise (Phoenix has no "SARVAM" option; Sarvam's own
API is OpenAI-shaped, same as this app's other Sarvam integration comments already note) --
`model_name` is still the real `sarvam-105b`. **Run and verified for real**: pushed successfully
on the first try, confirmed via `GET /v1/prompts` showing the real prompt with the right name/
description.

### D. Evaluators -- `scripts/phoenix_rag_evaluation.py`

Mirrors `scripts/langsmith_rag_evaluation.py` exactly (same dataset, same two scores --
`retrieval_correctness` deterministic, `groundedness` LLM-as-judge via Sarvam). Uses
`client.experiments.run_experiment(dataset=..., task=..., evaluators=[...])` -- Phoenix's near-1:1
equivalent of LangSmith's `client.evaluate()`. **Run and verified for real**: uploaded all 21
cases as a real Phoenix Dataset (confirmed via `GET /v1/datasets`), ran the real production RAG
pipeline against all 21 (Sarvam being down meant every case hit the fallback path, not a script
bug -- the task function correctly handled every failure without crashing), completed "21 task
runs, 2 evaluator runs, 42 evaluations." One real, now-fixed bug found by this live run: the
final summary line accessed `ran.experiment.id`, but `run_experiment()` actually returns a
`RanExperiment` TypedDict (`ran["experiment_id"]`, not an object with a nested `.experiment`) --
fixed, not re-run again afterward (re-running would just create a duplicate experiment entry and
spend more of the currently-exhausted Sarvam quota for no new information -- the fix itself is a
one-line, unambiguous dict-key correction, confirmed against `RanExperiment`'s real type
definition directly rather than re-run).

### Also hit and fixed this round (unrelated bug, found via the existing test suite)

`start_child_run()`/`end_run()` originally did `parent.id`/`run.id` unguarded before my Phoenix
additions. `tests/test_ask_janmitra_tracing.py` deliberately monkeypatches
`graph_module.tracing.start_root_run` to return the literal string `"FAKE_ROOT"` (not a real
`RunTree`) while leaving `nodes_module.tracing.start_child_run`/`end_run` as the real functions, to
test the plumbing in isolation -- my first version crashed on that string sentinel. Fixed to
`getattr(parent, "id", None)` / `getattr(run, "id", None)`, matching this module's existing
"never raise" philosophy. Caught by running the real test suite, not by inspection.

### Regression check

`pytest tests/test_ask_janmitra*.py tests/test_orchestration_graph.py
tests/test_image_content_validation.py tests/test_rag_answer_cache_integration.py` (the files
touching the changed `generate()` signature) = 120 passed, 2 failed -- both failures confirmed
unrelated to this round's changes (real Sarvam 402 mid-test, and the same known Windows
page-file/embedding-model-load issue documented elsewhere in this project's memory). `npx tsc -b`
on the frontend showed 2 pre-existing errors in `AdminAiMonitoring.tsx` (an unrelated,
already-in-progress search feature from other work on this file, not touched here) and zero new
errors from this round's own changes.

---

## Round 3 (same day, 2026-08-26): real dollar cost, plus two dashboard gaps the user actually
## spotted live in Phoenix's own UI (screenshot-driven, not guessed)

User opened Phoenix's Metrics dashboard and reported several real gaps by name: "Total Cost: $0",
"Status: Unset" on the trace, empty "Top model by cost"/"Top model by tokens" charts, empty
Span/Trace/Session Annotation Store sections. Then separately asked me to look up Sarvam's real
pricing online rather than needing it supplied.

**Real Sarvam pricing, confirmed directly from Sarvam's own docs
(`https://docs.sarvam.ai/api/getting-started/pricing`, checked 2026-08-26)**: sarvam-105b is
Rs29.28/1M input tokens, Rs10.98/1M cached-input tokens (unused -- Sarvam's SDK never reports a
cached-token count for this model), Rs73.2/1M output tokens. Converted to USD at ~95.5 INR/USD
(also checked live that day) purely so Phoenix's Metrics view -- which hardcodes a literal "$" in
front of the number -- shows something roughly right instead of a silently mislabeled rupee
figure. This conversion is a real, documented approximation (exchange rates move daily); the INR
rate itself is not.

**What was actually fixed:**
1. **Real cost, not $0**: `answer_generation_service.py`'s `generate()` now also computes
   `prompt_cost_usd`/`completion_cost_usd`/`total_cost_usd` from the real token counts against the
   confirmed Sarvam rate above (constants `_SARVAM_INPUT_COST_PER_TOKEN_USD`/
   `_SARVAM_OUTPUT_COST_PER_TOKEN_USD`), folded into the same `token_usage` dict the Metrics work
   already added. `tracing.py`'s `_phoenix_end_span()` promotes these three keys to
   `SpanAttributes.LLM_COST_PROMPT`/`LLM_COST_COMPLETION`/`LLM_COST_TOTAL`.
2. **"Top model by cost/tokens" empty**: never fixable by cost data alone -- those charts need to
   know WHICH model answered, and nothing set `SpanAttributes.LLM_MODEL_NAME` before this round.
   `nodes.py`'s `answer_generation` span now tags `model_name: settings.LLM_MODEL` (only when a
   real LLM call actually happened, same reasoning as token/cost data -- a cached or fallback
   answer wasn't produced by any model just now).
3. **"Status: Unset"**: `_phoenix_end_span()` never explicitly set an OK status on success, only
   ERROR on failure -- an "Unset" span in Phoenix's UI is otherwise indistinguishable from one that
   crashed before ever finishing. Now sets `Status(StatusCode.OK)` explicitly in the non-error path.
4. **Span/Trace/Session Annotation Store, still empty -- correctly, not a bug**: this is a
   genuinely different, NOT-yet-built feature -- live, automatic quality-scoring of every real
   citizen question as it happens. What exists today (the offline `phoenix_rag_evaluation.py`
   script) only scores a fixed 21-question test set, once, manually -- it never attaches scores to
   real production traces/sessions. Flagged to the user as a real option, not started without
   being asked (would add judge-LLM latency/cost to every single live request).
5. **"Prompt/completion token DETAILS" (the more granular sub-breakdown) empty -- also correct,
   not a bug**: checked directly with a live Sarvam call -- `usage.prompt_tokens_details` and
   `usage.completion_tokens_details` both come back `None` from Sarvam's own API. There is no
   deeper breakdown to surface; Sarvam's API doesn't provide one.

**A real, more serious bug found while re-testing: the Round 2 `generate()` signature change (2-tuple
-> 3-tuple) was NOT fully swept the first time.** Missed call sites, found via a second, wider
grep pass after a live pytest run surfaced the first one as a real failure (not a flaky/environment
one this time):
- `tests/test_orchestration_graph.py:387` -- a second, differently-styled mock
  (`fake_answer_service.generate = Mock(return_value=(...))`) in the SAME file my first pass had
  already partially fixed (a different mock, styled as a `lambda`, further down the same file, was
  already caught the first time -- this second one used a different pattern and slipped through).
- `tests/test_rag_grounding_topic_mismatch.py` -- FOUR call sites calling the REAL
  `AnswerGenerationService.generate()` directly (not mocked at all) and unpacking a 2-tuple --
  would have raised `ValueError: not enough values to unpack` the moment anyone ran this file.
- `scripts/langsmith_rag_evaluation.py` -- the EXISTING LangSmith eval script (not touched in
  Round 2) also unpacks `generate()`'s return -- would have crashed the instant it was next run.
All fixed (added the third tuple element / an extra unpacked variable). **Lesson, if
`generate()`'s signature is ever touched again**: grep for literally every call/mock pattern
(`.generate(`, `.generate =`, `svc.generate`, `answer_service.generate`) across `tests/`,
`scripts/`, AND `backend/` before considering it done -- a single grep pattern missed real call
sites twice in one day.

**Also found and fixed, a real defensive-coding gap**: the original token/cost extraction code sat
inside `generate()`'s OUTER try/except (the same one that catches a genuine Sarvam API failure) --
meaning if `response.usage` ever came back in an unexpected shape (confirmed this can actually
happen: a test's plain `Mock()` response object makes `usage.prompt_tokens` a `Mock`, and
`Mock * float` raises `TypeError`), the WHOLE call would look like a failed Sarvam request and
silently discard a real, successfully-generated answer in favor of the fallback -- a much worse
outcome than just losing some Metrics data for that one turn. Fixed: token/cost extraction now
has its own inner try/except, so a malformed usage object can only cost observability data, never
throw away a real answer that already succeeded.

### Regression check (Round 3)
`pytest tests/test_rag_grounding_topic_mismatch.py tests/test_orchestration_graph.py
tests/test_rag_answer_cache_integration.py` = 60 passed (0 failed -- this is the exact set that
had 1 real failure before the fixes above). `pytest tests/ --collect-only` = 886 tests, zero
import/collection errors project-wide, confirming no other file references the old 2-tuple shape.
Live-verified once more with a real, fresh Sarvam call: real cost (`$0.00029` prompt + `$0.00115`
completion for one real answer), real model name (`sarvam-105b`), and `OK` status all showed up
correctly in Phoenix's own span data, checked directly via its REST API.

## Round 4 (2026-09-02): review-annotation was silently a no-op in production, ever

While preparing a demo, checked whether Phoenix's Evaluators/annotation data was actually populated
in production — it wasn't. `spanAnnotationNameCounts`/`traceAnnotationNameCounts`/
`documentEvaluationNames` all came back empty via a direct GraphQL query against the live project,
despite real out-of-scope/insufficient-knowledge questions having fired `enqueue_for_review()` many
times over the preceding week.

**Root cause, confirmed live, not guessed**: `_phoenix_enqueue_for_review()`'s Round 3-era
`force_flush(timeout_millis=2000)` fix (see §8 above / `docs/OBSERVABILITY.md`) only closed the
"Phoenix batches exports" gap — it guarantees the span was *exported* over OTLP, not that Phoenix's
own backend has finished *ingesting and indexing* it into something queryable via GraphQL yet.
Reproduced directly: asked a real out-of-scope question against production, then grepped the
backend's own logs for the resulting GraphQL calls. The project lookup and the spans-lookup query
both came back `200 OK` about 4 seconds after the span ended — but the spans-lookup's result set
didn't include this run's span, so `span_graphql_id` came back `None` and the function returned
before ever reaching the annotation mutation (exactly the same silent-no-op shape the Round 3 fix
was meant to close, just with a longer real-world gap than one flush call covers). Confirmed this
wasn't a permanent gap either — the exact same span, looked up again a few minutes later, was there.

**Fix**: since this whole function already runs on a background thread (an earlier, separate fix —
see `enqueue_for_review()`'s own docstring — moved the real network work off the citizen's request
thread), there's no cost to retrying the spans-lookup a few times with a short delay before giving
up. Added `_REVIEW_SPAN_LOOKUP_ATTEMPTS` (4) / `_REVIEW_SPAN_LOOKUP_RETRY_DELAY_SECONDS` (1.5s) to
`tracing.py`. Also hardened the mutation call itself to check its own response for GraphQL-level
errors and log a warning if present — it previously assumed success unconditionally, which would
have been a second, quieter way for this to silently do nothing (GraphQL reports a failed mutation
as HTTP 200 with an `errors` array, not an HTTP error status, so `raise_for_status()` alone can't
catch it).

**Testing**: 7 new tests added to `tests/test_langsmith_tracing.py` against a fake `httpx.Client`
double, covering immediate success, retry-until-found, give-up-after-max-attempts (no raise, no
bogus mutation call), and a GraphQL-error-in-mutation-response being logged. Full
`test_langsmith_tracing.py` + `test_ask_sarthi.py`: 116 passed, 1 skipped (pre-existing), no
regressions. See PR #75 for the deploy-and-verify-in-production follow-through.
