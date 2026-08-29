"""Orchestrates the full Ask Sarthi pipeline via the LangGraph orchestrator in
`backend/services/orchestration/` -- intent -> location -> conditional routing -> (RAG retrieval
-> answer generation -> citations) OR (complaint creation + worker assignment) OR (complaint-
status lookup) OR (clarification) OR (out-of-scope) -> structured response.

This class is now a thin adapter around `orchestration.graph.run_graph()`: it builds the graph
once (cheap, pure structure), builds the shared `GraphDeps` once (the actually-expensive parts --
the Chroma collection, the embedding model -- are unchanged from before this phase), and on every
`ask()` call builds a per-request `RequestContext` + initial `GraphState`, invokes the graph, and
translates the final state back into `AskJanMitraResponse`. See
`backend/services/orchestration/graph.py`'s module docstring for the full node/edge diagram and
`docs/ask_janmitra_orchestration.md` for the architectural writeup -- this docstring only covers
what's still true about this class's own responsibilities (the public API surface).

Source routing (unchanged rule, now enforced by the graph's edges rather than this class's own
if/else): TYPE_C (personal complaint status) is answered EXCLUSIVELY from the existing
`complaints` table — it never touches the RAG retriever. TYPE_B (service info) is answered
EXCLUSIVELY via RAG retrieval. TYPE_A (complaint) now creates a real complaint via the existing
`ComplaintAgent`/`assign_next_worker` services (a deliberate behavior change made this phase, see
`orchestration/nodes.py`'s module docstring) — it never touches RAG or the status lookup.
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import UploadFile
from sqlalchemy.orm import Session

from backend.config import settings, to_bcp47
from backend.models import User
from backend.repositories import ai_request_log_repository
from backend.schemas.ask_janmitra import (
    AskJanMitraRequest,
    AskJanMitraResponse,
    AskVoiceResponse,
    Citation,
    ConversationTurn,
    LocationInfo,
    PhotoEvidenceRef,
)
from backend.services.answer_generation_service import AnswerGenerationService
from backend.services.complaint_agent import ComplaintAgent
from backend.services.embedding_provider import SentenceTransformerEmbeddingProvider
from backend.services.evidence_service import SavedFile, validate_and_write
from backend.services.location_extractor import LocationExtractor, RagGazetteer
from backend.services.location_resolver import LocationResolver
from backend.services.observability import tracing
from backend.services.orchestration.graph import build_graph, root_run_inputs_and_metadata, root_run_outputs, run_graph
from backend.services.orchestration.nodes import GraphDeps, RequestContext
from backend.services.orchestration.state import GraphState
from backend.services.rag_retriever import RagRetriever
from backend.services.audio_duration import get_audio_duration_seconds
from backend.services.sarvam_client import AIServiceError, SarvamClient
from backend.services.stt_service import transcribe_chunks
from backend.services.translation_service import TranslationService
from backend.services.vector_store import ChromaVectorStore
from backend.services.vision_service import VisionService, VisionServiceError

logger = logging.getLogger(__name__)

_LANGUAGE_NAMES = {code: info["name"] for code, info in settings.SUPPORTED_LANGUAGES.items()}

# Real Sarvam pricing for text-to-speech (confirmed against docs.sarvam.ai/api/getting-started/
# pricing, checked 2026-08-27): Rs30/10K characters for bulbul:v3 (billed per character, same as
# translation -- see orchestration/nodes.py's _SARVAM_TRANSLATE_COST_PER_CHAR_INR for the same
# reasoning: reported in real Indian Rupees, not USD, since Phoenix's dashboard hardcodes a "$"
# regardless of the number's real currency). Billed on the input (spoken) text length -- the one
# number known before the call is made, same approximation as translation's own cost.
_SARVAM_TTS_COST_PER_CHAR_INR = 30 / 10_000

# Real Sarvam pricing for speech-to-text (same pricing page, same date): Rs30/hour of audio for
# the basic saaras:v3 transcription this app uses (no diarization/translation add-on). Needs a
# real audio DURATION, not just byte count -- see audio_duration.py's own docstring for how that's
# obtained (and its one honest gap: some browsers' recorded audio has no readable duration at
# all, in which case this cost is skipped entirely for that turn rather than guessed at).
_SARVAM_STT_COST_PER_SECOND_INR = 30 / 3600


class AskJanMitraService:
    """Constructor-injected, matching this codebase's existing service pattern (e.g.
    TranslationService's injected SarvamClient) — every dependency can be swapped for a test
    double, so tests never make a real network call or need the real 1.5MB embeddings index."""

    def __init__(
        self,
        vector_store: object | None = None,
        embedding_provider: object | None = None,
        location_extractor: LocationExtractor | None = None,
        answer_service: AnswerGenerationService | None = None,
        top_k: int | None = None,
        relevance_threshold: float | None = None,
        verified_relevance_threshold: float | None = None,
        complaint_agent: ComplaintAgent | None = None,
        location_resolver: LocationResolver | None = None,
        vision_service: VisionService | None = None,
        sarvam_client: SarvamClient | None = None,
    ) -> None:
        self._vector_store = vector_store or self._load_default_store()
        self._embedding_provider = embedding_provider or self._load_default_embedding_provider()
        self._retriever = RagRetriever(
            self._vector_store,
            self._embedding_provider,
            top_k=top_k if top_k is not None else settings.RAG_TOP_K,
            relevance_threshold=relevance_threshold if relevance_threshold is not None else settings.RAG_EMBEDDING_RELEVANCE_THRESHOLD,
            verified_relevance_threshold=verified_relevance_threshold if verified_relevance_threshold is not None else settings.RAG_VERIFIED_RELEVANCE_THRESHOLD,
        )
        self._location_extractor = location_extractor or self._build_default_extractor()
        self._answer_service = answer_service or AnswerGenerationService()
        self._complaint_agent = complaint_agent or ComplaintAgent()
        self._location_resolver = location_resolver or LocationResolver()
        # Lazily loaded (see VisionService's own docstring) -- constructing this is cheap, the
        # real ~1.9B-param model only loads on the first attached image, matching this class's
        # existing "never pay startup cost for an optional AI feature" pattern (see
        # _load_default_embedding_provider's docstring for the same reasoning).
        self._vision_service = vision_service or VisionService()
        # Shared for both STT (voice-assistant input) and TTS (voice-assistant output) -- one
        # Sarvam client, matching how ComplaintAgent already owns its own single SarvamClient
        # instance for the same two purposes (STT there; translation here for TTS as well).
        self._sarvam_client = sarvam_client or SarvamClient()
        # Reuses self._sarvam_client (see comment above) -- so clarification/out-of-scope nodes
        # can localize their hardcoded English text into the citizen's language without a second
        # Sarvam client instance.
        self._translation_service = TranslationService(self._sarvam_client)

        self._deps = GraphDeps(
            retriever=self._retriever,
            location_extractor=self._location_extractor,
            answer_service=self._answer_service,
            complaint_agent=self._complaint_agent,
            location_resolver=self._location_resolver,
            translation_service=self._translation_service,
        )
        self._graph = build_graph()

    @staticmethod
    def _load_default_store() -> ChromaVectorStore:
        store = ChromaVectorStore(settings.CHROMA_PERSIST_DIR, settings.CHROMA_COLLECTION_NAME)
        try:
            store.load()
        except Exception as exc:
            # Matches this codebase's "never crash the whole app over an optional AI feature"
            # philosophy (see e.g. SarvamClient's __init__ warning-not-raising on a missing API
            # key) — Ask Sarthi will report RAG-unavailable per-request rather than the app
            # failing to start if the Chroma collection hasn't been built yet (see §33's
            # empty/missing-store test coverage).
            logger.warning("Chroma vector store could not be loaded: %s", exc)
        return store

    @staticmethod
    def _load_default_embedding_provider() -> SentenceTransformerEmbeddingProvider:
        # Deliberately NOT eagerly loaded here (SentenceTransformerEmbeddingProvider's model load
        # is itself lazy, see that class) -- constructing AskJanMitraService must stay cheap and
        # never fail just because the embedding model hasn't been downloaded/cached yet; the
        # first real query pays that cost once, not every app startup.
        return SentenceTransformerEmbeddingProvider()

    @staticmethod
    def _build_default_extractor() -> LocationExtractor:
        gazetteer = RagGazetteer(settings.RAG_DATA_DIR / "chunks" / "chunks.json")
        return LocationExtractor(gazetteer)

    def ask(self, db: Session, user: User, request: AskJanMitraRequest) -> AskJanMitraResponse:
        """Builds the initial graph state from the request, runs the LangGraph orchestrator, and
        translates the final state back into `AskJanMitraResponse`. See
        `backend/services/orchestration/graph.py` for the actual routing/flow logic — this method
        is deliberately thin.

        Also records one `AiRequestLog` row per call (see backend/repositories/
        ai_request_log_repository.py) — the Admin "AI Monitoring" dashboard's data source — on
        both the success and failure path. This is separate from (and does not depend on)
        LangSmith tracing: `langsmith_trace_id` is stored alongside purely as a pointer for the
        dashboard's optional "View Trace" link; the metrics themselves always come from this row,
        never from LangSmith, so the dashboard keeps working even when LangSmith doesn't (see
        docs/ask_janmitra_langsmith_observability.md).
        """
        ctx = RequestContext(
            db=db,
            user=user,
            latitude=request.latitude,
            longitude=request.longitude,
            location_text=request.location_text,
        )
        initial_state = self._build_initial_state(
            question=request.question,
            language=request.language,
            conversation_history=request.conversation_history,
            input_mode="STT" if request.was_voice_input else "TEXT",
            conversation_id=request.conversation_id,
        )
        response, _final_state, _latency_ms = self._run(db, ctx, initial_state, request.language)
        return response

    def ask_with_image(
        self,
        db: Session,
        user: User,
        *,
        question: str,
        language: str,
        latitude: float | None,
        longitude: float | None,
        location_text: str | None,
        conversation_history: list[ConversationTurn],
        image: UploadFile,
        was_voice_input: bool = False,
        conversation_id: str | None = None,
    ) -> AskJanMitraResponse:
        """Same pipeline as `ask()`, plus an attached image. Validates and saves the image to
        disk via the same `evidence_service.validate_and_write()` every other photo upload in
        this app already uses (see backend/services/evidence_service.py) -- no second
        image-storage system -- then captions it via `VisionService` and threads both the saved
        file and the caption into the graph run (see orchestration/nodes.py's
        input_processing_node/complaint_flow_node for how each is used).

        Captioning is best-effort: a `VisionServiceError` (model unavailable, decode failure) is
        logged and swallowed, not raised -- an image that fails to caption still gets attached to
        a complaint as real evidence, it just doesn't enrich the text (matches this codebase's
        "never crash over an optional AI feature" pattern, e.g. SentenceTransformerEmbeddingProvider/
        ChromaVectorStore's own load-failure handling above).

        LangSmith (see docs/ask_janmitra_langsmith_observability.md §9.1): the root run is
        started here, BEFORE the graph runs, so `_process_image()`'s real vision-model call shows
        up as its own `vision_processing` child span (real duration, not folded into the graph's
        own timing) -- the same trace as the graph's `rag_retrieval`/`complaint_creation` spans,
        not a separate one. `_run()` reuses this root run via `run_graph()`'s `root_run` param
        (see that function) and ends it once the graph completes, since (unlike `ask_voice()`)
        there's no post-graph step here that still needs it open.
        """
        input_mode = "IMAGE_STT" if was_voice_input else "IMAGE"
        trace_id = uuid.uuid4()
        request_id = trace_id.hex[:12]
        prelim_state = self._build_initial_state(
            question=question, language=language, conversation_history=conversation_history,
            input_mode=input_mode, conversation_id=conversation_id, has_image=True,
        )
        inputs, metadata = root_run_inputs_and_metadata(prelim_state, request_id)
        root_run = tracing.start_root_run(
            "ask_janmitra_graph", run_id=trace_id, inputs=inputs, metadata=metadata, tags=["ask_janmitra"],
        )

        vision_span = tracing.start_child_run(root_run, "vision_processing", "tool", inputs={"has_image": True})
        saved, image_description = self._process_image(image)
        vision_outputs = {
            "caption_produced": image_description is not None,
            "caption_length": len(image_description or ""),
            # No cost attribute -- unlike every other model this app calls, this is a free,
            # local, self-hosted model (see vision_service.py's own docstring), not a billed
            # external API. `model_name` alone still lets Phoenix show which model ran.
            "model_name": self._vision_service.model_name,
        }
        if image_description:
            # isinstance guard, not a None check: in tests, `self._vision_service` is often a bare
            # Mock() with only `describe_image` stubbed -- an unconfigured `count_tokens` call
            # returns a Mock object, not None, which this filters out the same as a real failure.
            token_count = self._vision_service.count_tokens(image_description)
            if isinstance(token_count, int):
                # Reuses the same "total_tokens" key answer_generation's real token count uses --
                # tracing.py's _phoenix_end_span() already promotes it to Phoenix's dedicated
                # token-count attribute, so this shows up in the Metrics token charts too, with no
                # new promotion logic needed there.
                vision_outputs["total_tokens"] = token_count
        tracing.end_run(vision_span, outputs=vision_outputs)

        ctx = RequestContext(
            db=db,
            user=user,
            latitude=latitude,
            longitude=longitude,
            location_text=location_text,
            image_saved=saved,
        )
        initial_state = self._build_initial_state(
            question=question,
            language=language,
            conversation_history=conversation_history,
            input_mode=input_mode,
            conversation_id=conversation_id,
            has_image=True,
            image_description=image_description,
        )
        response, _final_state, _latency_ms = self._run(
            db, ctx, initial_state, language, root_run=root_run, trace_id=trace_id, request_id=request_id,
        )
        return response

    def ask_voice(
        self,
        db: Session,
        user: User,
        *,
        audio_segments: list[bytes],
        language: str,
        latitude: float | None,
        longitude: float | None,
        location_text: str | None,
        conversation_history: list[ConversationTurn],
        image: UploadFile | None = None,
        conversation_id: str | None = None,
    ) -> AskVoiceResponse:
        """Voice-to-voice: transcribes the citizen's spoken turn via the same chunked-STT
        pipeline `ComplaintAgent`'s voice-complaint flow already uses (see
        `stt_service.transcribe_chunks`), runs it through the exact same graph every other entry
        point uses, then synthesizes the AI's text answer into real spoken audio via Sarvam TTS.
        An optional attached image is accepted too (combined voice+image turns) -- validated and
        captioned exactly like `ask_with_image()`, via the same shared `_process_image()` helper.

        STT failure (every audio chunk unusable) is NOT swallowed -- there is no text to answer
        at all, so `AIServiceError` propagates as a real error for the route to surface honestly
        (never a fabricated/empty answer). TTS failure IS best-effort: a synthesis failure is
        logged and swallowed, not raised -- the citizen still gets the real text answer with
        `audio_base64=None` (never fake/placeholder audio) rather than the whole turn failing.

        LangSmith (see docs/ask_janmitra_langsmith_observability.md §9.1): the root run is
        started here, before STT even runs, so `speech_to_text` (and `vision_processing`, if an
        image is attached too) and the eventual `text_to_speech` span all nest under the SAME
        trace as the graph's own spans -- "Ask Sarthi Voice -> STT -> [Vision ->] LangGraph ->
        ... -> TTS", not three separate traces. `_run()` is told NOT to end the root run itself
        (`end_root_run=False`) since TTS still needs it open; this method ends it once TTS (best-
        effort) is done, via the same `graph.root_run_outputs()` helper `run_graph()`'s own
        internal end-of-run path uses, so the shape is identical either way.
        """
        if not audio_segments:
            raise ValueError("At least one audio segment is required.")

        # STT decodes with Sarvam's own auto-detect ("unknown"), NOT the client-supplied
        # `language` (typically the UI's language toggle) -- forcing a fixed decode language onto
        # audio the citizen may have actually spoken in a DIFFERENT language risks a genuinely
        # broken transcription (Sarvam decoding Marathi speech as if it were English phonetics),
        # not just an answer-language mismatch. See language_node's own docstring for the matching
        # text-side fix; this is voice's equivalent for its own, audio-specific failure mode.
        has_image = image is not None and bool(image.filename)
        input_mode = "IMAGE_VOICE_ASSISTANT" if has_image else "VOICE_ASSISTANT"
        trace_id = uuid.uuid4()
        request_id = trace_id.hex[:12]

        prelim_state = self._build_initial_state(
            question="", language=language, conversation_history=conversation_history,
            input_mode=input_mode, conversation_id=conversation_id, has_image=has_image,
        )
        inputs, metadata = root_run_inputs_and_metadata(prelim_state, request_id)
        root_run = tracing.start_root_run(
            "ask_janmitra_graph", run_id=trace_id, inputs=inputs, metadata=metadata, tags=["ask_janmitra"],
        )

        stt_span = tracing.start_child_run(root_run, "speech_to_text", "llm", inputs={"segment_count": len(audio_segments)})
        try:
            question = transcribe_chunks(self._sarvam_client, audio_segments, "unknown")
        except AIServiceError as exc:
            tracing.end_run(stt_span, error=str(exc))
            tracing.end_run(root_run, error=str(exc))
            raise
        stt_outputs = {"transcript_length": len(question), "transcript": tracing.redact_text(question)}
        # Best-effort real cost: only set when EVERY segment's duration was actually readable --
        # silently summing just the known ones would understate the real total rather than being
        # honestly absent (see audio_duration.py's own docstring for why some segments have no
        # readable duration at all).
        segment_durations = [get_audio_duration_seconds(chunk) for chunk in audio_segments]
        if segment_durations and all(d is not None for d in segment_durations):
            total_seconds = sum(segment_durations)
            stt_outputs["model_name"] = "saaras:v3"
            stt_outputs["total_cost_inr"] = total_seconds * _SARVAM_STT_COST_PER_SECOND_INR
            # Same reasoning as the translation/TTS spans: STT is billed per SECOND of audio, not
            # per token, so Phoenix's own per-model dashboard widgets have nothing to work with
            # unless this slot carries a real duration-derived count. Milliseconds (not whole
            # seconds) to keep meaningful precision for short voice questions -- `total_cost_inr`
            # above is the real, unrounded source of truth regardless.
            total_ms = round(total_seconds * 1000)
            stt_outputs["prompt_tokens"] = total_ms
            stt_outputs["total_tokens"] = total_ms
        tracing.end_run(stt_span, outputs=stt_outputs)

        if has_image:
            vision_span = tracing.start_child_run(root_run, "vision_processing", "tool", inputs={"has_image": True})
            saved, image_description = self._process_image(image)
            vision_outputs = {
                "caption_produced": image_description is not None,
                "caption_length": len(image_description or ""),
                "model_name": self._vision_service.model_name,
            }
            if image_description:
                token_count = self._vision_service.count_tokens(image_description)
                if isinstance(token_count, int):
                    vision_outputs["total_tokens"] = token_count
            tracing.end_run(vision_span, outputs=vision_outputs)
        else:
            saved, image_description = None, None

        ctx = RequestContext(
            db=db,
            user=user,
            latitude=latitude,
            longitude=longitude,
            location_text=location_text,
            image_saved=saved,
        )
        initial_state = self._build_initial_state(
            question=question,
            language=language,
            conversation_history=conversation_history,
            input_mode=input_mode,
            conversation_id=conversation_id,
            has_image=has_image,
            image_description=image_description,
        )
        base_response, final_state, latency_ms = self._run(
            db, ctx, initial_state, language, root_run=root_run, trace_id=trace_id, request_id=request_id, end_root_run=False,
        )

        audio_base64: str | None = None
        # base_response.language is the auto-detected response_language (see _run()'s own
        # comment on this), NOT the raw client-supplied `language` this method was called with --
        # TTS must speak in whatever language the answer TEXT actually ended up in, or a citizen
        # who asked (and got answered) in a different language than their UI toggle would hear
        # speech in the WRONG language despite reading a correct answer.
        tts_bcp47 = to_bcp47(base_response.language)
        tts_span = tracing.start_child_run(root_run, "text_to_speech", "llm", inputs={"answer_length": len(base_response.answer)})
        try:
            audio_base64 = self._sarvam_client.synthesize_speech_long(base_response.answer, tts_bcp47)
            tracing.end_run(
                tts_span,
                outputs={
                    "audio_produced": True,
                    "model_name": settings.TTS_MODEL,
                    "total_cost_inr": len(base_response.answer) * _SARVAM_TTS_COST_PER_CHAR_INR,
                    # Same reasoning as _localize()'s response_translation span (orchestration/
                    # nodes.py): TTS is billed per character, not per token, so Phoenix's own
                    # per-model cost/token dashboard widgets have nothing to work with unless this
                    # slot carries the real character count -- the actual `total_cost_inr` above is
                    # unaffected either way.
                    "prompt_tokens": len(base_response.answer),
                    "total_tokens": len(base_response.answer),
                },
            )
        except AIServiceError as exc:
            logger.warning("Ask Sarthi voice flow: TTS failed, returning text-only: %s", exc)
            tracing.end_run(tts_span, outputs={"audio_produced": False}, error=str(exc))

        tracing.end_run(root_run, outputs=root_run_outputs(final_state, latency_ms))

        return AskVoiceResponse(**base_response.model_dump(), question=question, audio_base64=audio_base64)

    def _process_image(self, image: UploadFile | None) -> tuple[SavedFile | None, str | None]:
        """Shared by `ask_with_image()`/`ask_voice()`: validates+saves an optional attached image
        (via the same `evidence_service.validate_and_write()` every photo upload in this app
        already uses) and captions it best-effort via `VisionService`. Returns `(None, None)` if
        no image was actually attached (an empty/filename-less `UploadFile` counts as none, same
        as every other optional-photo endpoint in this codebase, e.g. routes/complaints.py's
        `photo`/`photos` handling)."""
        if image is None or not image.filename:
            return None, None

        # validate_and_write() consumes image.file via .read() -- read our own copy first (for
        # VisionService, which needs the raw bytes) and rewind before handing it off, so the save
        # step still sees the full file.
        image_bytes = image.file.read()
        image.file.seek(0)
        saved = validate_and_write(image)

        image_description: str | None = None
        try:
            image_description = self._vision_service.describe_image(image_bytes)
        except VisionServiceError as exc:
            logger.warning("Ask Sarthi: captioning failed, continuing without a description: %s", exc)

        return saved, image_description

    @staticmethod
    def _build_initial_state(
        *,
        question: str,
        language: str,
        conversation_history: list[ConversationTurn],
        input_mode: str,
        conversation_id: str | None = None,
        has_image: bool = False,
        image_description: str | None = None,
    ) -> GraphState:
        state: GraphState = {
            "user_message": question,
            "original_language": language,
            "input_type": "text",
            "conversation_id": conversation_id,
            "conversation_history": [
                {
                    "role": t.role,
                    "content": t.content,
                    "complaint_workflow_state": t.complaint_workflow_state,
                    "photo_evidence": t.photo_evidence.model_dump() if t.photo_evidence else None,
                }
                for t in conversation_history
            ],
            "input_mode": input_mode,
            "vision_used": has_image,
            "tts_used": input_mode in ("VOICE_ASSISTANT", "IMAGE_VOICE_ASSISTANT"),
        }
        if has_image:
            state["has_image"] = True
            if image_description:
                state["image_description"] = image_description
        return state

    def _run(
        self,
        db: Session,
        ctx: RequestContext,
        initial_state: GraphState,
        language: str,
        *,
        root_run: object | None = None,
        trace_id: uuid.UUID | None = None,
        request_id: str | None = None,
        end_root_run: bool = True,
    ) -> tuple[AskJanMitraResponse, GraphState, float]:
        """Shared tail end of `ask()`/`ask_with_image()`/`ask_voice()`: run the graph, record the
        AiRequestLog row, translate the final state into `AskJanMitraResponse`. Extracted so every
        Ask Sarthi entry point invokes the exact same `run_graph()` call and logs identically --
        no parallel pipeline per input mode.

        Returns `(response, final_state, latency_ms)` -- callers that don't need the latter two
        (every caller except `ask_voice()`, which needs `final_state`/`latency_ms` to end its own
        deferred root run after TTS via `graph.root_run_outputs()`) just discard them.

        `root_run`/`trace_id`/`request_id`: normally all `None`, in which case this method creates
        its own trace id and lets `run_graph()` own the root run's full lifecycle exactly as
        before. `ask_with_image()`/`ask_voice()` pass an already-started root run instead (see
        those methods) so their own vision/STT/TTS spans nest under the SAME trace; `end_root_run`
        controls whether THIS method ends that externally-owned root run when the graph completes
        (`ask_with_image()`, nothing runs after) or leaves it open (`ask_voice()`, TTS runs after).
        On an exception, an externally-owned root run is always ended here with the error --
        there's no "later" for a raised exception to still add a TTS span to.
        """
        trace_id = trace_id or uuid.uuid4()
        request_id = request_id or trace_id.hex[:12]
        start = time.perf_counter()

        try:
            final_state = run_graph(
                self._graph, self._deps, ctx, initial_state, request_id=request_id, trace_id=trace_id, root_run=root_run,
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            if root_run is not None:
                tracing.end_run(root_run, error=str(exc))
            # A mid-request exception may have left `db`'s transaction in a failed state (e.g. a
            # DB error inside a node) -- roll back before attempting to write the log row, so that
            # write doesn't itself fail because of the earlier one.
            db.rollback()
            ai_request_log_repository.record_ai_request(
                db,
                request_id=request_id,
                langsmith_trace_id=str(trace_id) if tracing.is_enabled() else None,
                phoenix_trace_id=tracing.get_phoenix_trace_id(trace_id),
                conversation_id=initial_state.get("conversation_id"),
                intent=None,
                service_category=None,
                routed_to="ERROR",
                success=False,
                error_type=type(exc).__name__,
                latency_ms=latency_ms,
            )
            # Checked on the failure path too -- a string of failing requests is exactly the
            # sustained-error-rate case admins most need alerted on. Best-effort, see that
            # function's own docstring; never masks the exception being re-raised below.
            ai_request_log_repository.check_and_fire_alerts(db)
            raise

        latency_ms = (time.perf_counter() - start) * 1000
        if root_run is not None and end_root_run:
            tracing.end_run(root_run, outputs=root_run_outputs(final_state, latency_ms))
        ai_request_log_repository.record_ai_request(
            db,
            request_id=request_id,
            langsmith_trace_id=str(trace_id) if tracing.is_enabled() else None,
            phoenix_trace_id=tracing.get_phoenix_trace_id(trace_id),
            conversation_id=final_state.get("conversation_id"),
            intent=final_state.get("intent"),
            service_category=final_state.get("service_category"),
            routed_to=final_state.get("routed_to", "NONE_OUT_OF_SCOPE"),
            success=True,
            error_type=None,
            latency_ms=latency_ms,
            ai_cost_inr=final_state.get("ai_cost_inr"),
            ai_model_name=final_state.get("ai_model_name"),
            ai_total_tokens=final_state.get("ai_total_tokens"),
        )
        ai_request_log_repository.check_and_fire_alerts(db)

        location = None
        if "location_city" in final_state or "location_state" in final_state:
            location = LocationInfo(
                city=final_state.get("location_city"),
                state=final_state.get("location_state"),
                source=final_state.get("location_source", "none"),
                is_ambiguous=final_state.get("location_is_ambiguous", False),
                ambiguous_candidates=final_state.get("location_ambiguous_candidates", []),
            )

        sources = [Citation(**s) for s in final_state.get("sources", [])]

        response = AskJanMitraResponse(
            answer=final_state.get("response_text", "I'm not sure how to help with that yet."),
            intent=final_state.get("intent", "TYPE_A_COMPLAINT"),
            service_category=final_state.get("service_category"),
            # response_language (auto-detected by language_node from the turn's own text/
            # transcript, see its own docstring), NOT the raw `language` param this method was
            # called with -- this field must reflect what the answer text is ACTUALLY in. Falls
            # back to `language` only if the graph somehow never set response_language at all
            # (should not happen in practice; language_node always seeds it).
            language=final_state.get("response_language", language),
            location=location,
            sources=sources,
            verification_status=final_state.get("verification_status"),
            follow_up_required=final_state.get("follow_up_required", False),
            follow_up_question=final_state.get("follow_up_question"),
            follow_up_options=final_state.get("follow_up_options", []),
            follow_up_options_labels=final_state.get("follow_up_options_labels"),
            insufficient_knowledge=final_state.get("insufficient_knowledge", False),
            routed_to=final_state.get("routed_to", "NONE_OUT_OF_SCOPE"),
            answer_was_llm_generated=final_state.get("answer_was_llm_generated", False),
            complaint_id=final_state.get("complaint_id"),
            complaint_workflow_state=final_state.get("complaint_workflow_state"),
            photo_evidence=(
                PhotoEvidenceRef(**final_state["photo_evidence"]) if final_state.get("photo_evidence") else None
            ),
        )
        return response, final_state, latency_ms
