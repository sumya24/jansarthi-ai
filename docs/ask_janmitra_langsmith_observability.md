# Ask Sarthi — LangSmith Observability

**Status: implemented and tested.** This document covers the observability layer added around
the already-complete LangGraph orchestrator (`docs/ask_janmitra_orchestration.md`) and RAG
pipeline (`docs/ask_janmitra_rag_architecture.md`), both unchanged by this phase. Nothing about
routing, retrieval, complaint creation, or the database schema for complaints/workers/users was
touched — this phase only adds a way to *observe* that pipeline, plus a small local metrics table
the Admin dashboard reads from.

**Since this was written, a second tracing backend was added alongside LangSmith: Arize Phoenix
(self-hosted, local-only so far) — see [`OBSERVABILITY.md`](OBSERVABILITY.md) for that one, plus
the real ₹ cost tracking, Admin AI Monitoring page, and alerts built on top of both backends.
[`PHOENIX_TRACING_PLAN.md`](../PHOENIX_TRACING_PLAN.md) has the full round-by-round history behind
those decisions. `tracing.py` writes to both backends; nothing below about LangSmith itself
changed.**

```
Frontend  ->  FastAPI  ->  LangGraph  ->  Intent / Location  ->  Flow Router  ->  RAG / Complaint / Status  ->  Response
                              |
                              v
                      LangSmith tracing (observability only)
```

## 1. Why LangSmith

The LangGraph orchestrator (`backend/services/orchestration/`) already had text-log
observability (`graph.py`'s `run_graph()` logs each node's name/latency). That's enough to debug
from a terminal, but gives no structured, queryable, per-request view of what a specific citizen's
question actually did — which route it took, how long RAG retrieval took vs. the LLM call, what
the retrieved chunks' relevance scores were, whether the request errored. LangSmith is purpose-
built for exactly that, and is the natural fit given LangGraph is already the orchestration engine
(LangSmith is built by the same team, for tracing LangChain/LangGraph runs specifically).

## 2. Architecture: where LangSmith sits

LangSmith is an **observability layer only** — it sits alongside the existing pipeline, never in
front of or inside it:

- **PostgreSQL remains the operational source of truth** for complaints, workers, users,
  assignments, and application state. Nothing about this integration changes that.
- **ChromaDB remains the vector knowledge store.** LangSmith never stores or serves RAG content.
- **LangGraph remains the orchestration engine**, unchanged (see `docs/ask_janmitra_orchestration.md`).
- **LangSmith only *observes*** what LangGraph/RAG/the LLM call already did — it cannot influence
  routing, retrieval, or any application decision. If LangSmith is down, misconfigured, or simply
  not set up, the application behaves *identically* (see §7).

## 3. What is traced

One LangSmith trace per `/ask-janmitra` request, structured as:

```
ask_janmitra_graph (root span)
  ├── rag_retrieval        (only for the RAG flow)
  ├── answer_generation    (only when retrieval found something to answer from)
  └── complaint_creation   (only for the complaint flow)
```

| Span | Created in | Captures |
|---|---|---|
| `ask_janmitra_graph` | `orchestration/graph.py`'s `run_graph()` | The citizen's question (redacted, see §6), language, input type, turn count on the way in; on the way out: intent, `routed_to`, service category, verification status, `insufficient_knowledge`, `follow_up_required`, complaint id, the generated answer (redacted), and total latency. Errors (if the graph raised) are recorded before re-raising. The root run's `metadata` also carries, added for the multimodal/voice upgrade: `input_mode` (`"TEXT"` \| `"STT"` \| `"IMAGE"` \| `"IMAGE_STT"` \| `"VOICE_ASSISTANT"` \| `"IMAGE_VOICE_ASSISTANT"` — `"STT"`/`"IMAGE_STT"` mean the text came from Mic 1, `AskJanMitraRequest.was_voice_input`, not that any transcription happens inside the graph itself), `has_image`/`vision_used` (bool, identical signal, `vision_used` kept as its own key so a LangSmith filter reads naturally), and `tts_used` (bool — this request's mode *will attempt* TTS; whether synthesis actually succeeded is separately visible via `AskVoiceResponse.audio_base64` in the application response, not duplicated into tracing). All four are purely categorical/boolean — never the image itself, its caption, or any audio (see §7). |
| `rag_retrieval` | `orchestration/nodes.py`'s `rag_flow_node()` | The query (redacted), service category, city/state filter in; result count, top relevance score, `insufficient_knowledge`, and the reason out. |
| `answer_generation` | same | The question (redacted), target language, number of context chunks in; whether the LLM actually generated the answer (`answer_was_llm_generated`) vs. the raw-excerpt fallback, and the answer itself (redacted) out. |
| `complaint_creation` | `orchestration/nodes.py`'s `complaint_flow_node()` | Service category and language in; the new complaint's id/status out, or the error if creation failed. |
| `vision_processing` | `ask_janmitra_service.py`'s `ask_with_image()`/`ask_voice()` | `{"has_image": true}` in; `caption_produced` (bool) and `caption_length` (int) out -- never the caption text itself or the image. Real span duration, not folded into the graph's own timing -- this is where the vision model's real, measured latency (see docs/... performance notes) actually shows up. |
| `speech_to_text` | `ask_janmitra_service.py`'s `ask_voice()` | `segment_count` in; `transcript_length` and the transcript itself (redacted) out, or the error if every chunk failed. |
| `text_to_speech` | same | `answer_length` in; `audio_produced` (bool) out -- never the audio itself. |

Child-span nesting for image/voice requests genuinely matches the flow a reviewer would expect to
see in the LangSmith UI:

```
IMAGE:  ask_janmitra_graph (root) -> vision_processing -> [rag_retrieval/answer_generation OR complaint_creation]
VOICE:  ask_janmitra_graph (root) -> speech_to_text -> [rag_retrieval/... OR complaint_creation] -> text_to_speech
VOICE+IMAGE:  ask_janmitra_graph (root) -> speech_to_text -> vision_processing -> [...] -> text_to_speech
```

`vision_processing`/`speech_to_text`/`text_to_speech` are real children of the SAME
`ask_janmitra_graph` root run the RAG/complaint spans belong to, not a separate trace and not
just inferred from `input_mode` metadata. This required `run_graph()` to accept an already-started
root run (see its own docstring for exactly how ownership/ending is negotiated between it and the
service layer) -- `graph.py` exposes `root_run_inputs_and_metadata()`/`root_run_outputs()` so both
the internal (text/STT, no image) and external (image/voice) root-run lifecycles build the
identical inputs/metadata/outputs shape.

This is **manual, curated instrumentation** — see §6 for why it's deliberately not automatic
LangChain-callback tracing (`LANGCHAIN_TRACING_V2`/blanket `LANGSMITH_TRACING` autotracing), even
though LangGraph's compiled graph is a `Runnable` and would support that with zero code changes.

**Not separately traced**: individual LangGraph nodes other than the three above (intent
classification, location resolution, clarification, status lookup, out-of-scope) — these are
cheap, deterministic, non-LLM operations already covered by the root span's inputs/outputs and by
the pre-existing per-node text log (`run_graph()`'s `logger.info(...)` line, unchanged by this
phase). Sarvam speech-to-text/translation/summarization calls made by the *dedicated* complaint
form (`POST /complaints`, outside the Ask Sarthi/LangGraph path) are also not traced — this
integration's scope is the LangGraph pipeline shown in the diagram above, not every Sarvam call in
the app.

## 4. Configuration

All in `backend/config.py`, loaded from environment variables (see `.env.example`) — nothing is
hardcoded, and nothing is read from `os.environ` outside `config.py` (matching this codebase's
existing convention for every other external service).

| Variable | Default | Purpose |
|---|---|---|
| `LANGSMITH_TRACING` | `false` | Master on/off switch. Tracing only actually runs when this is `true` **and** an API key is set. |
| `LANGSMITH_API_KEY` | *(empty)* | Your LangSmith API key. Leave blank to keep tracing off regardless of `LANGSMITH_TRACING`. |
| `LANGSMITH_ENDPOINT` | `https://api.smith.langchain.com` | LangSmith API host — only change this for a self-hosted/EU instance. |
| `LANGSMITH_PROJECT` | `jansarthi-ai` | The LangSmith project traces are grouped under. |
| `LANGSMITH_TRACE_URL_TEMPLATE` | *(empty)* | Optional. Enables the Admin dashboard's "View Trace" link — see §5. |

## 5. Admin Monitoring

`AdminDashboard.tsx`'s new **AI Monitoring** section, backed by two endpoints
(`backend/routes/admin.py`, admin-role only):

- `GET /admin/ai-monitoring` — aggregate tiles: total/successful/failed requests, error rate,
  average latency, and a breakdown by route (RAG / complaint / status / out-of-scope /
  clarification).
- `GET /admin/ai-monitoring/requests` — the most recent requests, each with intent, route,
  latency, success/failure, and a "View Trace" link where available.

**Deliberately sourced from a local table, not from LangSmith.** Every `/ask-janmitra` call writes
one row to a new `ai_request_logs` table (`AiRequestLog` in `models.py`, populated by
`AskJanMitraService.ask()`) regardless of whether LangSmith tracing is enabled. The Admin
dashboard reads only this table — never the LangSmith API — so it keeps showing real numbers
whether LangSmith is fully configured, partially configured, or not set up at all (see §7). This
matches §4's architecture: LangSmith observes the pipeline, it does not become a dependency of
anything the app needs to keep working, including its own dashboard.

`langsmith_trace_id` is stored on each row purely as a pointer. **"View Trace" only appears when
`LANGSMITH_TRACE_URL_TEMPLATE` is configured** — see §6. No LangSmith API call happens on the
dashboard's read path even when trace links are shown; the URL is built by local string
substitution.

## 6. Finding a trace

Because the trace URL format includes your LangSmith organization/workspace id, which isn't
knowable from an API key alone, this integration doesn't call the LangSmith API to resolve a link
(that would be a network call on the admin dashboard's read path, and a second thing that could be
slow/down). Instead, **set `LANGSMITH_TRACE_URL_TEMPLATE` once**, and every subsequent request
automatically gets a working link:

1. Open any trace in your LangSmith project's UI and copy its URL, e.g.
   `https://smith.langchain.com/o/1a2b3c/projects/p/jansarthi-ai/r/9f8e7d6c-...`.
2. Replace the run/trace id at the end with the literal placeholder `{trace_id}`:
   ```
   LANGSMITH_TRACE_URL_TEMPLATE=https://smith.langchain.com/o/1a2b3c/projects/p/jansarthi-ai/r/{trace_id}
   ```
3. Restart the backend. Every AI Monitoring request row now links straight to its own trace.

Without this set, trace ids are still recorded (and shown as plain text) — they're just not
clickable.

## 7. Privacy and redaction

Reviewed field-by-field, per span, before deciding what's sent (see §3's table for what each span
actually carries):

- **Never traced, under any configuration**: passwords, password hashes, JWTs/auth tokens, API
  keys, raw phone numbers used for login, or full `User`/`Session` objects. The tracing code
  (`backend/services/observability/tracing.py`) only ever receives the small, explicit dicts
  `graph.py`/`nodes.py` build for it — never the raw `GraphState`, `RequestContext`, or ORM
  objects — so there's no path for a field this list didn't intend to leak by accident. This is
  also *why* this integration doesn't use LangChain's automatic callback-based tracing
  (`LANGCHAIN_TRACING_V2`): that would trace the LangGraph `Runnable`'s real inputs/outputs,
  including the full `GraphState` dict and `config["configurable"]` — which holds the live
  SQLAlchemy `Session`/`User` objects — with no per-field control.
- **Redacted before sending**: the citizen's question and the generated answer both pass through
  `tracing.redact_text()`, which masks email addresses and 7+ digit runs (covers phone numbers
  and most ID-like numbers) and caps length at 2000 characters.
- **Known limitation, stated honestly**: `redact_text()` is a regex filter, not NLU — it will not
  catch a name or street address written in ordinary prose (e.g. "My name is Priya, the streetlight
  outside 14 MG Road is broken"). If your deployment's Ask Sarthi traffic routinely includes
  such details, treat your LangSmith project as containing citizen PII for access-control purposes,
  the same as the application database itself.
- **Not PII, sent as-is**: intent, service category, route, verification status, location
  city/state (already a public geographic filter, not a citizen's home address), RAG relevance
  scores, latency, and source metadata (title/organization/URL — all public knowledge-base
  content, never citizen data). The multimodal/voice upgrade adds `input_mode` and `has_image` to
  this same category — both are fixed enum/boolean values, carrying no content.
- **Never traced, multimodal/voice upgrade**: raw image bytes/base64, the vision model's caption
  (`image_description`), raw audio bytes/base64, and the citizen's transcribed speech text are
  never passed to any span's `inputs`/`outputs`/`metadata` — confirmed by direct code review of
  every `tracing.*` call site in `orchestration/nodes.py`/`graph.py` (see backend/services/
  ask_janmitra_service.py's `ask_with_image()`/`ask_voice()`, which keep the caption/transcript
  entirely inside `GraphState`/the HTTP response, never inside a tracing call). A transcribed
  question that becomes part of `response_text` still passes through the same `redact_text()` the
  root span's `answer` output already used.

## 8. Failure behavior

Every function in `backend/services/observability/tracing.py` catches its own exceptions and
returns `None`/no-ops instead of raising — see that module's docstring. Concretely, none of the
following can happen because of a LangSmith problem:

- Missing/invalid `LANGSMITH_API_KEY` → `tracing.is_enabled()` is `False`, every trace call is a
  no-op, the app runs exactly as if this feature didn't exist.
- LangSmith API unreachable/timing out → the `langsmith` SDK itself batches/uploads on a
  background thread rather than blocking the request; this module's own try/except is
  defense-in-depth on top of that.
- The `langsmith` package missing or broken at import time → `tracing.is_enabled()` is `False`
  (checked at the module level), same as above.

`AiRequestLog` writes (the Admin dashboard's data, separate from LangSmith — see §5) are
independently best-effort: `ai_request_log_repository.record_ai_request()` catches and logs its
own exceptions rather than raising, so a database hiccup while writing this row cannot fail (or
mask the outcome of) the Ask Sarthi response that has already been produced.

Verified directly: `tests/test_ask_janmitra_tracing.py::test_ask_janmitra_endpoint_works_when_every_langsmith_call_raises`
exercises the real `/ask-janmitra` endpoint with `LANGSMITH_TRACING=true` and a LangSmith client
that raises on every call, and asserts a normal `200` response.

## 9. Testing

- `tests/test_langsmith_tracing.py` — the tracing module in isolation: configuration parsing,
  span start/end shape, redaction, and that every failure mode (missing config, missing package,
  client construction failure, API-call failure) is swallowed, never raised.
- `tests/test_ask_janmitra_tracing.py` — the same behavior exercised through the real LangGraph
  orchestrator and (for the RAG spans) the real `/ask-janmitra` endpoint: one root span per
  request, the root span reaching nodes via `config["configurable"]["trace_root"]`, error tracing
  on a node exception, RAG retrieval/answer-generation spans (including the "nothing found" case),
  and the full HTTP request/response cycle staying correct with a fully-failing LangSmith client.
- `tests/test_ai_monitoring.py` — the `AiRequestLog` repository (aggregation math, ordering,
  best-effort write-failure handling) and the `/admin/ai-monitoring*` endpoints (admin-only
  access, summary correctness, trace-URL template wiring).

Run just this feature's tests:
```
python -m pytest -q tests/test_langsmith_tracing.py tests/test_ask_janmitra_tracing.py tests/test_ai_monitoring.py
```
Run the full suite (unchanged tests plus the above) with `python -m pytest -q`.

## 10. Evaluators + Datasets/Experiments — RAG quality regression testing

`scripts/langsmith_rag_evaluation.py` uploads the same labeled dataset
`scripts/evaluate_rag_retrieval.py` already uses
(`data/rag_knowledge_base/test_questions/retrieval_evaluation_dataset.json`, 21 cases) as a real
LangSmith **Dataset**, runs the current production RAG pipeline (the real
`RagRetriever`/`AnswerGenerationService`, real ChromaDB, real Sarvam calls) against every case,
and scores two things per case as a LangSmith **Experiment**:

1. **`retrieval_correctness`** (deterministic) — did the pipeline correctly decide the question
   was answerable/unanswerable from the knowledge base?
2. **`groundedness`** (LLM-as-judge, via Sarvam) — for answerable cases only: is every claim in
   the generated answer actually supported by the retrieved context, or did the model add
   something not present in it? **This is the check flagged as missing during this integration**
   — the app's existing hallucination-prevention is entirely *upstream* of generation (intent-
   classifier out-of-scope detection, category+location metadata filtering, the measured 0.79
   relevance threshold, and a system prompt that forbids inventing facts/citations — see
   `backend/config.py`'s `RAG_EMBEDDING_RELEVANCE_THRESHOLD` comment and
   `answer_generation_service.py`'s docstring). Nothing previously scored the generated text
   itself *after* the fact; this evaluator does.

**Where to see it**: run `python scripts/langsmith_rag_evaluation.py`, then in the LangSmith UI go
to **Datasets & Experiments** → `janmitra-ask-janmitra-rag-eval`. Each run shows up as a new
experiment column — compare experiments over time (e.g. before/after a knowledge-base update or a
relevance-threshold change) to catch a regression instead of discovering it from citizen traffic.

This is a deliberately separate, manually-run script (like `evaluate_rag_retrieval.py` already
is) — not part of the test suite or CI, since it makes real Sarvam LLM calls (cost + latency) and
writes to your live LangSmith project.

## 11. Annotation Queue — knowledge-base-gap review

Every Ask Sarthi request where the pipeline genuinely couldn't answer — `insufficient_knowledge`
was true, or the intent classifier detected a known-but-unsupported service (`routed_to ==
"NONE_OUT_OF_SCOPE"`) — is a real citizen question the knowledge base doesn't cover yet. Those
traces are automatically added to a LangSmith **Annotation Queue** (see `graph.py`'s
`run_graph()`, `tracing.enqueue_for_review()`), so reviewing them turns into a
knowledge-base-improvement backlog instead of an invisible aggregate count on the AI Monitoring
dashboard.

The queue (name configurable via `LANGSMITH_REVIEW_QUEUE_NAME`, default
`jansarthi-ai-knowledge-gaps`) is created automatically on first use — no manual setup step.

**Where to see it**: LangSmith UI → **Annotation Queues** → `jansarthi-ai-knowledge-gaps`. Each
entry is a full trace (question, retrieval outcome, reason) ready for a reviewer to tag "add to
KB" vs. "genuinely out of scope."

Not a moderation queue and not blocking — adding a run to the queue happens after the citizen
already has their response; a queuing failure (LangSmith down, misconfigured) is swallowed the
same way every other `tracing.*` call is (see §8).

## 12. Prompt Hub — versioned prompts, without changing what the app loads

`scripts/push_prompts_to_langsmith.py` mirrors `prompts/ask_janmitra_system_prompt.txt` and
`prompts/ask_janmitra_answer_prompt.txt` into LangSmith's **Prompt Hub** as one
`janmitra-ask-janmitra-answer-prompt` prompt (system message + human message, exactly the two
messages `AnswerGenerationService.generate()` sends to Sarvam).

**Deliberately mirror-only.** `AnswerGenerationService` keeps reading the local `.txt` files via
`backend/config.py`'s `get_prompt()`, completely unchanged — nothing in the request path calls
LangSmith to fetch a prompt. Re-run the script after editing either file to push a new commit, so
LangSmith's own version history (diff commits, roll back, see when something changed) tracks every
revision — a visibility/experimentation layer, not a runtime dependency.

*Why not load prompts from LangSmith at runtime instead*: that would add a network call (or a
cache-invalidation problem) to the answer-generation hot path, for a capability — shipping a
prompt wording change without a code deploy — this project doesn't currently need. Worth
revisiting if that need actually shows up; not done speculatively here.

**Where to see it**: run `python scripts/push_prompts_to_langsmith.py`, then LangSmith UI →
**Prompts** → `janmitra-ask-janmitra-answer-prompt`. Test alternate wording in the **Playground**
there before ever touching the actual `.txt` files.

## 13. Admin alerts — sustained error rate / latency

A new in-app notification, reusing the existing `Notification` system (previously worker-only,
for complaint assignments): `ai_request_log_repository.check_and_fire_alerts()` looks at the most
recent 20 Ask Sarthi requests after every new one, and if either condition holds, notifies every
admin:

| Alert | Condition | Cooldown |
|---|---|---|
| `HIGH_ERROR_RATE` | ≥20% of the last 20 requests failed | 30 minutes |
| `HIGH_LATENCY` | average latency over the last 20 requests ≥10s | 30 minutes |

The cooldown (tracked in a new `ai_alert_states` table, one row per alert type) is what stops a
sustained problem from creating a fresh notification on every single request while it stays true
— it re-fires once the cooldown window has passed and the condition is still true.

**Deliberately computed from this app's own `AiRequestLog` table, not a LangSmith Automation
webhook.** Matches this doc's own §2 principle (PostgreSQL is the operational source of truth,
even for numbers LangSmith also has) and avoids needing an internet-reachable webhook endpoint
for something that only needs to notify admins already using this app. LangSmith's own
Automations feature (Monitoring → Automations in the UI) can *additionally* be configured for
the same kind of alert if you want LangSmith itself to page/Slack someone directly from trace
data — the two aren't mutually exclusive, this app's own alerts just don't depend on it being set
up.

**Where to see it**: the notification bell in the top bar (now shown for every role — previously
worker-only) — an admin sees a badge and a dropdown entry titled "High AI error rate" or "High AI
latency" with the measured rate/latency in the message. Best-effort like the rest of this
integration: `check_and_fire_alerts()` catches its own exceptions and never breaks the Ask
Sarthi response that triggered the check.

## 14. Manual setup steps (not automatable from this repo)

1. Create a LangSmith account and project at https://smith.langchain.com (or your self-hosted
   instance) if you don't already have one.
2. Generate an API key in LangSmith and set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY=...`
   in your `.env` (never commit real credentials — `.env.example` only documents the variable
   names).
3. Optionally set `LANGSMITH_PROJECT` to match an existing LangSmith project name (default:
   `jansarthi-ai`).
4. Optionally set `LANGSMITH_TRACE_URL_TEMPLATE` per §6 to enable the Admin dashboard's "View
   Trace" links.
5. Restart the backend. The next `/ask-janmitra` request should appear in your LangSmith project
   within a few seconds.
6. Optionally run `python scripts/langsmith_rag_evaluation.py` (§10) to populate Datasets &
   Experiments, and `python scripts/push_prompts_to_langsmith.py` (§12) to populate Prompt Hub —
   both are one-off/manually-re-run scripts, not part of app startup.
7. The Annotation Queue (§11) and admin alerts (§13) need no setup — they activate automatically
   once tracing/the app itself is running.

No code change is required for any of the above — everything is environment-variable driven (§4).
