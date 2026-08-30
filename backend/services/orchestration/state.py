"""Shared graph state for the Ask Sarthi LangGraph orchestrator.

One `GraphState` flows through every node (see `graph.py`). It is a `TypedDict` (not a Pydantic
model) because LangGraph's `StateGraph` natively merges partial dict returns from each node into
the running state -- a Pydantic model would need an extra reducer step for no real benefit here,
since nothing in this state needs field-level validation (each field is already validated at its
*source* -- `AskSarthiRequest`, `ClassificationResult`, `LocationResolution` -- before it's
copied into the graph state as a plain value).

`total=False`: every field is optional. A field simply isn't present until the node that
produces it has run -- e.g. `intent` doesn't exist until `intent_node` runs, `sources` doesn't
exist unless the `rag_flow` node ran. Nodes read with `state.get(...)`, never `state[...]`, so a
not-yet-populated field never raises.

Deliberately excludes fields the spec's example list mentions but that this codebase doesn't
actually need as separate state: `user_id` (available via the `config["configurable"]["user"]`
side-channel, since it never changes mid-graph and every node that needs it can read it from
there -- see graph.py's `GraphDeps`), `location_confidence` (redundant with
`location_is_ambiguous` + `location_city`/`location_state` being present or not -- a fourth field
encoding the same information would just be another way for state to drift out of sync with
itself), `error` as a single field is kept, but no per-node error fields -- one place for "the
graph could not complete normally" is enough at this scope.
"""

from __future__ import annotations

from typing import Any, TypedDict


class ConversationTurnDict(TypedDict):
    role: str
    content: str


class GraphState(TypedDict, total=False):
    # --- input (input_processing_node) ---
    user_message: str
    original_language: str
    normalized_message: str
    input_type: str  # "text" | "voice" -- both already arrive as text by the time the graph
                      # runs (STT happens upstream, see backend/services/complaint_agent.py's
                      # _transcribe_chunks); this field records which one it was, for logging.
    conversation_id: str | None
    conversation_history: list[ConversationTurnDict]

    # --- image (optional; captioning happens upstream in ask_sarthi_service.py, same
    #     precedent as input_type's STT-happens-upstream note above) ---
    has_image: bool  # an image was attached at all, regardless of whether captioning succeeded
    image_description: str | None  # VisionService's best-effort caption, or None
    # Set (by input_processing_node) whenever a photo was validated and saved to disk THIS turn
    # (mirrors evidence_service.SavedFile field-for-field) -- flows into AskSarthiResponse.
    # photo_evidence for the frontend to echo back on the matching ConversationTurn, so a LATER
    # turn with no photo of its own can still recover and attach the SAME real file to a complaint
    # (see nodes.py's `_recover_photo_evidence_from_history` and schemas/ask_sarthi.py's
    # PhotoEvidenceRef for the full rationale).
    photo_evidence: dict[str, Any] | None
    # "TEXT" | "STT" | "IMAGE" | "IMAGE_STT" | "VOICE_ASSISTANT" | "IMAGE_VOICE_ASSISTANT" --
    # LangSmith metadata only (see graph.py's run_graph()), never read by routing/business logic.
    # "STT"/"IMAGE_STT" distinguish Mic-1-produced text from typed text (AskSarthiRequest.
    # was_voice_input); "IMAGE_VOICE_ASSISTANT" is Mic 2 with an attached image.
    input_mode: str
    vision_used: bool  # == has_image; named separately so a LangSmith filter doesn't need to
                        # know has_image is the same signal
    tts_used: bool  # this request's mode will attempt TTS (VOICE_ASSISTANT/IMAGE_VOICE_ASSISTANT)
                     # -- whether synthesis actually SUCCEEDED is separately visible via
                     # AskVoiceResponse.audio_base64, not duplicated into tracing (see §7 of
                     # docs/ask_sarthi_langsmith_observability.md)

    # --- classification (intent_node) ---
    intent: str  # QuestionIntent.value, e.g. "TYPE_A_COMPLAINT"
    service_category: str | None  # ServiceCategory.value, or None
    out_of_scope_service: str | None  # "ELECTRICITY" | "NEW_SERVICE_CONNECTION" | None
    requests_new_connection: bool  # see intent_classifier.ClassificationResult's own docstring

    # --- location (location_node) ---
    location_city: str | None
    location_state: str | None
    location_source: str  # "text" | "gps" | "conversation_history" | "citizen_home_ward" | "none"
    location_is_ambiguous: bool
    location_ambiguous_candidates: list[str]
    # True only when the citizen gave an EXPLICIT location signal (the "Use current location"/
    # "Select location" UI, or typed free text passed as RequestContext.location_text) that still
    # didn't resolve to anywhere recognizable -- e.g. picking a ward whose name isn't a real city
    # the location gazetteer knows. Distinct from location_source == "none" alone, which is also
    # true when the citizen simply never gave any location signal at all -- see
    # clarification_flow_node's default branch, which uses this field to show an honest
    # "I couldn't recognize that" message instead of repeating the exact same first-ask question.
    location_explicit_signal_unresolved: bool
    # DISTINCT from the above: true when the citizen never used the explicit location field at
    # all, but their MESSAGE TEXT names a real-sounding place (e.g. "...in Pune?") this app simply
    # has no gazetteer entry for -- the "Pune fallback" bug's own honest-message case (see
    # location_extractor.py's `looks_like_it_names_an_unrecognized_place`). Kept separate from
    # `location_explicit_signal_unresolved` so clarification_flow_node can give it its own, more
    # accurate wording -- "couldn't recognize that as a location" reads oddly for a real,
    # well-known place, unlike a genuinely unresolved explicit reply (gibberish, a typo, ...).
    location_message_names_unresolved_place: bool

    # --- routing (route_intent conditional edge) ---
    route: str  # "complaint" | "rag" | "status" | "clarification" | "out_of_scope"
    needs_clarification: bool
    clarification_reason: str | None  # "category" | "location" | "status_number" | None

    # --- RAG flow results ---
    retrieved_chunks: list[dict[str, Any]]  # serialized ScoredChunk-shaped dicts
    sources: list[dict[str, Any]]  # serialized Citation-shaped dicts
    verification_status: str | None
    insufficient_knowledge: bool
    answer_was_llm_generated: bool
    # Real Sarvam cost/usage for this turn's answer_generation LLM call, in Indian Rupees (see
    # answer_generation_service.py's AnswerGenerationService.generate() docstring for the exact
    # rate) -- surfaced here (rather than staying only inside the Phoenix span, see
    # observability/tracing.py) so ai_request_log_repository.record_ai_request() can persist it
    # onto AiRequestLog, for the Admin AI Monitoring page's own cost column (see routes/admin.py).
    # None on every path that didn't call the LLM this turn: fallback, cache hit, or a flow that
    # never reaches rag_flow_node at all (greeting, out-of-scope, complaint creation, ...).
    ai_cost_inr: float | None
    ai_model_name: str | None
    ai_total_tokens: int | None

    # --- complaint flow results ---
    complaint_id: int | None
    complaint_data: dict[str, Any] | None
    worker_assignment: dict[str, Any] | None
    # P0 SAFETY FIX: explicit complaint-workflow state, set by complaint_flow_node so it's visible
    # in logs/tracing/tests rather than only implicit in `routed_to`/`follow_up_*`. One of "NONE"
    # (no complaint-shaped signal this turn), "DRAFT" (category and/or location still missing),
    # "AWAITING_CONFIRMATION" (category+location resolved, but the citizen has not yet explicitly
    # confirmed -- create_complaint() has NOT run), "AWAITING_LOCATION_CHANGE" (the citizen picked
    # "Change location" on the confirmation prompt and is being asked which ward/area to use
    # instead -- see complaint_flow_node's own location-change handling), "CONFIRMED" (explicit
    # confirmation received this turn, complaint created), or "CANCELLED" (explicit cancellation
    # received, OR a citizen-picked replacement location was rejected for belonging to a different
    # city than their own saved one -- see the same handling). This state is *derived* fresh each
    # request from `conversation_history` (see complaint_flow_node's own docstring) -- there is
    # still no server-side session/checkpointer; this field only makes that per-request derivation
    # visible, it is not itself persisted between requests.
    complaint_workflow_state: str

    # --- status flow results ---
    # (status text is written straight to response_text -- no dedicated field needed beyond that)

    # --- final response (every flow node sets these; response_generation_node only logs) ---
    response_text: str
    response_language: str
    follow_up_required: bool
    follow_up_question: str | None
    follow_up_options: list[str]
    # Same length/order as `follow_up_options`, translated into `response_language` for DISPLAY
    # only -- see nodes.py's `_localize_options` for why `follow_up_options` itself always stays
    # the canonical English text (clicking a button still sends that back, not this). Absent
    # (not just empty) whenever a node didn't populate it -- e.g. the dynamic ambiguous-location
    # city-name options, deliberately left untranslated (see that call site's own comment).
    follow_up_options_labels: list[str]
    routed_to: str  # "RAG" | "COMPLAINT_CREATED" | "COMPLAINT_STATUS_API" | "NONE_OUT_OF_SCOPE" |
                     # "NONE_CLARIFICATION_NEEDED" | "NONE_GREETING" | "NONE_CAPABILITIES" | ...
    error: str | None

    # --- final response grounding (response_generation_node -- see that function's own docstring
    # for why this stays that node's name/id despite now also serving as GROUNDING #2) ---
    # PRODUCTION ARCHITECTURE UPGRADE: whether every deterministic final-response-grounding check
    # passed on the FIRST pass through that node. `grounding_checks_failed` names which ones
    # didn't (e.g. "unsafe_completion_claim", "complaint_id_without_created_routing") -- empty
    # when `grounding_passed` is True. Purely observational: by the time this field is set, the
    # response has ALREADY been corrected in place (see that node's own "replan via deterministic
    # recomputation" comment) -- nothing downstream branches on this field, it exists so LangSmith
    # traces/tests can see that a correction happened and why, not to gate further routing.
    grounding_passed: bool
    grounding_checks_failed: list[str]
    # How many bounded REPLAN attempts this node actually ran (0 = passed on the first check, no
    # correction needed). See response_generation_node's own `_MAX_GROUNDING_REPLANS` docstring
    # for why 1 is always sufficient, and enforced as a real bound rather than assumed.
    grounding_replan_count: int
