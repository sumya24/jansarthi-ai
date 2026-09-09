"""POST /ask-sarthi — the Ask Sarthi retrieval endpoint.

Requires authentication (same as every other route in this app — see backend/deps.py) because
TYPE_C complaint-status questions need to know who's asking (a citizen can only see their own
complaints, matching GET /complaints's existing authorization rule). All roles (citizen/worker/
admin) can call this — a worker or admin asking a civic-service question gets the same RAG
answer a citizen would; TYPE_C status lookups are scoped by role the same way the rest of the
complaints API already is.
"""

import asyncio
import base64
import json
import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sarvamai.types.audio_output import AudioOutput
from sarvamai.types.event_response import EventResponse
from sarvamai.types.realtime_audio_input import RealtimeAudioInput
from sarvamai.types.realtime_error import RealtimeError as SarvamRealtimeError
from sarvamai.types.realtime_transcript_final import RealtimeTranscriptFinal
from sarvamai.types.realtime_transcript_partial import RealtimeTranscriptPartial
from sqlalchemy.orm import Session

from backend.config import settings, to_bcp47
from backend.database import get_db
from backend.deps import check_ai_rate_limit, get_current_user, get_current_user_ws, require_ai_rate_limit
from backend.models import User
from backend.schemas.ask_sarthi import AskSarthiRequest, AskSarthiResponse, AskVoiceResponse, ConversationTurn
from backend.services import metrics as sentry_metrics
from backend.services.ask_sarthi_service import AskSarthiService
from backend.services.auth_service import InvalidTokenError
from backend.services.sarvam_client import AIServiceError
from backend.services.sarvam_realtime_client import SarvamRealtimeClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ask-sarthi", tags=["ask-sarthi"])

_service = AskSarthiService()
_realtime_client = SarvamRealtimeClient()

_GENERIC_UNAVAILABLE_DETAIL = "Ask Sarthi is temporarily unavailable. Please try again, or use the complaint form directly."


@router.post("", response_model=AskSarthiResponse, dependencies=[Depends(require_ai_rate_limit)])
def ask_sarthi(
    request: AskSarthiRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AskSarthiResponse:
    """Ask a civic-service question (complaint-shaped or information-shaped) or check a
    complaint's status. See backend/services/ask_sarthi_service.py for the full routing logic.

    Rate-limited per authenticated user, shared with the /image and /voice variants below (see
    backend/deps.py's require_ai_rate_limit) -- protects the real, paid Sarvam/LLM calls this
    triggers from abuse.
    """
    if request.language not in settings.SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {request.language}")

    sentry_metrics.count("ask_sarthi.request", 1, attributes={"channel": "text"})

    try:
        return _service.ask(db, current_user, request)
    except Exception as exc:
        # Defense-in-depth, matching the same principle applied to LocationResolver's call site
        # in routes/complaints.py: a bug anywhere in the RAG/LLM pipeline must produce a clear
        # error, never a stack trace leaked to the client and never a fabricated-looking answer.
        logger.exception("Ask Sarthi request failed unexpectedly")
        raise HTTPException(status_code=503, detail=_GENERIC_UNAVAILABLE_DETAIL) from exc


def _parse_conversation_history(raw: str) -> list[ConversationTurn]:
    """Multipart form fields can't carry nested JSON structures directly -- the frontend
    JSON-encodes `conversation_history` into one string field, mirroring the exact shape
    `AskSarthiRequest.conversation_history` already validates for the plain-JSON endpoint."""
    try:
        parsed = json.loads(raw) if raw else []
        return [ConversationTurn(**turn) for turn in parsed]
    except (json.JSONDecodeError, TypeError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail="Invalid conversation_history.") from exc


@router.post("/image", response_model=AskSarthiResponse, dependencies=[Depends(require_ai_rate_limit)])
def ask_sarthi_with_image(
    question: str = Form(""),
    language: str = Form("en"),
    latitude: float | None = Form(None),
    longitude: float | None = Form(None),
    location_text: str | None = Form(None),
    conversation_history: str = Form("[]"),
    conversation_id: str | None = Form(None),
    was_voice_input: bool = Form(False),
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AskSarthiResponse:
    """Same as `POST /ask-sarthi`, with one attached photo. Multipart (not JSON) because it
    carries a file -- `question` may be empty here (an image with no text at all is a valid,
    real use case, unlike the plain endpoint's `AskSarthiRequest.question` which requires
    non-empty text). See backend/services/ask_sarthi_service.py's `ask_with_image()`.
    """
    if language not in settings.SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {language}")

    history = _parse_conversation_history(conversation_history)

    sentry_metrics.count("ask_sarthi.request", 1, attributes={"channel": "image"})

    try:
        return _service.ask_with_image(
            db,
            current_user,
            question=question,
            language=language,
            latitude=latitude,
            longitude=longitude,
            location_text=location_text,
            conversation_history=history,
            image=image,
            was_voice_input=was_voice_input,
            conversation_id=conversation_id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Ask Sarthi image request failed unexpectedly")
        raise HTTPException(status_code=503, detail=_GENERIC_UNAVAILABLE_DETAIL) from exc


@router.post("/voice", response_model=AskVoiceResponse, dependencies=[Depends(require_ai_rate_limit)])
def ask_sarthi_voice(
    language: str = Form("en"),
    latitude: float | None = Form(None),
    longitude: float | None = Form(None),
    location_text: str | None = Form(None),
    conversation_history: str = Form("[]"),
    conversation_id: str | None = Form(None),
    audio: list[UploadFile] = File(...),
    image: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AskVoiceResponse:
    """The voice-to-voice assistant turn: one or more recorded audio segments in, a real spoken
    answer out. `audio` may contain more than one file for the same reason `POST /complaints`'s
    own `audio` field does -- Sarvam's STT endpoint hard-caps a single request at 30 seconds (see
    docs/ai_pipeline_limits.md), so the citizen-facing recorder (useAudioRecorder.ts) splits a
    longer turn into ~28s segments client-side. An optional attached `image` is accepted here too
    (a combined voice+image turn) -- see backend/services/ask_sarthi_service.py's `ask_voice()`.
    """
    if language not in settings.SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {language}")

    audio_segments = [a.file.read() for a in audio if a.filename]
    if not audio_segments:
        raise HTTPException(status_code=400, detail="At least one audio segment is required.")

    history = _parse_conversation_history(conversation_history)

    sentry_metrics.count("ask_sarthi.request", 1, attributes={"channel": "voice"})

    try:
        return _service.ask_voice(
            db,
            current_user,
            audio_segments=audio_segments,
            language=language,
            latitude=latitude,
            longitude=longitude,
            location_text=location_text,
            conversation_history=history,
            image=image,
            conversation_id=conversation_id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Ask Sarthi voice request failed unexpectedly")
        raise HTTPException(status_code=503, detail=_GENERIC_UNAVAILABLE_DETAIL) from exc


async def _stream_answer_audio(websocket: WebSocket, answer: str, tts_language: str) -> None:
    """Synthesizes `answer` via Sarvam's streaming TTS and forwards each audio chunk to the
    client the moment it's produced -- unlike `POST /ask-sarthi/voice`'s `synthesize_speech_long`,
    which stitches every chunk into one WAV before anything is sent. Best-effort like that same
    batch path: a synthesis failure here is logged and swallowed, never raised -- the citizen
    already has the real text answer (sent by the caller before this runs); losing audio for one
    turn is not worth failing the whole live session over."""
    try:
        async with _realtime_client.open_tts_stream() as tts_socket:
            await tts_socket.configure(target_language_code=tts_language, speaker=settings.TTS_SPEAKER)
            await tts_socket.convert(answer)
            await tts_socket.flush()
            while True:
                event = await tts_socket.recv()
                if isinstance(event, AudioOutput):
                    await websocket.send_json({
                        "type": "audio_chunk",
                        "audio_base64": event.data.audio,
                        "content_type": event.data.content_type,
                    })
                elif isinstance(event, EventResponse) and event.data.event_type == "final":
                    break
    except Exception:
        logger.warning("Live-voice TTS streaming failed; the text answer was already sent.", exc_info=True)


async def _try_send_error(websocket: WebSocket, detail: str) -> None:
    """Best-effort: the session is already ending (an unexpected exception unwound past every
    other handler) -- the underlying socket may itself be the reason this is failing, so a second
    failure here must never mask the original one with a fresh traceback."""
    try:
        await websocket.send_json({"type": "error", "detail": detail})
    except Exception:
        pass


async def _handle_one_live_turn(
    websocket: WebSocket,
    db: Session,
    current_user: User,
    transcript: str,
    conversation_history: list[ConversationTurn],
    conversation_id: str,
    language: str,
) -> None:
    """Runs the exact same orchestration `POST /ask-sarthi` uses (`AskSarthiService.ask()`, zero
    routing/graph changes) for one Sarvam-VAD-finalized utterance, then streams the answer back as
    speech. Rate-limited per utterance (not per HTTP request, since this isn't one) via
    `check_ai_rate_limit` -- same limiter/settings `require_ai_rate_limit` uses for every other
    Ask Sarthi entry point, so a citizen can't get a higher effective quota just by switching to
    Live mode. `db`'s blocking `ask()` call runs in a thread (`asyncio.to_thread`) so it doesn't
    block this connection's event loop -- and, since FastAPI runs one event loop per worker process
    shared across all connections, doesn't block every OTHER citizen's live session either."""
    allowed, retry_after = check_ai_rate_limit(current_user.id)
    if not allowed:
        await websocket.send_json({
            "type": "error",
            "detail": "Too many requests to Ask Sarthi. Please wait a moment and try again.",
            "retry_after": retry_after,
        })
        return

    # `transcript` is included here (not a separate message) so the client can show the citizen's
    # own turn in the visible history the moment processing starts, not only once the answer
    # arrives -- this is the ONLY place the finalized transcript reaches the client at all; the
    # `answer` message below carries only the assistant's own response fields.
    await websocket.send_json({"type": "turn_started", "transcript": transcript})
    request = AskSarthiRequest(
        question=transcript, language=language, conversation_history=list(conversation_history),
        conversation_id=conversation_id, was_voice_input=True,
    )
    try:
        response = await asyncio.to_thread(_service.ask, db, current_user, request)
    except Exception:
        logger.exception("Ask Sarthi live-voice turn failed unexpectedly")
        await websocket.send_json({"type": "error", "detail": _GENERIC_UNAVAILABLE_DETAIL})
        return

    await websocket.send_json({"type": "answer", **response.model_dump(mode="json")})
    conversation_history.append(ConversationTurn(role="user", content=transcript))
    conversation_history.append(ConversationTurn(
        role="assistant", content=response.answer,
        complaint_workflow_state=getattr(response, "complaint_workflow_state", None),
    ))

    await _stream_answer_audio(websocket, response.answer, to_bcp47(response.language))
    await websocket.send_json({"type": "turn_complete"})


@router.websocket("/voice/live")
async def ask_sarthi_voice_live(websocket: WebSocket, language: str = "en", db: Session = Depends(get_db)) -> None:
    """"Live" voice mode: always-listening, low-latency voice -- an OPT-IN alternative to
    `POST /ask-sarthi/voice`'s tap-to-record flow (see that route's own docstring), never a
    replacement for it. Proxies the citizen's continuous mic audio to Sarvam's realtime STT
    WebSocket, whose own VAD (`endpointing="vad"`, see sarvam_realtime_client.py) decides where
    each utterance ends -- no tap gesture or server-side silence-detection logic needed. Each
    finalized utterance runs through the SAME orchestration every other Ask Sarthi entry point
    uses (see `_handle_one_live_turn`), then the answer streams back as speech chunk-by-chunk as
    Sarvam synthesizes it, instead of waiting for one fully-stitched audio blob.

    Turn-based, but with no tap required to START a turn -- true mid-response interruption
    ("barge-in") is explicitly out of scope: the client is expected to stop sending mic audio
    while a turn's `audio_chunk`s are playing, and resume once `turn_complete` arrives. Full
    barge-in needs echo-cancelled simultaneous listen+speak handling, a materially larger lift
    than this feature; revisit only if asked (same honest limitation `VoiceAssistantOverlay.tsx`'s
    own "Classic" mode already documents about itself).

    Auth: same cookie/header extraction `get_current_user` uses for every other route, adapted
    for `WebSocket` (see `get_current_user_ws`) -- a same-origin browser WebSocket handshake
    carries the `access_token` cookie automatically, so no query-param token workaround is needed.

    Message protocol -- client -> server: binary frames of raw 16-bit PCM mono audio, 16kHz
    (`linear16`, matching sarvam_realtime_client.py's configured encoding). server -> client, JSON
    text messages: `{"type": "partial_transcript", "text": ...}` (live caption, best-effort),
    `{"type": "turn_started", "transcript": ...}` (Sarvam's VAD finalized this utterance --
    `transcript` is the ONLY place the citizen's own finalized text reaches the client),
    `{"type": "answer", ...AskSarthiResponse fields...}`, `{"type": "audio_chunk",
    "audio_base64": ..., "content_type": ...}` (one per streamed TTS chunk),
    `{"type": "turn_complete"}`, `{"type": "error", "detail": ...}`.
    """
    await websocket.accept()
    try:
        current_user = get_current_user_ws(websocket, db)
    except InvalidTokenError as exc:
        await websocket.send_json({"type": "error", "detail": str(exc)})
        await websocket.close(code=4401)
        return

    if language not in settings.SUPPORTED_LANGUAGES:
        await websocket.send_json({"type": "error", "detail": f"Unsupported language: {language}"})
        await websocket.close(code=4400)
        return

    if not _realtime_client.configured():
        await websocket.send_json({"type": "error", "detail": _GENERIC_UNAVAILABLE_DETAIL})
        await websocket.close(code=1011)
        return

    sentry_metrics.count("ask_sarthi.request", 1, attributes={"channel": "voice_live"})
    conversation_history: list[ConversationTurn] = []
    conversation_id = str(uuid.uuid4())

    try:
        async with _realtime_client.open_transcription_stream(to_bcp47(language)) as stt_socket:
            transcript_queue: asyncio.Queue[str] = asyncio.Queue()

            async def forward_audio() -> None:
                while True:
                    frame = await websocket.receive_bytes()
                    await stt_socket.send_realtime_audio_input(
                        RealtimeAudioInput(audio=base64.b64encode(frame).decode("ascii"))
                    )

            async def listen_for_transcripts() -> None:
                # LIVE-REPORTED BUG: a fatal Sarvam error (confirmed live -- local dev's own
                # Sarvam account out of realtime-STT credits, `code="quota_exceeded"`) used to
                # only be logged server-side here -- the client was never told at all, and Sarvam
                # closes its own socket right after sending the error event, so the NEXT loop
                # iteration's `stt_socket.recv()` raised an uncaught `ConnectionClosedError` that
                # killed the whole session with no message ever reaching the citizen. The overlay
                # just sat on "Listening..." forever with nothing visibly wrong. Fixed by
                # forwarding every error to the client (fatal or not -- a non-fatal one is still
                # worth surfacing, even though this loop keeps running for it) and returning
                # immediately on a fatal one instead of trying to keep reading from a socket
                # Sarvam has already closed.
                while True:
                    event = await stt_socket.recv()
                    if isinstance(event, RealtimeTranscriptFinal) and event.text.strip():
                        await transcript_queue.put(event.text)
                    elif isinstance(event, RealtimeTranscriptPartial):
                        # Best-effort live caption only (matches VoiceAssistantOverlay.tsx's own
                        # "Classic" mode live caption) -- never read by any turn-processing logic,
                        # so a dropped/out-of-order partial here is never a correctness issue.
                        await websocket.send_json({"type": "partial_transcript", "text": event.text})
                    elif isinstance(event, SarvamRealtimeError):
                        logger.warning("Sarvam realtime STT error: %s", event)
                        await websocket.send_json({"type": "error", "detail": event.message})
                        if event.is_fatal:
                            return

            async def process_turns() -> None:
                while True:
                    transcript = await transcript_queue.get()
                    await _handle_one_live_turn(
                        websocket, db, current_user, transcript, conversation_history, conversation_id, language,
                    )

            tasks = [asyncio.create_task(coro()) for coro in (forward_audio, listen_for_transcripts, process_turns)]
            try:
                done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    exc = task.exception()
                    if exc is not None and not isinstance(exc, WebSocketDisconnect):
                        raise exc
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    except AIServiceError as exc:
        logger.warning("Live-voice session ended on a Sarvam error: %s", exc)
        await _try_send_error(websocket, _GENERIC_UNAVAILABLE_DETAIL)
    except Exception:
        logger.exception("Ask Sarthi live-voice session failed unexpectedly")
        await _try_send_error(websocket, _GENERIC_UNAVAILABLE_DETAIL)
