"""Tests for the Admin "AI Monitoring" dashboard: the `AiRequestLog` repository
(backend/repositories/ai_request_log_repository.py) and the /admin/ai-monitoring* endpoints
(backend/routes/admin.py).

Deliberately does not require a working LangSmith connection anywhere in this file -- these
metrics are sourced entirely from the local `ai_request_logs` table, which is the whole point
(see AiRequestLog's docstring in models.py): the Admin dashboard must show real numbers whether
or not LangSmith is configured. `trace_url` is exercised separately via
`LANGSMITH_TRACE_URL_TEMPLATE`, which needs no live LangSmith connection either -- it's pure
string formatting (see tracing.get_trace_url()).
"""

from __future__ import annotations

from backend.config import settings
from backend.models import AiRequestLog
from backend.repositories import ai_request_log_repository


def _login(client, phone, password):
    response = client.post("/auth/login", json={"identifier": phone, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def _seed_logs(db_session, rows: list[dict]) -> None:
    db = db_session()
    for row in rows:
        db.add(AiRequestLog(**row))
    db.commit()
    db.close()


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


# --- repository: aggregation ---


def test_summary_is_all_zero_with_no_requests(db_session):
    db = db_session()
    summary = ai_request_log_repository.get_ai_monitoring_summary(db)
    db.close()
    assert summary == {
        "total_requests": 0,
        "successful_requests": 0,
        "failed_requests": 0,
        "error_rate": 0.0,
        "average_latency_ms": 0.0,
        "rag_requests": 0,
        "complaint_requests": 0,
        "status_requests": 0,
        "out_of_scope_requests": 0,
        "clarification_requests": 0,
        "latency_alert_threshold_ms": ai_request_log_repository._LATENCY_ALERT_THRESHOLD_MS,
        "needs_review_count": 0,
    }


def test_summary_aggregates_counts_and_rates(db_session):
    _seed_logs(
        db_session,
        [
            {**_BASE_ROW, "routed_to": "RAG", "success": True, "latency_ms": 100.0},
            {**_BASE_ROW, "routed_to": "RAG", "success": True, "latency_ms": 200.0},
            {**_BASE_ROW, "routed_to": "COMPLAINT_CREATED", "success": True, "latency_ms": 300.0},
            {**_BASE_ROW, "routed_to": "COMPLAINT_CREATION_FAILED", "success": True, "latency_ms": 50.0},
            {**_BASE_ROW, "routed_to": "COMPLAINT_STATUS_API", "success": True, "latency_ms": 40.0},
            {**_BASE_ROW, "routed_to": "NONE_OUT_OF_SCOPE", "success": True, "latency_ms": 10.0},
            {**_BASE_ROW, "routed_to": "NONE_CLARIFICATION_NEEDED", "success": True, "latency_ms": 20.0},
            {**_BASE_ROW, "routed_to": "ERROR", "success": False, "error_type": "AIServiceError", "latency_ms": 5.0},
        ],
    )
    db = db_session()
    summary = ai_request_log_repository.get_ai_monitoring_summary(db)
    db.close()

    assert summary["total_requests"] == 8
    assert summary["successful_requests"] == 7
    assert summary["failed_requests"] == 1
    assert summary["error_rate"] == 1 / 8
    assert summary["rag_requests"] == 2
    # Both COMPLAINT_CREATED and COMPLAINT_CREATION_FAILED count as complaint-shaped requests.
    assert summary["complaint_requests"] == 2
    assert summary["status_requests"] == 1
    assert summary["out_of_scope_requests"] == 1
    assert summary["clarification_requests"] == 1
    assert summary["average_latency_ms"] == sum([100, 200, 300, 50, 40, 10, 20, 5]) / 8


def test_summary_counts_needs_review_requests(db_session):
    _seed_logs(
        db_session,
        [
            {**_BASE_ROW, "routed_to": "NONE_OUT_OF_SCOPE", "needs_review": True},
            {**_BASE_ROW, "routed_to": "RAG", "needs_review": True},  # insufficient_knowledge case
            {**_BASE_ROW, "routed_to": "RAG", "needs_review": False},
        ],
    )
    db = db_session()
    summary = ai_request_log_repository.get_ai_monitoring_summary(db)
    db.close()
    assert summary["needs_review_count"] == 2


def test_get_recent_needs_review_requests_only_returns_flagged_rows_newest_first(db_session):
    _seed_logs(
        db_session,
        [
            {**_BASE_ROW, "request_id": "flagged-1", "needs_review": True},
            {**_BASE_ROW, "request_id": "not-flagged", "needs_review": False},
            {**_BASE_ROW, "request_id": "flagged-2", "needs_review": True},
        ],
    )
    db = db_session()
    rows = ai_request_log_repository.get_recent_needs_review_requests(db)
    db.close()
    assert [r.request_id for r in rows] == ["flagged-2", "flagged-1"]


def test_get_recent_ai_requests_orders_newest_first_and_respects_limit(db_session):
    _seed_logs(db_session, [{**_BASE_ROW, "request_id": f"req-{i}"} for i in range(5)])
    db = db_session()
    rows = ai_request_log_repository.get_recent_ai_requests(db, limit=3)
    db.close()
    assert len(rows) == 3
    assert rows[0].request_id == "req-4"  # most recently inserted


def test_record_ai_request_persists_a_row(db_session):
    db = db_session()
    ai_request_log_repository.record_ai_request(
        db,
        request_id="xyz",
        langsmith_trace_id="11111111-1111-1111-1111-111111111111",
        phoenix_trace_id=None,
        conversation_id=None,
        intent="TYPE_A_COMPLAINT",
        service_category="GARBAGE",
        routed_to="COMPLAINT_CREATED",
        success=True,
        error_type=None,
        latency_ms=123.4,
    )
    rows = db.query(AiRequestLog).all()
    db.close()
    assert len(rows) == 1
    assert rows[0].request_id == "xyz"
    assert rows[0].langsmith_trace_id == "11111111-1111-1111-1111-111111111111"
    assert rows[0].needs_review is False  # default, unless the caller explicitly passes True


def test_record_ai_request_persists_needs_review_flag(db_session):
    db = db_session()
    ai_request_log_repository.record_ai_request(
        db,
        request_id="xyz",
        langsmith_trace_id=None,
        phoenix_trace_id=None,
        conversation_id=None,
        intent="TYPE_B_SERVICE_INFO",
        service_category=None,
        routed_to="NONE_OUT_OF_SCOPE",
        success=True,
        error_type=None,
        latency_ms=50.0,
        needs_review=True,
    )
    rows = db.query(AiRequestLog).all()
    db.close()
    assert rows[0].needs_review is True


def test_record_ai_request_never_raises_on_db_failure(db_session):
    """A logging failure must never break the caller (see this module's docstring) -- simulate
    one by closing the session before the write is attempted."""
    db = db_session()
    db.close()
    ai_request_log_repository.record_ai_request(
        db,
        request_id="xyz",
        langsmith_trace_id=None,
        phoenix_trace_id=None,
        conversation_id=None,
        intent=None,
        service_category=None,
        routed_to="ERROR",
        success=False,
        error_type="RuntimeError",
        latency_ms=1.0,
    )  # must not raise


# --- endpoints: auth gating ---


def test_ai_monitoring_summary_requires_admin(client, make_citizen):
    token, _ = make_citizen(phone="9000000001")
    response = client.get("/admin/ai-monitoring", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_ai_monitoring_requests_requires_admin(client, make_worker):
    token, _ = make_worker(phone="9000000002")
    response = client.get("/admin/ai-monitoring/requests", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_ai_monitoring_needs_review_requires_admin(client, make_worker):
    token, _ = make_worker(phone="9000000003")
    response = client.get("/admin/ai-monitoring/needs-review", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_ai_monitoring_needs_review_returns_only_flagged_requests(client, make_admin, db_session):
    make_admin(phone="9999999999", password="adminpass")
    token = _login(client, "9999999999", "adminpass")
    _seed_logs(
        db_session,
        [
            {**_BASE_ROW, "request_id": "flagged", "routed_to": "NONE_OUT_OF_SCOPE", "needs_review": True},
            {**_BASE_ROW, "request_id": "not-flagged", "routed_to": "RAG", "needs_review": False},
        ],
    )

    response = client.get("/admin/ai-monitoring/needs-review", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["request_id"] == "flagged"
    assert body[0]["routed_to"] == "NONE_OUT_OF_SCOPE"


def test_ai_monitoring_summary_accessible_to_admin(client, make_admin, db_session):
    make_admin(phone="9999999999", password="adminpass")
    token = _login(client, "9999999999", "adminpass")
    _seed_logs(db_session, [{**_BASE_ROW, "routed_to": "RAG"}])

    response = client.get("/admin/ai-monitoring", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["total_requests"] == 1
    assert body["rag_requests"] == 1


# --- endpoints: trace_url wiring ---


def test_ai_monitoring_requests_trace_url_none_without_template(client, make_admin, db_session, monkeypatch):
    monkeypatch.setattr(settings, "LANGSMITH_TRACE_URL_TEMPLATE", "")
    make_admin(phone="9999999999", password="adminpass")
    token = _login(client, "9999999999", "adminpass")
    _seed_logs(db_session, [{**_BASE_ROW, "langsmith_trace_id": "trace-abc"}])

    response = client.get("/admin/ai-monitoring/requests", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()[0]["trace_url"] is None


def test_ai_monitoring_requests_trace_url_built_from_template(client, make_admin, db_session, monkeypatch):
    monkeypatch.setattr(
        settings, "LANGSMITH_TRACE_URL_TEMPLATE", "https://smith.langchain.com/o/org/projects/p/proj/r/{trace_id}"
    )
    make_admin(phone="9999999999", password="adminpass")
    token = _login(client, "9999999999", "adminpass")
    _seed_logs(db_session, [{**_BASE_ROW, "langsmith_trace_id": "trace-abc"}])

    response = client.get("/admin/ai-monitoring/requests", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()[0]
    assert body["trace_url"] == "https://smith.langchain.com/o/org/projects/p/proj/r/trace-abc"


def test_ai_monitoring_requests_trace_url_none_when_no_trace_recorded(client, make_admin, db_session, monkeypatch):
    """A request that ran while tracing was disabled has no trace id at all -- the link must be
    absent even with a template configured, not point at a nonexistent trace."""
    monkeypatch.setattr(
        settings, "LANGSMITH_TRACE_URL_TEMPLATE", "https://smith.langchain.com/o/org/projects/p/proj/r/{trace_id}"
    )
    make_admin(phone="9999999999", password="adminpass")
    token = _login(client, "9999999999", "adminpass")
    _seed_logs(db_session, [{**_BASE_ROW, "langsmith_trace_id": None}])

    response = client.get("/admin/ai-monitoring/requests", headers={"Authorization": f"Bearer {token}"})
    assert response.json()[0]["trace_url"] is None
