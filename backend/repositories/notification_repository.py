"""Repository layer for `Notification`. Created from several places, one per event type -- see
models.py's `Notification` docstring for the full list: assignment_service.py (NEW_ASSIGNMENT/
REASSIGNED, to a worker), routes/complaints.py (COMPLAINT_ACCEPTED/STARTED/RESOLVED, to the
citizen; COMPLAINT_REJECTED, broadcast to every admin), and
repositories/ai_request_log_repository.py (AI_ALERT, broadcast to every admin).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.models import Notification


def create_notification(
    db: Session,
    *,
    recipient_id: int,
    type: str,
    title: str,
    message: str,
    complaint_id: int | None,
    related_rejection_id: int | None = None,
) -> Notification:
    notification = Notification(
        recipient_id=recipient_id, type=type, title=title, message=message, complaint_id=complaint_id,
        related_rejection_id=related_rejection_id,
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return notification


def list_notifications(db: Session, recipient_id: int, limit: int = 50) -> list[Notification]:
    return (
        db.query(Notification)
        .filter(Notification.recipient_id == recipient_id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
        .all()
    )


def count_unread(db: Session, recipient_id: int) -> int:
    return (
        db.query(Notification)
        .filter(Notification.recipient_id == recipient_id, Notification.read_at.is_(None))
        .count()
    )


def mark_read(db: Session, notification: Notification) -> Notification:
    """Marks one notification read -- idempotent (re-marking an already-read notification just
    leaves its original read_at untouched, never bumped forward)."""
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(notification)
    return notification
