"""Tests for backend/repositories/ai_request_log_repository.py's AI-monitoring alerting
(check_and_fire_alerts()): rolling error-rate/latency detection, the cooldown that prevents
spamming a notification every request, and that every admin (not just one) gets notified.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.models import AiAlertState, AiRequestLog, Notification, User
from backend.repositories import ai_request_log_repository
from backend.services.auth_service import hash_password

_BASE_ROW = {
    "request_id": "abc123",
    "langsmith_trace_id": None,
    "conversation_id": None,
    "intent": "TYPE_B_SERVICE_INFO",
    "service_category": "STREETLIGHTS",
    "routed_to": "RAG",
    "success": True,
    "error_type": None,
    "latency_ms": 100.0,
}


def _seed_requests(db_session, rows: list[dict]) -> None:
    db = db_session()
    for row in rows:
        db.add(AiRequestLog(**row))
    db.commit()
    db.close()


def _make_admin(db_session, phone: str, full_name: str = "Test Admin", preferred_language: str = "en") -> User:
    db = db_session()
    admin = User(full_name=full_name, phone=phone, password_hash=hash_password("adminpass"), role="admin", preferred_language=preferred_language)
    db.add(admin)
    db.commit()
    db.refresh(admin)
    db.close()
    return admin


def test_no_alert_with_fewer_than_window_size_requests(db_session):
    _make_admin(db_session, "9999999999")
    _seed_requests(db_session, [{**_BASE_ROW, "success": False} for _ in range(5)])  # < 20, all failing

    db = db_session()
    fired = ai_request_log_repository.check_and_fire_alerts(db)
    db.close()

    assert fired == []


def test_no_alert_when_traffic_is_healthy(db_session):
    _make_admin(db_session, "9999999999")
    _seed_requests(db_session, [{**_BASE_ROW, "success": True, "latency_ms": 200.0} for _ in range(20)])

    db = db_session()
    fired = ai_request_log_repository.check_and_fire_alerts(db)
    notifications = db.query(Notification).all()
    db.close()

    assert fired == []
    assert notifications == []


def test_high_error_rate_fires_and_notifies_every_admin(db_session):
    """Also covers a LIVE-REPORTED gap: this title was hardcoded English regardless of each
    admin's own preferred_language -- admin1 (English) and admin2 (Marathi) must each get the
    title in their OWN language, confirming this is localized per-recipient inside the broadcast
    loop (see ai_request_log_repository.py's _try_fire())."""
    admin1 = _make_admin(db_session, "9999999991", "Admin One", preferred_language="en")
    admin2 = _make_admin(db_session, "9999999992", "Admin Two", preferred_language="mr")
    # 20 requests, 5 failing = 25% error rate, above the 20% threshold.
    rows = [{**_BASE_ROW, "success": (i >= 5)} for i in range(20)]
    _seed_requests(db_session, rows)

    db = db_session()
    fired = ai_request_log_repository.check_and_fire_alerts(db)
    notifications = db.query(Notification).order_by(Notification.recipient_id).all()
    db.close()

    assert fired == ["HIGH_ERROR_RATE"]
    assert len(notifications) == 2
    assert {n.recipient_id for n in notifications} == {admin1.id, admin2.id}
    assert all(n.type == "AI_ALERT" for n in notifications)
    assert all(n.complaint_id is None for n in notifications)
    assert "25%" in notifications[0].message
    titles_by_recipient = {n.recipient_id: n.title for n in notifications}
    assert titles_by_recipient[admin1.id] == "High AI error rate"
    assert titles_by_recipient[admin2.id] == "एआयचा उच्च त्रुटी दर"


def test_high_latency_fires(db_session):
    _make_admin(db_session, "9999999999")
    rows = [{**_BASE_ROW, "success": True, "latency_ms": 15000.0} for _ in range(20)]
    _seed_requests(db_session, rows)

    db = db_session()
    fired = ai_request_log_repository.check_and_fire_alerts(db)
    db.close()

    assert fired == ["HIGH_LATENCY"]


def test_both_alerts_can_fire_together(db_session):
    _make_admin(db_session, "9999999999")
    rows = [{**_BASE_ROW, "success": (i >= 10), "latency_ms": 15000.0} for i in range(20)]  # 50% failing AND slow
    _seed_requests(db_session, rows)

    db = db_session()
    fired = ai_request_log_repository.check_and_fire_alerts(db)
    db.close()

    assert set(fired) == {"HIGH_ERROR_RATE", "HIGH_LATENCY"}


def test_alert_does_not_refire_within_cooldown(db_session):
    _make_admin(db_session, "9999999999")
    rows = [{**_BASE_ROW, "success": False} for _ in range(20)]
    _seed_requests(db_session, rows)

    db = db_session()
    first = ai_request_log_repository.check_and_fire_alerts(db)
    second = ai_request_log_repository.check_and_fire_alerts(db)
    notifications = db.query(Notification).all()
    db.close()

    assert first == ["HIGH_ERROR_RATE"]
    assert second == []  # still within the cooldown window -- no second notification
    assert len(notifications) == 1


def test_alert_refires_after_cooldown_expires(db_session):
    _make_admin(db_session, "9999999999")
    rows = [{**_BASE_ROW, "success": False} for _ in range(20)]
    _seed_requests(db_session, rows)

    db = db_session()
    ai_request_log_repository.check_and_fire_alerts(db)
    # Simulate the cooldown having expired by backdating the alert state directly.
    state = db.query(AiAlertState).filter(AiAlertState.alert_type == "HIGH_ERROR_RATE").first()
    state.last_fired_at = datetime.now(timezone.utc) - timedelta(minutes=31)
    db.commit()
    db.close()

    db = db_session()
    fired_again = ai_request_log_repository.check_and_fire_alerts(db)
    notifications = db.query(Notification).all()
    db.close()

    assert fired_again == ["HIGH_ERROR_RATE"]
    assert len(notifications) == 2  # one from each firing


def test_check_and_fire_alerts_never_raises_on_db_failure(db_session):
    """Best-effort, like record_ai_request() -- see this module's docstring."""
    db = db_session()
    db.close()  # force any query against this session to fail
    fired = ai_request_log_repository.check_and_fire_alerts(db)
    assert fired == []
