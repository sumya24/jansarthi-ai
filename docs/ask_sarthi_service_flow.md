# Ask Sarthi — Service Flow Structure + Selective LangChain Evaluation

**Status: implemented and tested.** This document covers the SERVICE FLOW + SELECTIVE LANGCHAIN
INTEGRATION phase, built on top of the already-complete LangGraph orchestration
(`docs/ask_sarthi_orchestration.md`) and RAG/embeddings foundation
(`docs/ask_sarthi_rag_architecture.md`) — neither was rebuilt or replaced. This phase's actual
work: (1) a genuine, evidence-based evaluation of where LangChain would add real value across
every flow, (2) a concrete database repository layer extracted out of the graph nodes, (3) one
real classifier bug fix found via the phase's own worked example, (4) tests and documentation.

## 1. Architecture map — Graph Node → Flow → Service → Database/External dependency

| Graph node | Flow | Service(s) called | DB / external dependency |
|---|---|---|---|
| `input_processing`, `language_detection` | (shared) | — | — |
| `intent_classification` | (shared) | `intent_classifier.classify()` | — |
| `location_resolution` | (shared) | `LocationExtractor` | RAG gazetteer (`chunks.json`, in-memory) |
| `rag_flow` | RAG | `RagRetriever` → `ChromaVectorStore` + `SentenceTransformerEmbeddingProvider`; `AnswerGenerationService` | ChromaDB (`data/rag_knowledge_base/chroma/`); Sarvam LLM |
| `complaint_flow` | Complaint | `ComplaintAgent` (transcription/translation/summary); `assignment_service.assign_next_worker()`; **new this phase:** `repositories/complaint_repository.py` | `complaints`/`users` tables (SQLite); Sarvam (translation/summary, inside `ComplaintAgent`) |
| `status_flow` | Status | **new this phase:** `repositories/complaint_repository.get_complaint_by_id()` | `complaints` table |
| `clarification_flow` | Clarification | — (pure state → response text) | — |
| `out_of_scope_flow` | Out-of-scope | — | — |
| `response_generation` | (shared, terminal) | — | — |

Nothing in this table is new infrastructure — every cell names a service that already existed
before this phase, except the repository functions (§7), which only relocate logic that was
previously inline in `status_flow_node`/`complaint_flow_node`.

## 2. RAG flow — LangChain evaluated, not adopted

The RAG flow's five candidate LangChain uses (per this phase's spec) were each evaluated against
what already exists:

| Candidate | Existing approach | Verdict |
|---|---|---|
| Prompt templates | `.txt` files + `str.format()`, via `get_prompt()` (`backend/config.py`) — this codebase's established convention, used by every LLM call site | **Not adopted.** `PromptTemplate` would wrap the same `.format()` call with no new capability. |
| Retriever interface | `RagRetriever` — category+location metadata filtering, relevance threshold, VERIFIED-preference rerank, all custom and tested (see `docs/ask_sarthi_rag_architecture.md`) | **Not adopted.** Wrapping in LangChain's `BaseRetriever` would risk losing/obscuring the custom rerank and threshold logic for no functional gain — nothing currently needs `RagRetriever` to be interoperable with a LangChain chain. |
| Structured output | Not used for RAG answers — the answer is free-text prose; citations are built from Chroma metadata directly, **never** from the LLM (a hard requirement, see the RAG doc's citation section) | **Not applicable.** There is nothing structured to extract from the LLM here by design — this is the whole reason citations can never be fabricated. |
| Context formatting | `"\n\n---\n\n".join(context_chunks)` | **Not adopted.** Trivial; no abstraction earns its cost here. |
| LLM invocation | `sarvamai` SDK's `client.chat.completions()` directly (`AnswerGenerationService`) | **Not adopted** — see §4 for why, including the live feasibility test that informed this decision. |

**No RAG code was changed this phase.** Per the spec's own instruction ("do not change the
existing RAG architecture unless a real issue is discovered") and the evaluation above finding no
real issue, `RagRetriever`, `ChromaVectorStore`, `SentenceTransformerEmbeddingProvider`, and
`AnswerGenerationService` are byte-for-byte unchanged.

## 3. Complaint flow — LangChain evaluated with a live test, declined based on measured evidence

The spec explicitly floats one candidate use: LLM-based structured extraction of
`{category, issue, location, description}` from unstructured complaint text, as a complement to
(not necessarily a replacement for) the existing keyword classifier. This was evaluated for
real, not just discussed:

**Feasibility check**: Sarvam's REST API (`POST {base_url}/v1/chat/completions`, confirmed by
reading the `sarvamai` SDK's own request-construction code) is OpenAI-request-shape-compatible,
so `langchain_openai.ChatOpenAI(base_url="https://api.sarvam.ai/v1", api_key=..., model=
"sarvam-105b")` connects successfully and `.with_structured_output(...)` returns real, schema-
validated Pydantic objects — the *mechanism* works.

**Quality check (the part that matters)**: two live test runs, each asking the model to classify
5 short complaint-style sentences into a `Literal["WASTE_SANITATION", "WATER_DRAINAGE",
"ROADS_POTHOLES", "STREETLIGHTS"]` (or `null`) field, with the four options listed in a different
order in each run (to rule out simple list-position bias as the sole explanation):

| Input | Expected | Run 1 result | Run 2 (reordered) result |
|---|---|---|---|
| "The bin outside my flat hasn't been emptied in a week" | WASTE_SANITATION | WATER_DRAINAGE ✗ | WATER_DRAINAGE ✗ (low confidence) |
| "There's a big crater in the road outside my building" | ROADS_POTHOLES | STREETLIGHTS ✗ | ROADS_POTHOLES ✓ |
| "I want to book a train ticket" | *null* (out of scope) | STREETLIGHTS ✗ (high confidence) | STREETLIGHTS ✗ (high confidence) |
| "The pole light near the bus stop stopped glowing at night" | STREETLIGHTS | STREETLIGHTS ✓ | ROADS_POTHOLES ✗ (high confidence) |
| "Sewage is backing up into my kitchen sink" | WATER_DRAINAGE | STREETLIGHTS ✗ | STREETLIGHTS ✗ (medium confidence) |

**4/5 wrong in both runs**, with different specific errors each time (ruling out a pure
positional-bias explanation — this is broader unreliability), and most importantly: **the one
genuinely out-of-scope input ("book a train ticket") was confidently (both runs: "high"
self-reported confidence) assigned a real civic category in both runs**, rather than correctly
returning `null`. For a system that routes a citizen's complaint to a specific municipal
department/worker queue, a *confidently wrong* classification is a worse failure mode than the
existing keyword classifier's honest "no category matched, ask the citizen" behavior — precision
matters more than recall here, and this integration measurably has neither.

**Decision: declined.** `complaint_flow_node`'s category resolution remains 100% the existing
deterministic path: current-message classification → conversation-history recovery (§ see
`docs/ask_sarthi_orchestration.md` §9) → clarification if still unknown. No LLM output ever
reaches complaint routing. `langchain-openai` (installed locally only for this evaluation) was
**not** added to `requirements.txt` — it ships in no production code path, matching the spec's
"do not add LangChain simply for technology count."

This is reported as a first-class result, not a failure to complete the phase: a rigorous,
evidence-based "no" is exactly what "evaluate whether LangChain provides useful abstractions... if
useful, introduce it selectively" asks for when the evidence says no.

## 4. Status / Location / Clarification flows — LangChain intentionally not used

Unchanged from the orchestration phase and re-confirmed correct here: status lookup is a plain
DB query (now via the repository layer, §7); location resolution is deterministic
gazetteer/hierarchy matching (no distance calculation an LLM should ever be trusted with);
clarification is plain state-driven templated text. None of these involve free-form natural-
language *understanding* in a way an LLM would add value to over what regex/exact-match already
does correctly and fast, matching the spec's explicit list of what LangChain should **not** be
used for (CRUD, SQL, worker distance, deterministic routing, simple status lookup, location
hierarchy lookup).

## 5. A real bug found via the spec's own worked example

Testing this phase's own example query, "What documents do I need for a water connection?",
against the (unchanged) intent classifier surfaced a genuine, measurable false negative: it
classified as `TYPE_A_COMPLAINT` (the "something is wrong" default) instead of
`TYPE_B_SERVICE_INFO`, because `_SERVICE_INFO_KEYWORDS` had "documents required" but not the more
natural question phrasing a citizen would actually type. **Fixed** by adding "what documents",
"which documents", "documents do i need", "documents needed" to the existing English keyword list
(`backend/services/intent_classifier.py`) — the same kind of small, evidence-based, additive fix
already established in this codebase (see that file's `_NEW_CONNECTION_KEYWORDS` for the earlier
precedent). Verified: the exact query now classifies `TYPE_B_SERVICE_INFO` /
`WATER_DRAINAGE`, and `test_classifier_measured_accuracy_against_existing_labeled_test_files`
still reports its measured accuracy without regression (re-run as part of the full suite, see §9).

## 6. Database service layer

**Before this phase**: `status_flow_node` and `complaint_flow_node` ran `db.query(Complaint)...`/
`db.query(User)...`/`setattr(...)`/`db.commit()`/`db.refresh()` directly inline.

**This phase**: `backend/repositories/complaint_repository.py` — `get_complaint_by_id()`,
`get_user_by_id()`, `save_complaint_location()`. Deliberately thin (no speculative CRUD methods,
no generic query builder) — every function exists because a graph node actually calls it. The
layering is now:

```
LangGraph node  ->  Business service (ComplaintAgent, assign_next_worker)  ->  Repository  ->  DB
```

**Never**:

```
LangGraph node  ->  LLM  ->  generated SQL  ->  DB
```

No LLM constructs or influences a database operation anywhere in this codebase — every query in
`complaint_repository.py` is a fixed, reviewable `db.query(Model).filter(...)` statement, the same
pattern already used throughout `backend/services/`. This boundary is a design choice worth
stating explicitly (per the spec's own emphasis), not an accident of not having gotten around to
an LLM-based query layer.

## 7. Worker assignment architecture — unchanged

`assignment_service.assign_next_worker()` (ward-based candidate lookup, stable ordering,
rejection-aware skip logic) is untouched — still pure, deterministic Python, called by
`complaint_flow_node` exactly as it was in the orchestration phase. No LLM or agent is involved
in worker selection; distance/eligibility is plain SQL + Python comparison, matching the spec's
explicit "Do NOT ask an LLM to calculate distance."

## 8. Final architecture

```
                              USER
                               |
                               v
                            FASTAPI
                               |
                               v
                    LANGGRAPH ORCHESTRATOR
                               |
                               v
                  INTENT + LOCATION + STATE
                               |
                               v
                      CONDITIONAL ROUTER
        +----------------+----------------+-----------------+------------------+
        |                |                |                 |                  |
        v                v                v                 v                  v
    RAG FLOW       COMPLAINT FLOW     STATUS FLOW     CLARIFICATION FLOW  OUT-OF-SCOPE FLOW
        |                |                |                 |                  |
   (LangChain          ComplaintService      StatusRepository   -> USER, graph        (canned,
    evaluated,          (ComplaintAgent)     (complaint_          resumes on next       honest
    not used --             |                repository)         turn                  response)
    see §2)                 v
        |               DATABASE
        v                   |
    ChromaDB                v
        |             WorkerAssignment
        v              (deterministic)
     SARVAM
```

## 9. Why each technology

- **Why LangGraph?** Explicit, inspectable, independently-testable routing for a request that has
  five genuinely different outcomes depending on intent/location/completeness — see
  `docs/ask_sarthi_orchestration.md` §1 for the full reasoning (unchanged this phase).
- **Why LangChain (`langchain-core` only, in production)?** `RunnableConfig` — a direct
  `langgraph` dependency for passing per-request context to nodes. Nothing else, after genuine
  evaluation (§2, §3) found no other use that outperforms what already exists.
- **Why normal Python services?** Every deterministic decision (routing, status lookup, location
  matching, worker assignment) is faster, free, precise, and fully unit-testable as plain code —
  an LLM adds latency, cost, and (measured, §3) real error risk with no offsetting benefit for
  these specific decisions.
- **Why ChromaDB?** Unchanged — see `docs/ask_sarthi_rag_architecture.md` §4.
- **Why a database repository layer?** Isolates the fixed, reviewable set of queries a graph node
  needs from the orchestration/business-decision logic around them — and makes the "never LLM →
  SQL" boundary an explicit, visible seam in the codebase rather than an implicit convention.
- **Why Sarvam?** Unchanged — the only AI provider this project uses, for translation/STT/
  summarization (complaint intake) and RAG answer prose generation. Its OpenAI-compatible REST
  shape (confirmed while evaluating LangChain, §3) is a useful fact for a future integration, not
  something this phase needed to exploit.

## 10. Testing

- `tests/test_complaint_repository.py` (7 tests): the new repository functions, in isolation.
- `tests/test_service_flow_scenarios.py` (7 tests): the spec's own six worked examples, each
  asserted to reach the flow named — including the honest "off-topic message never fabricates a
  civic answer" case (§ scenario 6, since this codebase's out-of-scope detector is specifically a
  *known-but-unsupported-service* detector, not a general off-topic classifier — reported
  accurately rather than claiming coverage that doesn't exist).
- Full regression: 215/215 backend tests (201 pre-phase + 14 new this phase).
