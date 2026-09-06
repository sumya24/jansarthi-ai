"""Sends a citizen the lifecycle email for one of their complaint's status changes.

Factored out of routes/complaints.py so both the "Report an Issue" form's POST /complaints route
and Ask Sarthi's complaint_flow_node (backend/services/orchestration/nodes.py) call the exact same
logic for the "created" event -- LIVE-REPORTED: a complaint filed via Ask Sarthi never sent this
email at all, since nodes.py built and assigned the complaint itself but never called this. Moving
the one shared implementation here (rather than duplicating it) means both filing paths can never
drift apart on translation/fallback behavior again.
"""

import logging
from typing import Literal

from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import Complaint, ComplaintUpdate, User
from backend.services.complaint_translation_cache import get_display_text_and_summary
from backend.services.complaint_update_translation_cache import get_display_text as get_display_update_text
from backend.services.email_service import EmailServiceError, send_complaint_status_email
from backend.services.location_names import localize_ward_text
from backend.services.sarvam_client import AIServiceError
from backend.services.translation_service import TranslationService

logger = logging.getLogger(__name__)


def send_lifecycle_email_best_effort(
    db: Session,
    complaint: Complaint,
    event: Literal["created", "accepted", "started", "resolved"],
    translation_service: TranslationService | None,
    worker_update: ComplaintUpdate | None = None,
) -> None:
    """Fire-and-forget: sends the citizen a real email for one of their complaint's lifecycle
    moments, if (and only if) they have a verified email -- silently skipped otherwise, exactly
    the same as every other email_verified check in this codebase, never an error. Never raises:
    an EmailServiceError (SMTP not configured, or a real send failure) is caught and logged here,
    not surfaced to the caller -- see send_complaint_status_email's own docstring for why this
    must never fail the actual accept/start/resolve/create action it's attached to.

    Renders in the citizen's own preferred_language, translating the summary the same way the
    on-page complaint detail already does for that citizen (get_display_text_and_summary). Falls
    back to the stored English summary/note if `translation_service` is None (a caller that has
    none available, e.g. a test double) or on an AIServiceError, exactly like the on-page views do,
    so a translation hiccup degrades to an English email rather than losing the send.
    """
    citizen = db.query(User).filter(User.id == int(complaint.citizen_id)).first()
    if citizen is None or not citizen.email or not citizen.email_verified:
        return
    lang = citizen.preferred_language or "en"
    summary = complaint.summary
    if lang != "en" and translation_service is not None:
        try:
            _, summary = get_display_text_and_summary(db, complaint, lang, translation_service)
        except AIServiceError as exc:
            logger.error("Complaint %s: failed to translate lifecycle email into %s: %s", complaint.id, lang, exc)
            summary = complaint.summary
    worker_note = worker_update.text if worker_update is not None else None
    # A ComplaintUpdate has no "always canonical English" guarantee the way Complaint.summary does
    # (see complaint_update_translation_cache.py's own docstring) -- a worker's note can itself be
    # typed in ANY language, so `lang != "en"` is the wrong condition to skip translation on here.
    # Attempted whenever a worker_update exists at all, regardless of the citizen's own `lang`.
    if worker_update is not None and translation_service is not None:
        try:
            worker_note = get_display_update_text(db, worker_update, lang, translation_service)
        except AIServiceError as exc:
            logger.error("Complaint %s: failed to translate lifecycle email's worker note into %s: %s", complaint.id, lang, exc)
            worker_note = worker_update.text
    cta_url = f"{settings.FRONTEND_BASE_URL}/citizen/complaints/{complaint.id}" if settings.FRONTEND_BASE_URL else None
    try:
        send_complaint_status_email(
            citizen.email, event, f"JM-{complaint.id:05d}", summary or "", localize_ward_text(complaint.ward, lang) or "",
            cta_url=cta_url, lang=lang, worker_note=worker_note,
        )
    except EmailServiceError as exc:
        logger.error("Complaint %s: failed to send '%s' lifecycle email: %s", complaint.id, event, exc)
