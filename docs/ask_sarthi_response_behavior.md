# Ask Sarthi — Expected Response Behavior (Original Spec, Now Superseded)

**Status: HISTORICAL.** This document was written *before* Ask Sarthi existed, as forward-looking
architecture guidance ("no chat UI, retrieval endpoint, embeddings, or vector store exist yet")
so the data/schema decisions made at the time wouldn't have to be reworked once that phase
started. All of that has since been built, tested, and is live — see
[`docs/ask_sarthi_rag_architecture.md`](ask_sarthi_rag_architecture.md),
[`docs/ask_sarthi_orchestration.md`](ask_sarthi_orchestration.md), and
[`docs/ask_sarthi_service_flow.md`](ask_sarthi_service_flow.md) for what's actually
implemented today; those three are the current source of truth. Kept here as the original design
intent, not rewritten to match the current implementation — the location-clarification flow in
particular has since gone through several real, live-reported bug fixes not reflected below (see
`backend/services/orchestration/nodes.py`'s own extensive docstrings on `_resolve_location` and
`_should_skip_home_ward_fallback` for the actual, current behavior — including that an unresolved
location now gets an honest "I couldn't recognize that as a location" message rather than
repeating the same clarification question a second time). See
`data/rag_knowledge_base/citation_examples/citation_examples.md` for worked examples of the
citation format this spec requires — that part is still accurate.

## 1. Intent classification (first step, before any retrieval)

Every incoming question must be classified into one of three buckets before anything else
happens:

| Bucket | Examples | Handled by |
|---|---|---|
| **TYPE A — Complaint/issue** | "Pothole near my house", "Street light not working" | RAG retrieval over `data/rag_knowledge_base/chunks/chunks.json` |
| **TYPE B — Service/information request** | "I want a new water connection", "What documents are required?" | RAG retrieval — **but see §13**: much of this bucket currently has no data to retrieve (see `test_questions/type_ab_multilingual_questions.json`'s `TYPE_B_SERVICE_INFO` entries) |
| **Status/tracking** | "What's the status of my complaint?", "Has my application been approved?" | **NOT RAG** — this is the existing `complaints` table / a future live government API (see §13) — RAG must never attempt to answer this from static knowledge |

Misclassifying a status question as TYPE A/B and answering it from static knowledge would be a
direct hallucination risk (a citizen's actual complaint status is dynamic, per-record data that
no knowledge-base chunk can possibly contain) — this classification step is a hard requirement,
not a nice-to-have.

## 2. Location requirement flow

Every RAG chunk in this data set is scoped to a `state`/`district`/`city`/`municipality` (see
`backend/schemas/rag_knowledge.py`'s `Chunk` model) — there is no location-agnostic answer for
any TYPE A or TYPE B question, because the underlying facts (SLA, department, contact) genuinely
differ per city (see `TYPEAB_LOC_STATE_ONLY_EN` in the test file: Mohali and Patiala have
different streetlight SLAs even within the same state).

Required flow:
1. If the question includes an unambiguous location (city name, or a resolved GPS fix — see §3),
   proceed directly to retrieval scoped to that location.
2. If the location is missing or ambiguous (state given but not city; multiple cities plausible),
   **ask the citizen to clarify** ("Which city/area are you in?") — never silently default to one
   city's figures or blend multiple cities' answers into one.
3. If "use current location" is available and the citizen has explicitly permitted it, resolve
   coordinates first (via the existing `backend/services/location_resolver.py`) and use the
   resolved city — but see §3's integration gap.

## 3. Known integration gap: GPS resolution is not wired to RAG retrieval yet

`LocationResolver.resolve_coordinates()` (built in the location-migration phase) resolves GPS to
a `state`/`district`/`city` **name**, matched against the app's own `states/districts/ulbs`
tables. The RAG knowledge base's `state`/`district`/`city` fields are separate, denormalized text
fields on each `Chunk` — **nothing today matches a resolved GPS city name against RAG chunk
metadata.** This is an explicit, documented gap (see
`TYPEAB_LOC_CURRENT_LOCATION_EN` in the test file), not an oversight to paper over: building that
matching step is retrieval-layer work, explicitly out of scope for this phase.

## 4. Insufficient-knowledge handling

If retrieval finds no chunk matching the resolved location + service category (the common case —
most cities have zero RAG coverage at all, see `docs/location_migration_plan.md`'s master-data
limitations), the response **must state that plainly**:

> "I don't have official information for [service] in [location] yet."

Never: fall back to a different city's figures, average multiple cities together, or present a
SYNTHETIC record's representative estimate without disclosing it's an estimate (see §5).

## 5. Citation display format

Every answer sourced from a chunk must show, in this order:

1. The answer itself.
2. **Source title** (`chunk.source_title`).
3. **Organization** — for VERIFIED chunks, the real publishing body (`source_organization` on the
   parent `KnowledgeRecord`, not currently duplicated onto `Chunk` — retrieval-layer work would
   need to either add it or join back to the record); for SYNTHETIC chunks, always exactly
   `"JanSarthi AI — synthetic representative record (not sourced from an official document)"`.
4. **URL** — `chunk.source_url` if present (VERIFIED only; always `None` for SYNTHETIC, never
   fabricated) — rendered as `"According to [source title]..."` with a clickable link only when
   `source_url` is not null.
5. **Verification badge** — VERIFIED or SYNTHETIC, always shown, never omitted, never blended.

See `data/rag_knowledge_base/citation_examples/citation_examples.md` Examples 1–6 for the exact
worked phrasing this maps to, including the SYNTHETIC-disclosure case (Example 4) and the
VERIFIED-vs-SYNTHETIC side-by-side case (Example 5).

## 6. Official contact number / application link / form download

Per `type_ab_multilingual_questions.json`'s findings: these must be surfaced **only when actually
present on the matched record** (`contact_information`, and `source_url` when it happens to be a
live portal rather than just the source PDF — see `TYPEAB_B_OFFICIALWEBSITE_EN`). There is no
dedicated "application form URL" field anywhere in the schema today (see §8 of the audit) — a
"where do I download the form" question must say that isn't available, not point at the parent
citizen-charter PDF as if it were the form itself.

## 13 (spec section, matches the audit's numbering) — RAG vs. live-API responsibility split

| Question shape | Answered by |
|---|---|
| "What documents are required?" / "What is the SLA?" / "Which department handles this?" | RAG (static, changes rarely) |
| "What is the status of my complaint #1234?" | The existing `complaints` table via the app's own API — **never RAG** |
| "Has my new-connection application been approved?" | A future live government/utility API — does not exist yet, and is explicitly out of scope this phase (§13/§19 of the audit request) |

These three must remain architecturally separate consumers of different data sources — RAG
knowledge base, the app's transactional database, and (later) live external APIs — never merged
into one lookup, so that a future live-API integration doesn't require reworking how static
knowledge is retrieved, and vice versa.
