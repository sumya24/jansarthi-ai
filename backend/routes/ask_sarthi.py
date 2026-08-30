"""POST /ask-sarthi — the Ask Sarthi retrieval endpoint.

Requires authentication (same as every other route in this app — see backend/deps.py) because
TYPE_C complaint-status questions need to know who's asking (a citizen can only see their own
complaints, matching GET /complaints's existing authorization rule). All roles (citizen/worker/
admin) can call this — a worker or admin asking a civic-service question gets the same RAG
answer a citizen would; TYPE_C status lookups are scoped by role the same way the rest of the
complaints API already is.
"""

import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.deps import get_current_user, require_ai_rate_limit
from backend.models import User
from backend.schemas.ask_sarthi import AskSarthiRequest, AskSarthiResponse, AskVoiceResponse, ConversationTurn
from backend.services import metrics as sentry_metrics
from backend.services.ask_sarthi_service import AskSarthiService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ask-sarthi", tags=["ask-sarthi"])

_service = AskSarthiService()

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
