"""Caches per-language translations of a complaint's canonical English text and AI summary.

`Complaint.translated_text` and `Complaint.summary` are always English (see complaint_agent.py).
Every other display language a complaint gets viewed in is translated on demand and cached here
— both the full text and the summary together, in one cache row — so the same complaint/language
pair only ever costs the translation calls once, not on every view.
"""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import Complaint, ComplaintTranslation
from backend.services.translation_service import TranslationService

logger = logging.getLogger(__name__)


def get_display_text_and_summary(
    db: Session,
    complaint: Complaint,
    language_code: str,
    translation_service: TranslationService,
) -> tuple[str, str]:
    """Return the complaint's (text, summary) in `language_code`, translating/caching on first view.

    Args:
        db: Active database session.
        complaint: The complaint to display.
        language_code: Short language code to display it in, e.g. "hi".
        translation_service: Service used to translate on a cache miss.

    Returns:
        A (text, summary) tuple, both in the requested language.

    Raises:
        AIServiceError: If language_code != "en", there's no cached translation yet, and a
            live translation call fails. Callers decide how to fall back (see routes/complaints.py).
    """
    if language_code == "en":
        return complaint.translated_text, complaint.summary

    cached = (
        db.query(ComplaintTranslation)
        .filter(
            ComplaintTranslation.complaint_id == complaint.id,
            ComplaintTranslation.language_code == language_code,
        )
        .first()
    )
    # translated_summary is nullable only for rows cached before this field existed (see
    # models.py) — treat a row missing it the same as a full cache miss, so it backfills once.
    if cached is not None and cached.translated_summary is not None:
        return cached.translated_text, cached.translated_summary

    # Cache miss: translate live, then store it. A failed translation is deliberately left
    # uncached so the next view retries it, rather than getting permanently stuck.
    text = translation_service.to_language(complaint.translated_text, language_code)
    summary = translation_service.to_language(complaint.summary, language_code)

    if cached is not None:
        cached.translated_text = text
        cached.translated_summary = summary
    else:
        db.add(
            ComplaintTranslation(
                complaint_id=complaint.id,
                language_code=language_code,
                translated_text=text,
                translated_summary=summary,
            )
        )
    try:
        db.commit()
    except IntegrityError:
        # LIVE-REPORTED (production): two near-simultaneous requests for the SAME
        # (complaint_id, language_code) both saw a cache miss (this function has no locking
        # between the read above and this write), both paid for a real translation call, and
        # both tried to insert a row -- the second one violates ComplaintTranslation's own
        # uq_complaint_translation_lang unique constraint and previously crashed the request with
        # an unhandled IntegrityError. Recover instead: the OTHER request's commit already won and
        # is sitting in the table, so roll back this one's failed write and just return that.
        # Doesn't eliminate the rare double translation-call itself (the race window is the time
        # between the read and this commit, not something closeable without a per-key lock) -- only
        # the crash, which is the actual user-facing failure.
        db.rollback()
        winner = (
            db.query(ComplaintTranslation)
            .filter(
                ComplaintTranslation.complaint_id == complaint.id,
                ComplaintTranslation.language_code == language_code,
            )
            .first()
        )
        if winner is not None and winner.translated_summary is not None:
            logger.info(
                "Lost a concurrent-translation race (complaint_id=%s, language=%s); using the "
                "other request's cached result instead of the one just translated here.",
                complaint.id, language_code,
            )
            return winner.translated_text, winner.translated_summary
        # Unreachable in practice (the constraint only fires when a row already exists with
        # translated_summary set -- see the cache-miss check above), but fall back to this
        # request's own freshly-translated result rather than raising if it ever is.
    logger.info(
        "Cached translation (complaint_id=%s, language=%s)", complaint.id, language_code
    )
    return text, summary
