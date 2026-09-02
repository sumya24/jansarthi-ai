# Ask Sarthi — Phoenix Observability, Cost Tracking, and AI Monitoring

*Written so it makes sense whether or not you already write code.*

> Part of the JanSarthi AI documentation set. Start at [`README.md`](../README.md) for the big
> picture. This document is the sibling of
> [`ask_sarthi_langsmith_observability.md`](ask_sarthi_langsmith_observability.md) — that one
> covers LangSmith, this one covers **Arize Phoenix**, the second tracing backend, plus everything
> built on top of both: real ₹ cost tracking, the Admin AI Monitoring page, and the alerts that
> watch it. See [`PHOENIX_TRACING_PLAN.md`](../PHOENIX_TRACING_PLAN.md) at the repo root for the
> full round-by-round history behind every decision here, including dead ends.

---

## 1. Why a second tracing backend at all

LangSmith's free tier caps out at 5,000 traces/month. That ran out — twice, on two different
accounts — mostly from local development traffic (measured directly: ~93 requests/day on a dev
machine vs. ~20/day in real production) sharing the same quota as real citizens. **Arize Phoenix**
is a genuinely free, self-hosted alternative with no such cap, so it runs alongside LangSmith
rather than replacing it: either backend can be down, misconfigured, or simply switched off without
affecting the other, or the citizen's actual request.

**Current default**: LangSmith is `LANGSMITH_TRACING=false` in local development — Phoenix alone
covers local tracing. LangSmith is reserved for production, so its quota isn't spent on development
traffic. Flip it to `true` locally only for a deliberate, one-off check.

---

## 2. Two Python environments, on purpose — don't merge them

Phoenix's real server package (`arize-phoenix`) runs from its own isolated virtual environment,
`.phoenix-venv/` (gitignored), **never** the main project environment. This was tested directly,
not assumed: installing `arize-phoenix` on top of this project's pinned `requirements.txt` forces
**Starlette 0.41 → 1.6** (a full major-version jump) and **FastAPI 0.115 → 0.141** — both sit
directly underneath this app's own auth/CSRF middleware, so that's a real compatibility risk. Two
lightweight client packages the app actually imports, `arize-phoenix-otel` and
`arize-phoenix-client`, stay in the main environment via `requirements.txt` — confirmed separately
that neither pulls in anything heavy.

```powershell
# One-time setup for both environments — see setup.ps1 at the repo root, does this automatically.
python -m venv .phoenix-venv
.\.phoenix-venv\Scripts\python.exe -m pip install arize-phoenix

# Start the Phoenix server (separate terminal, stays running):
.\.phoenix-venv\Scripts\python.exe -m phoenix.server.main serve
# Browse traces at http://localhost:6006
```

## 3. Production access control — the app's own admin role, not a separate password

Phoenix has no login of its own — reachable directly at `localhost:6006` in local dev, where
that's fine (nobody outside the machine can reach it). In production, its UI sits behind Caddy at
`/phoenix-ui/*`, and needs real access control: real citizen traffic, cost, and question/answer
text would otherwise sit behind an open public URL.

The first version of this gated it with a single fixed HTTP Basic Auth username/password. That
fell apart the moment there's more than one real admin: either everyone shares the one login (no
way to tell who did what, no way to revoke just one person), or someone has to hand-provision and
track a separate Caddy credential per admin forever, disconnected from the app's own user table.

**What's actually built instead**: Caddy's `forward_auth` directive asks the *backend itself*,
per request, "is this a real, currently logged-in admin?" — `GET /admin/phoenix-auth-check`
(`backend/routes/admin.py`). Whoever holds this app's real `admin` role gets Phoenix access
automatically; losing that role removes it the same way — no separate credential to create, sync,
or revoke anywhere. The browser's own `access_token`/`refresh_token` cookies ride along on Caddy's
forwarded request as plain, unconfigured header forwarding (the same thing that happens on any
proxied request) — so an admin already logged into the app in that browser reaches Phoenix's UI
directly, with no separate password prompt at all.

**What happens on each real outcome** (`phoenix_auth_check()`'s own three branches):
- **No session at all** (never logged in, or an expired one) — a real HTTP 302 redirect to
  `/login`, so the visitor lands on the app's actual login page rather than a bare error. This
  deliberately does NOT call `Depends(require_role("admin"))` the way every other admin route
  does -- `forward_auth` copies back *whatever* this endpoint returns on any non-2xx status,
  verbatim, so a plain 401 would show as raw JSON instead of a real way forward. One known,
  accepted limitation: this does not auto-return the visitor to Phoenix after they log in --
  Phoenix isn't a React Router route the app's existing "return to where you came from" login
  logic (`Login.tsx`'s `from` state) can target, since it's served by Caddy directly, not the SPA.
  They land on their normal dashboard and open Phoenix's URL again -- one extra click, not
  engineered around, to avoid touching the existing, working login-redirect code for every other
  page over a small convenience.
- **A real session, but not an admin** (a citizen or worker) — a plain 403. Rare in practice
  (this URL is only ever going to be typed/bookmarked by an admin).
- **A real, current admin** — 204, no body. The only case `forward_auth` treats as "allowed"; the
  original request proceeds to Phoenix exactly as if nothing were in front of it.

**Verified live, all four real cases, end to end** (not assumed): a real `caddy` binary
(standalone, ~50MB, no Docker needed) run locally against a copy of the real Caddyfile with
Docker's internal service names swapped for `localhost`, fronting the real backend and a real
Phoenix instance. No session -> 302 to `/login`; logged in as a citizen -> 403; logged in as a
real admin -> 200 (Phoenix's actual dashboard loads); same admin session, second visit -> still
200, no re-prompt. This same lightweight (no Docker) technique is the fastest way to sanity-check
any future `deploy/Caddyfile` change before it goes anywhere near production.

## 4. Configuration (`.env`)

| Variable | Meaning |
|---|---|
| `PHOENIX_TRACING` | `true`/`false` — off unless explicitly enabled, same pattern as `LANGSMITH_TRACING`. In **production only**, `.github/workflows/cd.yml`'s deploy step sets this to `true` on the server's `.env` itself on every deploy (same upsert pattern it already uses for `BACKEND_IMAGE`/`FRONTEND_IMAGE`) — no manual server edit needed there. Local dev still defaults to `false` and is set by hand in your own `.env`. |
| `PHOENIX_COLLECTOR_ENDPOINT` | Where the backend sends spans, e.g. `http://localhost:6006/v1/traces` |
| `PHOENIX_PROJECT_NAME` | Groups this app's traces in Phoenix's UI (default `jansarthi-ai`) |
| `PHOENIX_TRACE_URL_TEMPLATE` | Optional — builds a direct "View Phoenix trace" link on the Admin AI Monitoring page |

## 5. What gets traced — two layers, showing up as two separate traces per request

Any one real Ask Sarthi request produces **two** distinct entries in Phoenix (and in LangSmith),
not a duplicate charge or a bug — two different views of the same single event:

1. **`ask_sarthi_graph`** — this app's own hand-built spans (`backend/services/observability/
   tracing.py`): `rag_retrieval`, `answer_generation`, `response_translation`, `text_to_speech`,
   `speech_to_text`, `vision_processing`, `final_response_grounding`. This is the one carrying real
   ₹ cost, model name, and token counts — it's what the "Cost by model" panel (§7) reads.
2. **`LangGraph`** — automatically generated by the underlying LangGraph/LangChain framework
   itself (via `openinference-instrumentation-langchain`, wired in `tracing.py`), showing every
   internal node the request actually passed through: `input_processing`, `language_detection`,
   `intent_classification`, `location_resolution`, `clarification_flow`, `response_generation`,
   plus the `_route_after_*` conditional-edge decisions. Pure debugging detail, carries no cost
   data — added specifically so Phoenix shows the same level of detail LangSmith already got for
   free from its own native LangChain integration.

## 6. Keeping the two tracing backends genuinely independent

`start_root_run()`/`start_child_run()`/`end_run()` in `tracing.py` create a Phoenix span
*unconditionally*, before ever touching LangSmith. A real bug shipped here once: when LangSmith was
disabled, these functions returned `None` for the whole run, and every downstream call treated
`None` as "no tracing at all" — silently dropping the Phoenix span that had already been opened.
Fixed with a `_PhoenixOnlyRun` stand-in object, returned instead of `None` whenever Phoenix is
active but there's no real backing LangSmith run — see that class's own docstring in `tracing.py`
for the full story. Worth knowing before touching either function again: **a run object might be a
real LangSmith `_RunTree`, a `_PhoenixOnlyRun`, or `None` — check with `_is_real_langsmith_run()`
before assuming which.**

## 7. Real ₹ cost tracking — the "Cost by model" panel

Every Sarvam-adjacent call this app makes gets tagged with its real cost in Indian Rupees, computed
from Sarvam's own published pricing (chat completion, translation, TTS billed per character, STT
billed per second of audio — see `answer_generation_service.py`/`nodes.py`/
`ask_sarthi_service.py`'s own cost constants). Gemini and the local vision fallback are tagged
`model_name` only — genuinely free, no cost to report.

Phoenix's own "Top models by cost" dashboard widget is hard-capped at showing 4 models at a time
(confirmed via GraphQL schema introspection — no limit/count argument exists at all), which would
silently hide whichever model has the smallest volume. Rather than live with that cap, the Admin AI
Monitoring page has its **own** "Cost by model" section (`tracing.get_model_cost_summary()`) that
queries Phoenix's spans directly and always shows all 5 real models: request count, real ₹ cost (or
"Free"), and a small usage meter per model (relative to whichever model is busiest *right now* —
not a fixed target, so it re-bases itself automatically as usage shifts).

## 8. Sessions, retention, and review annotations

- **Sessions**: `conversation_id` (generated client-side, persisted per chat) is set as the root
  span's OpenInference session id — Phoenix's Sessions tab groups a whole back-and-forth
  conversation together, not just isolated single requests.
- **Retention**: Phoenix's trace retention was found set to unlimited (`maxDays: 0`) — the direct,
  confirmed root cause of an earlier incident where Phoenix's own server process grew to 2.6GB of
  RAM and had to be manually restarted. Set to 30 days via the `patchProjectTraceRetentionPolicy`
  GraphQL mutation; applies instance-wide (all 3 projects on a local Phoenix instance share one
  retention policy). This is a setting stored in Phoenix's own database, not this app's config — it
  needs re-applying if Phoenix's data is ever reset (e.g. a fresh production deployment).
- **Review annotations**: mirrors LangSmith's Annotation Queue (an admin can review real citizen
  questions the knowledge base couldn't answer). Phoenix's closest equivalent is a per-span
  annotation (`createSpanAnnotations`) — `_phoenix_enqueue_for_review()` tags the matching span
  `needs_review` whenever `insufficient_knowledge` or `routed_to == "NONE_OUT_OF_SCOPE"` fires (same
  trigger as the LangSmith side, see `graph.py`). Two real timing bugs found here, both confirmed
  directly against production rather than guessed:
  1. Phoenix batches span exports, so querying for the just-ended span immediately afterward found
     nothing until a `force_flush(timeout_millis=2000)` was added right before the lookup.
  2. Even after that fix, the annotation still never actually appeared in production — confirmed
     via a direct GraphQL check that `spanAnnotationNameCounts` stayed empty despite real
     out-of-scope questions firing this path many times. Root cause: `force_flush()` only
     guarantees the span was *exported*, not that Phoenix's own backend has finished
     *ingesting/indexing* it yet — the lookup query came back 200 OK ~4 seconds after the span
     ended, but the span wasn't in the result set (confirmed the same span WAS queryable moments
     later). Since this whole function already runs on a background thread with nothing waiting on
     it, it now retries the lookup up to `_REVIEW_SPAN_LOOKUP_ATTEMPTS` times (1.5s apart) before
     giving up, and the mutation itself now checks its own response for GraphQL-level errors
     (which come back as HTTP 200, so `raise_for_status()` alone never caught them).

## 9. Admin alerts — "High AI latency" / "High AI error rate"

After every real Ask Sarthi request, `ai_request_log_repository.check_and_fire_alerts()` looks at
the most recent **20** requests (the same window size as the Recent Requests table's default page,
deliberately) and checks two real, live-computed numbers — not mocked, not estimated:

- **"High AI latency"** fires if the *average* latency across those 20 is **≥ 10 seconds**.
- **"High AI error rate"** fires if **≥ 20%** of those 20 failed.
- Either won't re-fire for the same problem more than once per **30-minute** cooldown.

Clicking either notification navigates straight to the Admin AI Monitoring page. That page exposes
the same real threshold (`latency_alert_threshold_ms` on the summary endpoint, not duplicated as a
separate frontend constant) and uses it to bold+highlight any individual request at or above the
line, with a click-to-sort Latency column header — so an admin can go from "an alert fired" to
"here are the actual slow requests" in two clicks, not a manual table scan.

## 10. Testing

```bash
pytest tests/test_langsmith_tracing.py tests/test_ask_sarthi_tracing.py tests/test_ai_monitoring.py -v
```

Every function in `tracing.py` is fail-open by design and tested that way: a broken/misconfigured
backend (either one) must never raise, never block a citizen's real request, and never affect the
other backend. Tests cover the `_PhoenixOnlyRun` stand-in path specifically (LangSmith disabled but
Phoenix enabled, and LangSmith's own call failing outright) alongside the existing "both disabled"
and "both enabled" cases.

## 11. Likely interview questions about this part of the project

**"Why two tracing backends instead of just picking one?"** — LangSmith's free tier ran out from
local dev traffic sharing the same quota as production; rather than pay for LangSmith or lose
observability locally, Phoenix (free, self-hosted) runs alongside it. Either can fail independently
without losing all observability.

**"Why keep Phoenix in a separate Python environment instead of just installing it normally?"** —
tested directly: it forces a major-version bump on Starlette, which this app's own middleware sits
on top of. A real, measured compatibility risk, not caution for its own sake.

**"How do you compute AI cost per request?"** — Sarvam's real published per-token/per-character/
per-second pricing, computed at request time and attached to each span's own attributes — not
estimated after the fact, and not from Phoenix's own (capped) dashboard widgets.

**"What happens if Phoenix is completely down?"** — nothing breaks for a citizen. Every tracing
call in `tracing.py` is wrapped so a Phoenix failure is caught, logged, and ignored; the actual
Ask Sarthi response the citizen sees is entirely unaffected either way.
