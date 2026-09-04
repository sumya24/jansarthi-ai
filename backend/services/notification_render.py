"""Renders a Notification's title/message FRESH, in whatever language is asked for, from the real
data it's actually about (a complaint's own translatable text + its ward) -- the same "translate
on demand, cache the result" principle complaint_translation_cache.py already uses for a
complaint's own on-page text, applied here to notifications too.

LIVE-REPORTED: a Notification's title/message used to be composed ONCE, at creation time, in
whatever language the recipient happened to be using that day, then stored as plain text forever
-- switching your language later never changed it, since nothing re-read or re-translated it.
Confirmed directly: a citizen who used the app in Marathi, then switched to Odia, still saw old
notifications' text (and the raw English ward suffix) exactly as it was written weeks earlier.

Fixed by no longer trusting the stored `title`/`message` columns for display at all (see
routes/notifications.py's `_to_response`) -- every notification whose `type` is one of the ones
below gets rebuilt fresh, in the CURRENT viewer's `preferred_language`, from `complaint_id` (which
every real notification of these types already has, including every notification that existed
before this fix) -- so both a brand new notification AND a notification from months ago render
correctly the moment they're viewed, with no separate backfill/migration needed. The functions
here are the single source of truth for this text, used both at creation time (routes/
complaints.py, assignment_service.py -- so a freshly created notification's stored fallback text,
if ever read without going through this render path, still matches) and at read time.

Extended to COMPLAINT_REJECTED (admin-facing) too, via `related_rejection_id`: that message names
the SPECIFIC worker who rejected, and a complaint can be rejected more than once over its life
(reassignment chain), so `complaint_id` alone can't say which rejection a given notification was
about -- `related_rejection_id` (a new column, see models.py's Notification docstring) resolves
this unambiguously. A COMPLAINT_REJECTED row from before this column existed has no
related_rejection_id (NULL) and keeps its original, frozen-at-creation text instead -- an honest
smaller gap for old rows only, not a wrong guess.

Still NOT extended to AI_ALERT: it has no complaint_id at all, nothing to rebuild from."""

import logging
from typing import Literal

from sqlalchemy.orm import Session

from backend.models import Complaint, ComplaintRejection, User
from backend.services.complaint_translation_cache import get_display_text_and_summary
from backend.services.email_service import _email_strings
from backend.services.location_names import localize_ward_text
from backend.services.sarvam_client import AIServiceError
from backend.services.translation_service import TranslationService

logger = logging.getLogger(__name__)

_translation_service = TranslationService()
_SNIPPET_LENGTH = 80

CitizenEvent = Literal["accepted", "started", "resolved"]

# Notification.type -> the citizen event it corresponds to, for render_citizen_notification().
CITIZEN_EVENT_BY_TYPE: dict[str, CitizenEvent] = {
    "COMPLAINT_ACCEPTED": "accepted",
    "COMPLAINT_STARTED": "started",
    "COMPLAINT_RESOLVED": "resolved",
}
# Notification.type values render_worker_notification() can rebuild (see its own `is_reassignment`
# param -- driven directly by which of these two the stored row already says, not recomputed).
WORKER_NOTIFICATION_TYPES = {"NEW_ASSIGNMENT", "REASSIGNED"}
# The one admin-facing type render_admin_rejection_notification() can rebuild -- only when the row
# also has a related_rejection_id (see that function's own docstring).
ADMIN_REJECTION_TYPE = "COMPLAINT_REJECTED"


def _snippet(db: Session, complaint: Complaint, lang: str) -> str:
    """The complaint's own summary/translated text, translated into `lang` on demand (cached --
    see get_display_text_and_summary), truncated the same way every notification message already
    was. Falls back to the raw English snippet on a translation failure, same as every existing
    caller of that function."""
    snippet = (complaint.summary or complaint.translated_text or "").strip()
    if lang and lang != "en" and snippet:
        try:
            display_text, display_summary = get_display_text_and_summary(db, complaint, lang, _translation_service)
            snippet = (display_summary or display_text or snippet).strip()
        except AIServiceError as exc:
            logger.error("On-read translation failed for complaint %s notification: %s", complaint.id, exc)
    if len(snippet) > _SNIPPET_LENGTH:
        snippet = snippet[:_SNIPPET_LENGTH].rstrip() + "…"
    return snippet


def _ward_label(complaint: Complaint, lang: str) -> str:
    location_label = _email_strings(lang)["label.location"]
    if not complaint.ward:
        return f"{location_label}: —"
    return f"{location_label}: {localize_ward_text(complaint.ward, lang)}"


def render_citizen_notification(db: Session, complaint: Complaint, event: CitizenEvent, lang: str) -> tuple[str, str]:
    """Returns (title, message) for a citizen's own complaint accept/start/resolve notification,
    in `lang` -- freshly computed every call, safe to call at creation time or at read time."""
    title = _email_strings(lang)[f"heading.{event}"]
    snippet = _snippet(db, complaint, lang)
    ward_label = _ward_label(complaint, lang)
    message = f"{snippet} — {ward_label}" if snippet else ward_label
    return title, message


def render_worker_notification(db: Session, complaint: Complaint, worker_lang: str, is_reassignment: bool) -> tuple[str, str]:
    """Returns (title, message) for a worker's new-assignment/reassignment notification, in
    `worker_lang` -- freshly computed every call, safe to call at creation time or at read time."""
    strings = _email_strings(worker_lang)
    title = strings["heading.reassigned"] if is_reassignment else strings["heading.assigned"]
    snippet = _snippet(db, complaint, worker_lang)
    ward_label = _ward_label(complaint, worker_lang)
    message = f"{snippet} — {ward_label}" if snippet else ward_label
    return title, message


def render_admin_rejection_notification(db: Session, rejection: ComplaintRejection, admin_lang: str) -> tuple[str, str]:
    """Returns (title, message) for the admin broadcast of one SPECIFIC worker rejection, in
    `admin_lang` -- freshly computed every call. Takes the ComplaintRejection row itself (not just
    a complaint) since that's the only way to know which worker rejected -- a complaint can be
    rejected more than once over its life, and `rejection.worker_id` is what disambiguates.

    Raises the same way a missing complaint/worker row would (an AttributeError on None) if the
    rejection's own complaint or worker has since been deleted -- callers (routes/notifications.py)
    already wrap this in a try/except and fall back to the stored text on any failure, same
    contract as every other render_* function here."""
    complaint = db.query(Complaint).filter(Complaint.id == rejection.complaint_id).first()
    worker = db.query(User).filter(User.id == rejection.worker_id).first()
    strings = _email_strings(admin_lang)
    title = strings["heading.workerRejected"]
    message = strings["message.workerRejected"].format(
        worker=worker.full_name, ward=localize_ward_text(complaint.ward, admin_lang)
    )
    return title, message
