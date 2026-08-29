"""Orchestrates the end-to-end processing and storage of a citizen complaint."""

import logging

from sqlalchemy.orm import Session

from backend.config import to_bcp47
from backend.models import Complaint
from backend.schemas.rag_knowledge import ServiceCategory
from backend.services.normalization_service import NormalizationService
from backend.services.sarvam_client import AIServiceError, SarvamClient
from backend.services.stt_service import STT_GAP_MARKER as _STT_GAP_MARKER
from backend.services.stt_service import transcribe_chunks
from backend.services.summary_service import SummaryService
from backend.services.translation_service import TranslationService

logger = logging.getLogger(__name__)


class ComplaintAgent:
    """Receives a citizen complaint (voice or text) and stores it as a processed record.

    Responsibilities: transcribe audio if needed, translate to English, clean up
    obvious spelling mistakes, generate a summary, and persist the resulting
    complaint to the database.
    """

    def __init__(
        self,
        sarvam_client: SarvamClient | None = None,
        translation_service: TranslationService | None = None,
        summary_service: SummaryService | None = None,
        normalization_service: NormalizationService | None = None,
    ) -> None:
        """Initialize the agent, creating default service instances if none are given."""
        self._sarvam = sarvam_client or SarvamClient()
        self._translation = translation_service or TranslationService(self._sarvam)
        self._summary = summary_service or SummaryService()
        self._normalization = normalization_service or NormalizationService()

    def create_complaint(
        self,
        db: Session,
        citizen_id: str,
        language_code: str,
        text: str | None,
        audio_chunks: list[bytes] | None,
        photo_path: str | None,
        category: ServiceCategory | None = None,
    ) -> Complaint:
        """Process citizen input and store a new complaint record.

        Exactly one of `text` or `audio_chunks` should be provided.

        Args:
            db: Active database session.
            citizen_id: Hardcoded citizen identifier.
            language_code: Short language code of the citizen's input, e.g. "mr".
            text: Typed complaint text, or None if voice was used.
            audio_chunks: Ordered raw-audio byte chunks of a spoken complaint, or None if
                text was used. A recording longer than Sarvam's 30-second-per-request STT
                cap arrives as multiple chunks — see
                frontend-react/src/lib/useAudioRecorder.ts, which splits recording into
                ~28s segments client-side for exactly this reason (see
                docs/ai_pipeline_limits.md for why 30s is a hard, actively-enforced limit).
            photo_path: Relative path to an attached photo, or None.
            category: The civic-service category this complaint was classified as (by Ask
                Sarthi's own intent classifier, or the Report an Issue wizard's 3-layer
                classifier), or None if it couldn't be determined -- see LIVE-REPORTED GAP note on
                Complaint.service_category. Optional/defaulted so no existing caller needs to
                change unless it actually has a category to give.

        Returns:
            The newly created and persisted Complaint record.

        Raises:
            ValueError: If neither text nor audio is provided, or transcription is empty.
            AIServiceError: If every audio chunk fails to transcribe, or translation or
                summarization fails.
        """
        if audio_chunks:
            logger.info(
                "Complaint received (voice, citizen=%s, language=%s, chunks=%d)",
                citizen_id, language_code, len(audio_chunks),
            )
            original_text = self._transcribe_chunks(audio_chunks, to_bcp47(language_code))
        elif text is not None:
            logger.info("Complaint received (text, citizen=%s, language=%s)", citizen_id, language_code)
            original_text = text
        else:
            raise ValueError("Either text or audio_chunks must be provided.")

        original_text = original_text.strip()
        if not original_text:
            raise ValueError("Complaint text is empty.")

        # Clean up obvious spelling/typing mistakes in the citizen's own language before
        # translating, so a typo (in Marathi, Hindi, or English) doesn't produce a bad
        # English translation that then propagates into every future re-translation for
        # workers. `original_text` in storage stays exactly what the citizen wrote; only
        # this working copy, used as translation input, is normalized. Best-effort: falls
        # back to the untouched text on failure rather than blocking complaint submission.
        normalized_text = self._normalization.normalize(original_text, language_code)
        translated_text = self._translation.to_english(normalized_text, language_code)
        # Best-effort, same as normalization above -- unlike SummaryService.summarize()'s own
        # documented contract (raises AIServiceError on any failure), a summary is a quality
        # enhancement, not something worth losing the citizen's whole complaint over. This was a
        # real, observed bug: sarvam-105b (a reasoning model) can burn its entire max_tokens
        # budget on internal reasoning and return empty content (finish_reason="length") before
        # ever producing the actual summary -- summarize() correctly treats that as a failure,
        # but until this fix, that failure propagated all the way up through this uncaught call
        # and the route's `except AIServiceError -> 502`, rejecting the whole submission. Falls
        # back to a truncated version of the translated text -- still useful to a worker reading
        # the complaint, unlike a generic "summary unavailable" placeholder.
        try:
            summary = self._summary.summarize(translated_text)
        except AIServiceError as exc:
            logger.warning("Summary generation failed, falling back to truncated text: %s", exc)
            summary = translated_text if len(translated_text) <= 200 else translated_text[:197] + "..."

        complaint = Complaint(
            citizen_id=citizen_id,
            original_text=original_text,
            original_language=language_code,
            translated_text=translated_text,
            summary=summary,
            photo_path=photo_path,
            status="open",
            service_category=category.value if category else None,
        )
        db.add(complaint)
        db.commit()
        db.refresh(complaint)
        logger.info("Complaint stored (id=%s, citizen=%s)", complaint.id, citizen_id)
        return complaint

    def _transcribe_chunks(self, audio_chunks: list[bytes], bcp47_language: str) -> str:
        """Transcribe each audio chunk in order and join the results into one transcript --
        see backend/services/stt_service.py's `transcribe_chunks()` for the full behavior
        (retry-once-then-gap-marker chunk stitching), unchanged, just relocated so Ask
        Sarthi's voice-assistant flow can reuse it too."""
        return transcribe_chunks(self._sarvam, audio_chunks, bcp47_language)
