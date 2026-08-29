# Ask Sarthi — LangGraph Orchestration Layer

**Status: implemented and tested.** This document covers the workflow-orchestration layer added
on top of the already-complete RAG/embeddings/ChromaDB foundation (see
`docs/ask_janmitra_rag_architecture.md`, unchanged this phase) and the pre-existing complaint/
worker-assignment system (`backend/services/complaint_agent.py`, `assignment_service.py`,
unchanged this phase). Nothing about embeddings, ChromaDB, the metadata schema, or the relevance
threshold was touched — this phase is purely about how a request is *routed* through the
already-working pieces.

## 1. Why LangGraph

Before this phase, `AskJanMitraService.ask()` was one Python method with nested if/else branches
deciding: TYPE_C → status lookup; out-of-scope → canned response; missing location → clarification;
else → RAG retrieval + answer generation. That worked, but every new routing case (this phase
adds: complaint creation, category clarification, multi-step clarification state) meant more
nested branching in one method. LangGraph's `StateGraph` makes the same decisions **explicit,
inspectable, and testable in isolation**: each decision is a named node or a named conditional
edge, or the routing logic itself (`_route_after_intent`, `_route_after_location`,
`_route_after_complaint` in `backend/services/orchestration/graph.py`) can be unit-tested as a
pure function with a plain dict, with no HTTP client, no database, no LLM call (see
`tests/test_orchestration_graph.py`).

## 2. What LangGraph does here

Provides `StateGraph` (the typed-state graph builder), conditional edges (`add_conditional_edges`,
routing to different nodes based on a function of the current state), and `.stream(...,
stream_mode="updates")` (used for per-node observability — see §15). This project uses LangGraph
purely as a **workflow orchestration engine**: build a graph once (`build_graph()`, cheap, pure
structure), then execute it once per request (`run_graph()`). No LangGraph agent, tool-calling
loop, or autonomous reasoning is used — see §17 for why, and what would change if that were
introduced later.

## 3. What LangChain does here

`langchain-core` is used for exactly one thing: `RunnableConfig`, the typed shape of the
`config` parameter every LangGraph node function receives (`langgraph` depends on
`langchain-core` directly — it is not an optional extra). This project's node functions read
per-request dependencies out of `config["configurable"]` (see `nodes.py`'s `GraphDeps`/
`RequestContext`). No LangChain prompt templates, chains, retriever wrappers, or tool interfaces
are used — `AnswerGenerationService` still calls the `sarvamai` SDK directly with plain
`.format()`-templated prompt files (unchanged from the RAG phase, see
`docs/ask_janmitra_rag_architecture.md`), and `RagRetriever`/`ChromaVectorStore` are still plain
Python classes, not LangChain retriever objects. This was a deliberate choice, not an oversight —
see §4.

## 4. Why LangChain is not the orchestrator (and isn't wrapped further than §3)

Two reasons, both concrete rather than stylistic:
1. **The spec's own instruction is explicit**: "LangChain is a TOOLKIT/FRAMEWORK... do not treat
   LangChain as the service layer." LangGraph is the orchestration engine; the actual service
   layer (`RagRetriever`, `AnswerGenerationService`, `ComplaintAgent`, `LocationExtractor`,
   `intent_classifier.classify()`) is this codebase's own existing, already-tested Python code —
   wrapping it in LangChain abstractions (a `Runnable` retriever, a `PromptTemplate`, an
   `LLMChain`) would add a translation layer with no behavior this project needs that plain
   Python doesn't already provide, and would risk subtly changing tested behavior (e.g.
   `AnswerGenerationService`'s specific fallback-to-raw-excerpt behavior, or `RagRetriever`'s
   specific VERIFIED-preference rerank) for no functional gain.
2. **Every "point" a LangChain abstraction would earn is already covered by this codebase's
   existing patterns**: structured output → Python dataclasses/Pydantic models already used
   throughout (`ClassificationResult`, `LocationResolution`, `RetrievalOutcome`); prompt
   templates → `.txt` files + `.format()`, already the established convention (`get_prompt()`,
   see `backend/config.py`); tool interfaces → plain method calls, since nothing here needs an
   LLM to *choose* which tool to call (see §17).

## 5. Graph nodes — what each one does

All node functions live in `backend/services/orchestration/nodes.py`; the compiled graph
structure is in `backend/services/orchestration/graph.py`.

| Node | Wraps (existing service) | Behavior |
|---|---|---|
| `input_processing` | — | Strips whitespace from the message; preserves the original untouched in `user_message`. Identity otherwise. |
| `language_detection` | — | Identity — `original_language` already arrives validated by the route. Seeds `response_language`. |
| `intent_classification` | `intent_classifier.classify()` | Unchanged classifier, wrapped. Sets `intent`, `service_category`, `out_of_scope_service`. |
| `location_resolution` | `LocationExtractor` | Same priority order as the pre-graph service: explicit `location_text` > location in the message text > GPS > conversation history. |
| `complaint_flow` | `ComplaintAgent`, `assign_next_worker`, `LocationResolver` | **New this phase** — see §9. |
| `rag_flow` | `RagRetriever`, `AnswerGenerationService` | Unchanged retrieval pipeline (see the RAG architecture doc), orchestrated here instead of inline. |
| `agent_flow` | `RagRetriever`, `AnswerGenerationService` | **Built later, see §17** — the same two services called once per detected category for a genuinely multi-category question, combined into one response. |
| `status_flow` | `Complaint` DB query | Unchanged complaint-number-regex + ownership-checked lookup — never touches RAG. |
| `clarification_flow` | — | Builds a follow-up question; which one depends on `clarification_reason` (category, location, or an ambiguous multi-city match). |
| `out_of_scope_flow` | — | The honest "I don't have that" response for a known-but-unsupported service. |
| `response_generation` | — | Terminal node — fills in any field a flow node didn't set, and is the last point observability logging captures (see §15). |

## 6. Shared graph state

`backend/services/orchestration/state.py`'s `GraphState` — a `TypedDict(total=False)`. Every
field is optional (present only once the node that produces it has run); every node reads with
`state.get(...)`. Deliberately excludes fields the spec's example list names but that don't earn
their own slot here: `user_id` (read from `config["configurable"]["ctx"].user` instead — it never
changes mid-graph, so it's request plumbing, not conversation state — see §11's `RequestContext`);
`location_confidence` (redundant with `location_is_ambiguous` + city/state being present or not).
See `state.py`'s own docstring for the full field list and the reasoning behind each inclusion/
exclusion.

## 7. Conditional routing

Three routing functions, each a **pure function of `GraphState`**, independently unit-tested
(`tests/test_orchestration_graph.py`):

```
_route_after_intent(state):
    TYPE_C_STATUS                    -> "status_flow"
    out_of_scope_service is set      -> "out_of_scope_flow"
    else                             -> "location_resolution"

_route_after_location(state):
    intent == TYPE_A_COMPLAINT       -> "complaint_flow"   (always -- it decides for itself
                                                              whether it still needs info, see §9)
    location ambiguous OR missing    -> "clarification_flow"
    >=2 categories detected          -> "agent_flow"        (see §17 -- a genuinely multi-category
                                                              question, checked via deterministic
                                                              keyword matching, never an LLM call)
    else                              -> "rag_flow"

_route_after_complaint(state):
    needs_clarification              -> "clarification_flow"
    else                              -> "response_generation"
```

Deterministic routing throughout — no LLM call decides which node runs next, per the spec's own
"do not use an LLM to perform simple routing if deterministic routing is sufficient."

## 8. RAG integration

`rag_flow_node` calls `RagRetriever.retrieve()` (unchanged) then `AnswerGenerationService.generate()`
(unchanged), and builds the same citation-from-metadata-only shape the pre-graph service built —
see `docs/ask_janmitra_rag_architecture.md` for everything about embeddings/ChromaDB/thresholds/
citations, none of which changed. The only thing that changed is *which* messages reach this node:
TYPE_B (service-information) questions, not TYPE_A (complaint-shaped) ones — see §9.

## 9. Complaint integration — the one deliberate behavior change this phase

**Before this phase**: a TYPE_A_COMPLAINT-classified message ("Street light not working near me")
was answered by RAG — Ask Sarthi could describe relevant civic-service information about the
issue, but could not act on it. Filing an actual complaint required the separate complaint form.

**This phase, confirmed with the user before implementing** (the alternative considered was
having `complaint_flow` only gather information and hand off to the existing form without ever
creating a complaint itself — rejected in favor of full creation, matching the spec's own worked
example in its §10, which uses a TYPE_A-shaped sentence as the complaint-flow example): TYPE_A now
routes to `complaint_flow_node`, which:

1. Determines the service category — from the current message's classification, or (new helper,
   `_recover_category_from_history`) by re-classifying prior user turns if the current one doesn't
   name one. If still unknown: `needs_clarification=True`, `clarification_reason="category"`.
2. Checks location (already resolved by the shared `location_resolution` node). If ambiguous or
   missing: `needs_clarification=True`, `clarification_reason="location"` (or `"location_ambiguous"`).
3. Once both are known, calls `ComplaintAgent.create_complaint()` — the SAME service the dedicated
   complaint form uses — with `text` only (no photo, no audio: Ask Sarthi is a JSON chat
   endpoint, not a multipart upload endpoint; a citizen who wants to attach a photo or record
   voice still uses the dedicated form, unchanged, per the "do not redesign the frontend"
   boundary).
4. Sets `complaint.ward` from the citizen's explicit location text (mirroring
   `routes/complaints.py`'s own `ward` form field exactly), additionally attempts the app's
   structured `LocationResolver.resolve_ward_by_text()`/`resolve_coordinates()` resolution (same
   calls that route already makes), then calls `assign_next_worker()` — completely unchanged
   assignment logic (see `assignment_service.py`, not touched this phase).
5. Returns the complaint ID, ward, and assignment status in the response
   (`AskJanMitraResponse.complaint_id`, a new field — see §22 below).

This necessarily changed the expected behavior of several previously-tested TYPE_A cases — see
`tests/test_ask_janmitra.py`'s updated assertions (queries changed to TYPE_B phrasing where the
test's actual purpose was to exercise RAG/citations, not complaint filing) and the final report
for the complete list.

## 10. Status integration

`status_flow_node` — byte-for-byte the same regex-based complaint-number extraction +
ownership-checked DB lookup the pre-graph `_answer_status_question` used. Routed to directly from
`_route_after_intent`, before location resolution even runs — a status question never touches
location, RAG, or complaint creation, matching the single hardest rule in this whole system
(unchanged): TYPE_C never touches RAG.

## 11. Location integration

`location_resolution` node uses `LocationExtractor` (RAG gazetteer: city/state, for RAG's
metadata filtering — unchanged from the RAG phase) for every intent except TYPE_C/out-of-scope.
`complaint_flow_node` additionally uses the app's own `LocationResolver` (state/district/ULB/ward
hierarchy — what `assign_next_worker` actually keys on) for ward-level resolution, via
`RequestContext` (a small dataclass carrying per-request `db`/`user`/`latitude`/`longitude`/
`location_text` through `config["configurable"]`, not folded into `GraphState` — see `nodes.py`'s
docstring for why: it's request plumbing, not conversation data). These are two genuinely
different location systems already present in this codebase before this phase (documented in
`location_extractor.py`'s own module docstring) — the graph doesn't unify them, it uses each for
the concern it already existed for.

## 12. Multi-turn clarification

Ask Sarthi remains a **stateless-server, client-resends-history** API (unchanged design
decision from the RAG phase — see `docs/ask_janmitra_rag_architecture.md`'s "why no server-side
conversation store"). "Multi-turn" means: turn N's response asks a clarifying question and ends;
turn N+1's request includes the updated `conversation_history`; the graph reruns from `START`,
and location/category recovery (both check `conversation_history` as a fallback, see §9/§11)
reconstruct what was already established. No LangGraph checkpointer (`MemorySaver`, etc.) is used
for cross-request persistence — it would only work single-process anyway, and would duplicate
information the client already resends, so it wouldn't add real capability over the tested
approach. Verified end-to-end with the spec's own 3-turn example
(`test_multi_turn_complaint_filing_category_then_location` in `tests/test_orchestration_graph.py`):
"I want to file a complaint." → "Streetlight." → "Use my current location." correctly recovers
the category from turn 2 while resolving location in turn 3, and files one complaint with the
right category.

## 13. Sarvam integration

Unchanged. `AnswerGenerationService` (RAG answers) and `ComplaintAgent`'s internal
`TranslationService`/`SummaryService`/`SarvamClient` (complaint creation) are called exactly as
before — the graph orchestrates *when* each is invoked, not *how*. No new Sarvam API key handling,
no duplicated client construction — `complaint_flow_node` receives an already-constructed
`ComplaintAgent` via `GraphDeps`, built once by `AskJanMitraService.__init__` the same way every
other dependency is.

## 14. Voice/text integration

Ask Sarthi's frontend page (`frontend-react/src/pages/AskJanMitra.tsx`) is **text-only** —
checked directly before this phase began (no `useAudioRecorder`/voice import exists in that file).
"Voice input" in this codebase exists only on the separate dedicated complaint form
(`useAudioRecorder.ts` → `ComplaintAgent._transcribe_chunks`), unchanged and untouched this
phase. `GraphState.input_type` (`"text"` | `"voice"`) exists structurally so a future
voice-transcribed message entering Ask Sarthi (transcribed upstream by the same STT pipeline
the complaint form already uses, then handed to the graph as text) has a field to record which
path it came from — this phase does not wire that frontend capability, since doing so would be a
frontend change beyond this phase's explicit scope ("do not redesign the frontend"). No
text-to-speech exists anywhere in this codebase (`SarvamClient` has no `synthesize`/`tts` method,
checked directly) — nothing here claims to "preserve" a voice-output pipeline that never existed.

## 15. Error handling

Every node either can't fail (pure state transforms) or wraps a call already designed to fail
gracefully:
- `complaint_flow_node`'s `ComplaintAgent.create_complaint()` call is wrapped in
  `try/except (ValueError, AIServiceError)` — an honest "I couldn't file that complaint right now"
  response (`routed_to="COMPLAINT_CREATION_FAILED"`), never a fabricated complaint ID (see
  `test_complaint_agent_failure_returns_honest_error_not_a_fake_complaint_id`).
- Ward/GPS resolution inside `complaint_flow_node` is wrapped in a broad `try/except Exception`
  (matching `routes/complaints.py`'s own equivalent) — a resolver failure never undoes an
  already-committed complaint creation.
- Any node exception that isn't caught locally propagates up through `run_graph()` (logged, then
  re-raised) to `AskJanMitraService.ask()` (no try/except — propagates further) to
  `routes/ask_janmitra.py`'s route-level `try/except Exception`, which converts it into a clean
  503 with a generic message — never a raw stack trace, API key, DB credential, or internal path
  reaches the client (unchanged from the RAG phase; see
  `test_location_resolver_failure_does_not_break_the_pipeline`).

## 16. Testing

- `tests/test_orchestration_graph.py` (19 tests): node-level unit tests (input/language/intent
  nodes, no DB/network), routing-function unit tests (all three `_route_after_*` functions as
  pure functions), graph-structure test (`build_graph()` produces the expected node set), the
  multi-turn 3-turn scenario end-to-end, the category-recovery helper in isolation, and the
  complaint-creation-failure error-handling case.
- `tests/test_ask_janmitra.py` (32 tests, updated this phase): every TYPE_A test whose actual
  purpose was RAG/citation behavior was changed to a TYPE_B phrasing (still tests RAG, now
  correctly avoiding complaint_flow); a new complaint-creation-and-worker-assignment test replaces
  the old "never creates a complaint" regression test (deliberately inverted, see §9); a new
  "doesn't create a half-filled complaint" test replaces it as the honest regression guard that's
  actually still true.
- `tests/test_ask_janmitra.py`'s `_FakeComplaintAgent` — mirrors the existing `fake_answers`
  pattern (no real Sarvam network call in tests), builds a real `Complaint` ORM row
  deterministically so `complaint_flow_node`'s downstream logic (ward assignment, response text)
  is exercised against genuine data, not a further mock.
- Full regression: 201/201 backend tests pass (up from 181 pre-this-phase: +19 new orchestration
  tests, +1 net from the TYPE_A test rewrite splitting one test into two). RAG-layer tests
  (`test_rag_vector_store.py`, 32 tests) untouched and still passing — nothing about embeddings/
  ChromaDB/thresholds changed.
- Three full validation cycles performed (pytest, RAG schema/source validation, TypeScript,
  frontend build, lint, Playwright against a freshly-restarted real backend each time) — see the
  final report for the one genuinely flaky (AI/network-latency, unrelated-to-this-phase) result
  found and confirmed transient.

## 17. The supervisor/multi-agent node (`agent_flow_node`)

**Built** (a later phase than the rest of this document) for exactly the case originally scoped
here: a genuinely multi-category question, e.g. a citizen reporting a flooded street, a blocked
drain, AND a broken streetlight in one message — one where the number of RAG queries genuinely
can't be predetermined by a single `service_category` field the way every other TYPE_B question
can.

**Still deliberately NOT an autonomous/reasoning agent** — no LLM decides which categories to
query, how many times, or in what order. `intent_classifier.py`'s `detect_multiple_categories()`
decides the category LIST, deterministically, via the same keyword-matching approach every other
routing decision in this graph already uses (see §7) — `_route_after_location` routes to
`agent_flow` only when 2+ categories are detected, otherwise the existing single-category
`rag_flow` runs exactly as before, unchanged. `agent_flow_node` itself just calls
`RagRetriever.retrieve()` + `AnswerGenerationService.generate()` once per detected category (the
same two services `rag_flow_node` already calls once) and combines the results into one response,
with a labeled section per category and a merged, deduplicated citation list. `insufficient_
knowledge` is only true if EVERY category came back with nothing usable — a citizen who reported
three issues and got two real answers plus one honest "don't have that" section still got real
help, unlike `rag_flow_node`'s single-category all-or-nothing case.

Deliberately narrower than `rag_flow_node` in two ways, not silently dropped: no `RagAnswerCache`
integration (each category's answer is a fresh `generate()` call every time) and no "new
connection" post-filter (out of scope for a multi-category message) — both straightforward to add
later if a real need shows up here specifically.

`detect_multiple_categories()` reuses `classify()`'s own `_CATEGORY_KEYWORDS`, with one
deliberate narrowing: `ROADS_POTHOLES` drops the bare "road" family keywords (kept for `classify()`
itself, where "first match wins" already handles the collision via check ordering — see that
dict's own STREETLIGHTS-vs-ROADS_POTHOLES comment). Without that narrowing, almost every
streetlight complaint that names the road it's on would look like a "streetlights + roads"
multi-category message, which it structurally is not — a real false-positive risk this narrowing
exists specifically to avoid, verified directly in `tests/test_intent_classifier.py`.

Built on top of Part 8's MCP tool wrapper (`backend/mcp_server.py`) only in the sense that both
now exist as real, callable entry points into the same underlying services — `agent_flow_node`
itself calls `RagRetriever`/`AnswerGenerationService` directly (in-process, same as every other
node), not through the MCP server, since there is no reason for an in-process LangGraph node to
make a network/subprocess round-trip to reach code that already lives in the same process.
