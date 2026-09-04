"""Assigns a complaint to a worker, and reassigns it when the current worker rejects it.

A ward can have more than one worker. Assignment always tries workers in the same ward, in a
stable order (lowest id first — i.e. whoever was added to that ward first), skipping anyone who
has already rejected this specific complaint. If every worker in the ward has rejected it (or
there are no workers in that ward at all), the complaint goes to "pending" with no assigned
worker until an admin adds one or an existing one's ward changes.

Location migration note: candidates are matched by structured `ward_id` when the complaint has
one (set by routes/complaints.py at creation time, via LocationResolver), falling back to the
text `ward` match otherwise. This is additive, not a behavior change — every complaint/worker
pair that matched by text before this migration still matches by text now (nothing here removes
that path); `ward_id` matching only ever adds coverage for rows the new location system has
actually resolved. See docs/location_migration_plan.md §H.

The text `ward` match is case-insensitive ("Kolhapur" and "kolhapur" are the same ward) —
originally an exact, case-sensitive match, changed after a live-data audit found two citizens'
complaints going unmatched purely over letter casing (see LOCATION_DATA_MOCK_VS_REAL_FINDINGS.md
§2). Still a plain text comparison otherwise -- no trimming, no punctuation normalization, since
those weren't the reported problem.

Worker-workflow phase: this is also the sole place that creates a `Notification` (NEW_ASSIGNMENT
for a fresh assignment, REASSIGNED after a rejection) and appends a `ComplaintStatusHistory` row
-- both genuinely belong here, not in routes/complaints.py, since a complaint is assigned from
two different call sites (initial creation, and rejection) and this function is what both already
funnel through. Deliberately does NOT include a service-category label in the notification text
(e.g. "Streetlight complaint") -- `Complaint` has no service-category field (that concept exists
only in the separate Ask Sarthi/RAG intent classifier, over free-text questions, not over
filed complaints), and adding one would mean either a schema change or reusing RAG-adjacent
classification code for a notification-copy nicety, both out of scope for this phase (see the
final report's "deferred" section). The notification message uses the complaint's own summary
snippet and ward instead -- real data already on hand, nothing inferred.
"""

import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models import Complaint, ComplaintRejection, User
from backend.repositories import complaint_workflow_repository, notification_repository
from backend.services.complaint_translation_cache import get_display_text_and_summary
from backend.services.email_service import _email_strings
from backend.services.sarvam_client import AIServiceError
from backend.services.translation_service import TranslationService

logger = logging.getLogger(__name__)

_SUMMARY_SNIPPET_LENGTH = 80
_translation_service = TranslationService()


def _candidates(db: Session, complaint: Complaint) -> list[User]:
    """Workers eligible for `complaint`, stable-ordered by id.

    Tries `ward_id` first (structured, only possible where both the complaint and the worker
    resolved to the same seeded ward) — if that yields zero candidates, falls back to the
    case-insensitive `ward` text match, so a worker who hasn't been through location migration
    (or a ward the new system couldn't resolve) is never silently excluded.
    """
    if complaint.ward_id is not None:
        by_id = (
            db.query(User)
            .filter(User.role == "worker", User.ward_id == complaint.ward_id)
            .order_by(User.id.asc())
            .all()
        )
        if by_id:
            return by_id

    if complaint.ward is None:
        ward_filter = User.ward.is_(None)
    else:
        ward_filter = func.lower(User.ward) == func.lower(complaint.ward)

    return (
        db.query(User)
        .filter(User.role == "worker", ward_filter)
        .order_by(User.id.asc())
        .all()
    )


def assign_next_worker(db: Session, complaint: Complaint) -> None:
    """Assign `complaint` to the next eligible worker in its ward, or mark it pending.

    Args:
        db: Active database session.
        complaint: The complaint to (re)assign. Mutated in place and committed.
    """
    # Any prior rejection means this call is a REASSIGNMENT, not the complaint's first
    # assignment -- used below to pick the right notification type/status-history note. Computed
    # before the ward-less early return too, so a complaint with no ward that's somehow already
    # been rejected once (shouldn't normally happen, but not assumed impossible) still gets an
    # accurate status-history note rather than a hardcoded "created" one.
    already_rejected_ids = {
        row.worker_id
        for row in db.query(ComplaintRejection).filter(ComplaintRejection.complaint_id == complaint.id).all()
    }
    is_reassignment = bool(already_rejected_ids)
    previous_status = complaint.status

    if not complaint.ward and complaint.ward_id is None:
        complaint.status = "pending"
        complaint.assigned_worker_id = None
        db.commit()
        complaint_workflow_repository.record_status_change(
            db, complaint, from_status=previous_status, to_status="pending", actor_role="system", actor_user_id=None,
            note="No ward on this complaint; nothing to assign.",
        )
        return

    candidates = _candidates(db, complaint)
    next_worker = next((w for w in candidates if w.id not in already_rejected_ids), None)

    if next_worker is None:
        complaint.status = "pending"
        complaint.assigned_worker_id = None
        logger.info("Complaint %s has no eligible worker left in ward %r; now pending", complaint.id, complaint.ward)
        db.commit()
        complaint_workflow_repository.record_status_change(
            db, complaint, from_status=previous_status, to_status="pending", actor_role="system", actor_user_id=None,
            note="No eligible worker left in ward." if is_reassignment else "No eligible worker in ward.",
        )
        return

    complaint.status = "assigned"
    complaint.assigned_worker_id = next_worker.id
    logger.info("Complaint %s assigned to worker %s (ward=%r)", complaint.id, next_worker.id, complaint.ward)
    db.commit()

    complaint_workflow_repository.record_status_change(
        db, complaint, from_status=previous_status, to_status="assigned", actor_role="system", actor_user_id=None,
        note="Reassigned after rejection." if is_reassignment else "Assigned to worker.",
    )

    # LIVE-REPORTED: this title/ward-label was hardcoded English regardless of the worker's own
    # preferred_language -- the exact same gap found and fixed for citizen notifications (see
    # routes/complaints.py's _citizen_notification_title/_citizen_notification_message). Reuses
    # the same shared, centralized per-language table (email_service.py's _email_strings) rather
    # than a second, independently-invented one.
    #
    # LIVE-REPORTED, part two: fixing the title/label above wasn't the whole gap -- the snippet
    # itself was complaint.summary/translated_text verbatim, both always canonical English (see
    # ComplaintAgent), so a worker using any non-English UI still got a correctly-translated
    # "Location:" label glued onto a raw English complaint snippet. Same on-read translation cache
    # every other view of this complaint's text already reads through; falls back to the raw
    # English snippet on a translation failure, same as every other caller of that function.
    worker_lang = next_worker.preferred_language or "en"
    strings = _email_strings(worker_lang)
    snippet = (complaint.summary or complaint.translated_text or "").strip()
    if worker_lang != "en" and snippet:
        try:
            display_text, display_summary = get_display_text_and_summary(db, complaint, worker_lang, _translation_service)
            snippet = (display_summary or display_text or snippet).strip()
        except AIServiceError as exc:
            logger.error("On-read translation failed for complaint %s worker notification: %s", complaint.id, exc)
    if len(snippet) > _SUMMARY_SNIPPET_LENGTH:
        snippet = snippet[:_SUMMARY_SNIPPET_LENGTH].rstrip() + "…"
    location_label = strings["label.location"]
    ward_label = f"{location_label}: {complaint.ward}" if complaint.ward else f"{location_label}: —"
    notification_repository.create_notification(
        db,
        recipient_id=next_worker.id,
        type="REASSIGNED" if is_reassignment else "NEW_ASSIGNMENT",
        title=strings["heading.reassigned"] if is_reassignment else strings["heading.assigned"],
        message=f"{snippet} — {ward_label}" if snippet else ward_label,
        complaint_id=complaint.id,
    )
