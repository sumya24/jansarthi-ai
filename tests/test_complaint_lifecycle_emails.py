"""Tests for citizen-facing complaint-lifecycle emails (created/accepted/started/resolved) --
see backend/services/email_service.py's send_complaint_status_email and routes/complaints.py's
_send_lifecycle_email_best_effort. Every citizen created via the make_citizen fixture already has
a verified email (mandatory at signup, see tests/test_signup_email_verification.py), so these
tests don't need any extra email-verification setup of their own.

send_complaint_status_email is mocked at its import site in backend.routes.complaints (not
backend.services.email_service) -- that module does `from ... import send_complaint_status_email`,
so the name it actually calls lives in its own namespace, same as how complaints_module._agent is
patched elsewhere in this test suite.
"""

from unittest.mock import Mock

import backend.routes.complaints as complaints_module
from backend.models import Complaint
from backend.services.email_service import EmailServiceError


def _fake_agent_create_complaint(db, citizen_id, language_code, text, audio_chunks, photo_path, category=None):
    complaint = Complaint(
        citizen_id=citizen_id, original_text=text or "(voice complaint)", original_language=language_code,
        translated_text=f"[en] {text or 'voice complaint'}", summary="A short summary.", photo_path=photo_path,
        status="pending", service_category=category.value if category else None,
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    return complaint


def _mock_status_email(monkeypatch) -> Mock:
    mock = Mock()
    monkeypatch.setattr(complaints_module, "send_complaint_status_email", mock)
    return mock


def _make_assigned_complaint_for_citizen(db_session, citizen_id: int, worker_id: int, ward: str = "Ward 14") -> int:
    db = db_session()
    complaint = Complaint(
        citizen_id=str(citizen_id), original_text="a", original_language="en",
        translated_text="Garbage issue", summary="Garbage not collected.",
        ward=ward, status="assigned", assigned_worker_id=worker_id,
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    complaint_id = complaint.id
    db.close()
    return complaint_id


def test_create_complaint_sends_a_created_email(client, monkeypatch, make_citizen):
    mock = _mock_status_email(monkeypatch)
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    token, user = make_citizen(phone="9000000001")

    response = client.post(
        "/complaints", headers={"Authorization": f"Bearer {token}"},
        data={"language": "en", "text": "Garbage issue"},
    )
    assert response.status_code == 200

    mock.assert_called_once()
    args, kwargs = mock.call_args
    assert args[0] == user["email"]
    assert args[1] == "created"
    assert args[2] == f"JM-{response.json()['id']:05d}"


def test_accept_complaint_sends_an_accepted_email(client, monkeypatch, make_citizen, make_worker, db_session):
    mock = _mock_status_email(monkeypatch)
    citizen_token, citizen = make_citizen(phone="9000000001")
    worker_token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_assigned_complaint_for_citizen(db_session, citizen["id"], worker["id"])

    response = client.post(
        f"/complaints/{complaint_id}/accept", headers={"Authorization": f"Bearer {worker_token}"},
    )
    assert response.status_code == 200

    mock.assert_called_once()
    args, _kwargs = mock.call_args
    assert args[0] == citizen["email"]
    assert args[1] == "accepted"


def test_start_work_sends_a_started_email(client, monkeypatch, make_citizen, make_worker, db_session):
    mock = _mock_status_email(monkeypatch)
    citizen_token, citizen = make_citizen(phone="9000000001")
    worker_token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_assigned_complaint_for_citizen(db_session, citizen["id"], worker["id"], ward="Ward 14")

    db = db_session()
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    complaint.status = "accepted"
    db.commit()
    db.close()

    response = client.post(
        f"/complaints/{complaint_id}/start", headers={"Authorization": f"Bearer {worker_token}"},
        data={"assessment": "Taking a look today."},
    )
    assert response.status_code == 200

    mock.assert_called_once()
    args, kwargs = mock.call_args
    assert args[0] == citizen["email"]
    assert args[1] == "started"
    assert kwargs["worker_note"] == "Taking a look today."


def test_resolve_complaint_sends_a_resolved_email(client, monkeypatch, make_citizen, make_worker, db_session):
    mock = _mock_status_email(monkeypatch)
    citizen_token, citizen = make_citizen(phone="9000000001")
    worker_token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_assigned_complaint_for_citizen(db_session, citizen["id"], worker["id"], ward="Ward 14")

    db = db_session()
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    complaint.status = "in_progress"
    db.commit()
    db.close()

    response = client.post(
        f"/complaints/{complaint_id}/resolve", headers={"Authorization": f"Bearer {worker_token}"},
        data={"completion_status": "Fixed."},
    )
    assert response.status_code == 200

    mock.assert_called_once()
    args, kwargs = mock.call_args
    assert args[0] == citizen["email"]
    assert args[1] == "resolved"
    assert kwargs["worker_note"] == "Fixed."


def test_lifecycle_email_failure_does_not_fail_the_action(client, monkeypatch, make_citizen, make_worker, db_session):
    """The core guarantee this whole feature depends on: if the SMTP send raises
    EmailServiceError, the complaint's own status change must still succeed -- see
    _send_lifecycle_email_best_effort's own docstring."""
    monkeypatch.setattr(
        complaints_module, "send_complaint_status_email",
        Mock(side_effect=EmailServiceError("SMTP is down.")),
    )
    citizen_token, citizen = make_citizen(phone="9000000001")
    worker_token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_assigned_complaint_for_citizen(db_session, citizen["id"], worker["id"])

    response = client.post(
        f"/complaints/{complaint_id}/accept", headers={"Authorization": f"Bearer {worker_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_no_email_sent_for_a_citizen_without_a_verified_email(client, monkeypatch, make_worker, db_session):
    """A complaint whose citizen_id doesn't resolve to any real, verified-email account (e.g. old
    test/demo data) must silently skip the email, never raise."""
    mock = _mock_status_email(monkeypatch)
    worker_token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_assigned_complaint_for_citizen(db_session, citizen_id=999999, worker_id=worker["id"])

    response = client.post(
        f"/complaints/{complaint_id}/accept", headers={"Authorization": f"Bearer {worker_token}"},
    )
    assert response.status_code == 200
    mock.assert_not_called()
