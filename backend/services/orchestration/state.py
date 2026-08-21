"""Shared graph state for the Ask Sarthi LangGraph orchestrator.

One `GraphState` flows through every node (see `graph.py`). It is a `TypedDict` (not a Pydantic
model) because LangGraph's `StateGraph` natively merges partial dict returns from each node into
the running state -- a Pydantic model would need an extra reducer step for no real benefit here,
since nothing in this state needs field-level validation (each field is already validated at its
*source* -- `AskJanMitraRequest`, `ClassificationResult`, `LocationResolution` -- before it's
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

    # --- image (optional; captioning happens upstream in ask_janmitra_service.py, same
    #     precedent as input_type's STT-happens-upstream note above) ---
    has_image: bool  # an image was attached at all, regardless of whether captioning succeeded
    image_description: str | None  # VisionService's best-effort caption, or None
    # "TEXT" | "STT" | "IMAGE" | "IMAGE_STT" | "VOICE_ASSISTANT" | "IMAGE_VOICE_ASSISTANT" --
    # LangSmith metadata only (see graph.py's run_graph()), never read by routing/business logic.
    # "STT"/"IMAGE_STT" distinguish Mic-1-produced text from typed text (AskJanMitraRequest.
    # was_voice_input); "IMAGE_VOICE_ASSISTANT" is Mic 2 with an attached image.
    input_mode: str
    vision_used: bool  # == has_image; named separately so a LangSmith filter doesn't need to
                        # know has_image is the same signal
    tts_used: bool  # this request's mode will attempt TTS (VOICE_ASSISTANT/IMAGE_VOICE_ASSISTANT)
                     # -- whether synthesis actually SUCCEEDED is separately visible via
                     # AskVoiceResponse.audio_base64, not duplicated into tracing (see §7 of
                     # docs/ask_janmitra_langsmith_observability.md)

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

    # --- complaint flow results ---
    complaint_id: int | None
    complaint_data: dict[str, Any] | None
    worker_assignment: dict[str, Any] | None
    # P0 SAFETY FIX: explicit complaint-workflow state, set by complaint_flow_node so it's visible
    # in logs/tracing/tests rather than only implicit in `routed_to`/`follow_up_*`. One of "NONE"
    # (no complaint-shaped signal this turn), "DRAFT" (category and/or location still missing),
    # "AWAITING_CONFIRMATION" (category+location resolved, but the citizen has not yet explicitly
    # confirmed -- create_complaint() has NOT run), "CONFIRMED" (explicit confirmation received
    # this turn, complaint created), or "CANCELLED" (explicit cancellation received). This state
    # is *derived* fresh each request from `conversation_history` (see complaint_flow_node's own
    # docstring) -- there is still no server-side session/checkpointer; this field only makes that
    # per-request derivation visible, it is not itself persisted between requests.
    complaint_workflow_state: str

    # --- status flow results ---
    # (status text is written straight to response_text -- no dedicated field needed beyond that)

    # --- final response (every flow node sets these; response_generation_node only logs) ---
    response_text: str
    response_language: str
    follow_up_required: bool
    follow_up_question: str | None
    follow_up_options: list[str]
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
