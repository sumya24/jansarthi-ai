# Add Arize Phoenix as a second, self-hosted tracing backend alongside LangSmith

**Status: planned, not yet implemented.** Kept here as a local reference copy of the approved plan
(originally written to `C:\Users\Asus\.claude\plans\snazzy-strolling-narwhal.md`).

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
