"""API endpoints for a user's own in-app notifications -- see models.py's `Notification`
docstring for scope (originally worker-only; citizens/admins are notified too now, see
routes/complaints.py's accept_complaint/start_work/resolve_complaint/reject_complaint).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.deps import get_current_user
from backend.models import Complaint, ComplaintRejection, Notification, User
from backend.repositories import notification_repository
from backend.services.notification_render import (
    ADMIN_REJECTION_TYPE,
    CITIZEN_EVENT_BY_TYPE,
    WORKER_NOTIFICATION_TYPES,
    render_admin_rejection_notification,
    render_citizen_notification,
    render_worker_notification,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationResponse(BaseModel):
    id: int
    type: str
    title: str
    message: str
    complaint_id: int | None
    created_at: str
    read_at: str | None

    model_config = ConfigDict(from_attributes=True)


class NotificationListResponse(BaseModel):
    notifications: list[NotificationResponse]
    unread_count: int


def _to_response(n: Notification, title: str, message: str) -> NotificationResponse:
    return NotificationResponse(
        id=n.id, type=n.type, title=title, message=message, complaint_id=n.complaint_id,
        created_at=n.created_at.isoformat(),
        read_at=n.read_at.isoformat() if n.read_at else None,
    )


# LIVE-REPORTED: a notification's title/message used to be trusted verbatim from the stored
# columns -- correct the moment it was created, in whatever language the recipient was using THAT
# day, but frozen from then on: switching your language later never changed anything already in
# your notification list. Confirmed directly, live: a citizen who'd used the app in Marathi, then
# switched to Odia, still saw old notifications' text (title, complaint snippet, AND the ward
# suffix) exactly as first written, weeks earlier.
#
# Fixed by never trusting the stored text for display at all, for the notification types that can
# be rebuilt: `complaint_id` (citizen/worker types) or `related_rejection_id` (the admin rejection
# type) is enough to recompute the exact same title/message fresh, in
# `current_user.preferred_language` as it is RIGHT NOW -- see notification_render.py's own
# docstring for the full reasoning, including why AI_ALERT (and any COMPLAINT_REJECTED row from
# before `related_rejection_id` existed) is deliberately left on the old, frozen-at-creation
# behavior. One batched query per lookup table (not one query per row) keeps this at the same
# query-count shape GET /complaints already uses for a list, not a new N+1.
def _render_notifications(db: Session, rows: list[Notification], lang: str) -> list[NotificationResponse]:
    recomputable_complaint_ids = {
        n.complaint_id
        for n in rows
        if n.complaint_id is not None and (n.type in CITIZEN_EVENT_BY_TYPE or n.type in WORKER_NOTIFICATION_TYPES)
    }
    complaints_by_id: dict[int, Complaint] = {}
    if recomputable_complaint_ids:
        complaints_by_id = {
            c.id: c for c in db.query(Complaint).filter(Complaint.id.in_(recomputable_complaint_ids)).all()
        }

    recomputable_rejection_ids = {
        n.related_rejection_id
        for n in rows
        if n.type == ADMIN_REJECTION_TYPE and n.related_rejection_id is not None
    }
    rejections_by_id: dict[int, ComplaintRejection] = {}
    if recomputable_rejection_ids:
        rejections_by_id = {
            r.id: r for r in db.query(ComplaintRejection).filter(ComplaintRejection.id.in_(recomputable_rejection_ids)).all()
        }

    responses: list[NotificationResponse] = []
    for n in rows:
        title, message = n.title, n.message  # the honest fallback every notification already had
        try:
            if n.type == ADMIN_REJECTION_TYPE:
                rejection = rejections_by_id.get(n.related_rejection_id) if n.related_rejection_id is not None else None
                if rejection is not None:
                    title, message = render_admin_rejection_notification(db, rejection, lang)
            elif n.complaint_id is not None:
                complaint = complaints_by_id.get(n.complaint_id)
                if complaint is not None:
                    if n.type in CITIZEN_EVENT_BY_TYPE:
                        title, message = render_citizen_notification(db, complaint, CITIZEN_EVENT_BY_TYPE[n.type], lang)
                    elif n.type in WORKER_NOTIFICATION_TYPES:
                        title, message = render_worker_notification(db, complaint, lang, n.type == "REASSIGNED")
        except Exception as exc:  # noqa: BLE001 -- never let a rendering hiccup break the whole list
            logger.error("Failed to re-render notification %s live; falling back to stored text: %s", n.id, exc)
            title, message = n.title, n.message
        responses.append(_to_response(n, title, message))
    return responses


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificationListResponse:
    """The current user's own notifications, most recent first, plus their unread count. Every
    role can call this -- scoped to `recipient_id == current_user.id` regardless of role.
    """
    rows = notification_repository.list_notifications(db, current_user.id, limit=min(max(limit, 1), 200))
    unread = notification_repository.count_unread(db, current_user.id)
    lang = current_user.preferred_language or "en"
    return NotificationListResponse(notifications=_render_notifications(db, rows, lang), unread_count=unread)


@router.post("/{notification_id}/read", response_model=NotificationResponse)
def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificationResponse:
    """Marks one of the current user's own notifications read. Idempotent -- marking an
    already-read notification again just returns it unchanged (see notification_repository.
    mark_read). A user can never mark someone else's notification read (404, not 403 -- doesn't
    reveal whether the id belongs to another user at all).
    """
    notification = (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.recipient_id == current_user.id)
        .first()
    )
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found.")
    updated = notification_repository.mark_read(db, notification)
    lang = current_user.preferred_language or "en"
    return _render_notifications(db, [updated], lang)[0]
