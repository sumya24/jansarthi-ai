"""Request/response contracts for POST /ask-janmitra.

Field names/shapes deliberately mirror frontend-react/src/lib/ragTypes.ts's existing
`SourceRecord`/`AskJanMitraResponse` interfaces (authored by a separate frontend session ahead of
this backend) wherever they overlap, so wiring the real API to that existing UI is closer to a
drop-in than a remap — see that file's own docstring, which states the same intent in reverse.
"""

from pydantic import BaseModel, Field

from backend.services.intent_classifier import QuestionIntent


class PhotoEvidenceRef(BaseModel):
    """A reference to a photo that's already been validated and written to disk (see
    evidence_service.SavedFile, which this mirrors field-for-field) -- threaded through
    `conversation_history` so a LATER turn with no photo of its own (e.g. a typed "yes, submit
    it") can still attach the SAME real, already-saved file to the eventual complaint, instead of
    the file being silently orphaned once the turn that uploaded it ends.

    LIVE-REPORTED REQUEST: a citizen who attaches a photo, then answers a follow-up
    clarification ("Report a problem"), then confirms ("yes, submit it") -- three separate
    requests -- expects the SAME photo to end up on the complaint, matching how the dedicated
    "Report an Issue" form behaves (single request, so this never came up there). This backend
    has no server-side session (see ConversationTurn's own docstring); the file itself IS already
    saved (ask_janmitra_service.py's `_process_image()` writes it via the same
    `evidence_service.validate_and_write()` every photo upload in this app uses, regardless of
    whether a complaint is ever created from that turn) -- what was missing was a way to
    RE-FIND that already-saved file on a later turn. This is that reference, round-tripped as
    plain DATA (never re-derived from human-readable `content` text) exactly like
    `complaint_workflow_state` already is."""

    filename: str
    original_name: str
    content_type: str
    size: int


class ConversationTurn(BaseModel):
    """One prior turn in a multi-turn Ask Sarthi exchange. The caller (frontend) resends the
    full history with each request — this API is stateless server-side (see
    docs/ask_janmitra_rag_architecture.md's "why no server-side conversation store" note), which
    keeps this phase's scope to the retrieval pipeline itself rather than also building session
    storage."""

    role: str  # "user" | "assistant"
    content: str
    # BUG FIX (live Marathi validation): optional, explicit echo of THIS turn's own
    # AskJanMitraResponse.complaint_workflow_state (see that field's own docstring) when `role`
    # is "assistant" -- lets the caller round-trip Sarthi's own complaint-flow state as DATA
    # instead of the backend having to re-derive "was the last turn a confirmation prompt?" by
    # pattern-matching `content`'s human-readable, LLM-translated text (see orchestration/
    # nodes.py's `_awaiting_confirmation`/`_last_turn_invites_complaint_reply`, which now check
    # this field FIRST). Optional and defaults to None specifically so an older/unaware caller
    # that only ever sent {role, content} keeps working exactly as before, via the same
    # marker-text fallback this field is meant to make unnecessary once adopted.
    complaint_workflow_state: str | None = None
    # Explicit echo of THIS turn's own AskJanMitraResponse.photo_evidence (when `role` is
    # "assistant") -- see PhotoEvidenceRef's own docstring for the exact gap this closes.
    photo_evidence: PhotoEvidenceRef | None = None


class AskJanMitraRequest(BaseModel):
    question: str = Field(min_length=1)
    language: str = "en"
    latitude: float | None = None
    longitude: float | None = None
    # Explicit location the citizen typed/picked (e.g. via a "select city" clarification step) —
    # distinct from a location mentioned inline in `question`'s own text; checked first since
    # it's the most deliberate signal (see ask_janmitra_service.py's location-resolution order).
    location_text: str | None = None
    conversation_history: list[ConversationTurn] = Field(default_factory=list)
    # True when `question`'s text came from Mic 1 (useSpeechToText.ts filling the input, possibly
    # then hand-edited) rather than being typed from scratch -- purely a LangSmith/observability
    # signal (see orchestration/graph.py's root-run metadata "input_mode": "TEXT" vs "STT"), never
    # read by any routing/business logic. Defaults False so older/unaware clients behave exactly
    # as before this field existed.
    was_voice_input: bool = False


class Citation(BaseModel):
    """Matches frontend-react/src/lib/ragTypes.ts's SourceRecord exactly (see that file) — built
    directly from Chunk metadata, never from LLM output (see answer_generation_service.py's
    docstring for why)."""

    source_id: str
    source_title: str
    source_organization: str
    source_url: str | None
    source_type: str
    verification_status: str
    geographic_scope: str


class LocationInfo(BaseModel):
    city: str | None = None
    state: str | None = None
    source: str = "none"  # "text" | "gps" | "conversation_history" | "citizen_home_ward" | "none"
    is_ambiguous: bool = False
    ambiguous_candidates: list[str] = Field(default_factory=list)


class AskJanMitraResponse(BaseModel):
    answer: str
    intent: QuestionIntent
    service_category: str | None = None
    language: str
    location: LocationInfo | None = None
    sources: list[Citation] = Field(default_factory=list)
    # Overall status label for the answer: "VERIFIED" if every source is VERIFIED, "SYNTHETIC" if
    # every source is SYNTHETIC, "MIXED" if both appear, None if there are no sources at all
    # (insufficient-knowledge / status-routed / out-of-scope responses).
    verification_status: str | None = None
    follow_up_required: bool = False
    follow_up_question: str | None = None
    follow_up_options: list[str] = Field(default_factory=list)
    # Same length/order as `follow_up_options`, translated into `language` for DISPLAY only --
    # `follow_up_options` itself always stays the canonical English text a click sends back (see
    # nodes.py's `_localize_options` docstring for why the two must not be merged into one field).
    # None whenever the backend didn't produce localized labels for this turn (e.g. no follow-up
    # at all, or the dynamic ambiguous-location city-name options, deliberately left
    # untranslated) -- the frontend falls back to `follow_up_options` itself in that case.
    follow_up_options_labels: list[str] | None = None
    insufficient_knowledge: bool = False
    # Which subsystem actually produced `answer` — lets the frontend (and tests) verify TYPE_C
    # never got its answer from RAG, per this phase's hard requirement.
    routed_to: str = "RAG"  # "RAG" | "COMPLAINT_CREATED" | "COMPLAINT_STATUS_API" |
                            # "NONE_OUT_OF_SCOPE" | "NONE_CLARIFICATION_NEEDED"
    answer_was_llm_generated: bool = False
    # Set only when routed_to == "COMPLAINT_CREATED" (see the LangGraph orchestrator's
    # complaint_flow node, backend/services/orchestration/nodes.py) — a TYPE_A complaint-shaped
    # message with enough information (service category + location) now files a real complaint
    # through the existing ComplaintAgent/assign_next_worker services, the same way the dedicated
    # complaint form does, and reports the resulting ID back here so the frontend/citizen can
    # look it up (e.g. via the existing GET /complaints or a future TYPE_C status question).
    complaint_id: int | None = None
    # BUG FIX (live Marathi validation): explicit complaint-flow state for THIS response, mirrored
    # from the graph's own internal `GraphState.complaint_workflow_state` (see that field's own
    # docstring in orchestration/state.py for the full value list: "DRAFT" | "AWAITING_
    # CONFIRMATION" | "CONFIRMED" | "CANCELLED", or None when this turn wasn't complaint-shaped
    # at all). A caller that resends this value on the matching ConversationTurn lets Sarthi
    # recognize its own pending-confirmation state as DATA rather than by re-parsing this
    # response's own (possibly non-English, LLM-translated, non-deterministically-phrased)
    # `answer` text on the next turn -- see ConversationTurn.complaint_workflow_state's own
    # docstring for the full rationale and the live bug this closes.
    complaint_workflow_state: str | None = None
    # Set whenever a photo was validated and saved to disk THIS turn (see PhotoEvidenceRef's own
    # docstring) -- a caller that resends this value on the matching ConversationTurn lets a LATER
    # turn with no photo of its own (e.g. "yes, submit it") still attach the SAME real file to the
    # complaint it eventually creates.
    photo_evidence: PhotoEvidenceRef | None = None


class AskVoiceResponse(AskJanMitraResponse):
    """Response for POST /ask-janmitra/voice (the voice-to-voice assistant) — everything
    AskJanMitraResponse already has, plus the citizen's own transcribed speech (so the overlay
    can show a real "you said" transcript, not a guess) and the AI's spoken reply as real
    synthesized audio (see backend/services/sarvam_client.py's synthesize_speech()).
    `audio_base64` is None only when TTS itself failed (a real, honest failure, never faked) —
    the frontend still has `answer` as text to fall back to display in that case."""

    question: str
    audio_base64: str | None = None
    audio_format: str = "wav"
