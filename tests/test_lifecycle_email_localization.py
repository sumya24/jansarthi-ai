"""Tests that complaint-lifecycle emails (see backend/services/email_service.py's
send_complaint_status_email) actually render in the citizen's own preferred_language, not just
English -- and that routes/complaints.py's _send_lifecycle_email_best_effort correctly determines
and forwards that language from the citizen's account.

Everything else about these emails (best-effort, never fails the underlying action, etc.) is
already covered by tests/test_complaint_lifecycle_emails.py; this file is specifically about the
localization added on top of that.
"""

from unittest.mock import Mock

import backend.routes.complaints as complaints_module
from backend.config import settings
from backend.models import Complaint
from backend.services.email_service import send_complaint_status_email


def _mock_smtp_configured(monkeypatch) -> None:
    """send_complaint_status_email's own _require_smtp_configured() check needs non-blank
    settings to get past it -- the real send is still short-circuited below by mocking _deliver,
    so nothing actually touches the network."""
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_USERNAME", "test@example.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "x")
    monkeypatch.setattr(settings, "EMAIL_FROM_ADDRESS", "test@example.com")


def _captured_deliver(monkeypatch) -> Mock:
    mock = Mock()
    monkeypatch.setattr("backend.services.email_service._deliver", mock)
    return mock


def _html_of(mock: Mock) -> str:
    """Pulls the HTML alternative part's payload back out of the MIMEMultipart _deliver was
    called with, so a test can assert on the actual rendered content."""
    message = mock.call_args[0][0]
    alternative = message.get_payload(0)
    html_part = alternative.get_payload(1)
    return html_part.get_payload(decode=True).decode("utf-8")


def test_status_email_renders_in_hindi_when_asked(monkeypatch):
    _mock_smtp_configured(monkeypatch)
    mock = _captured_deliver(monkeypatch)

    send_complaint_status_email(
        "citizen@example.com", "resolved", "JM-00042", "गड्ढा भर दिया गया।", "Ward 5", lang="hi",
    )

    html = _html_of(mock)
    assert "आपकी शिकायत का समाधान हो गया है" in html
    assert "समाधान हो गया" in html
    assert "शिकायत आईडी" in html
    # English chrome must NOT leak into a Hindi email.
    assert "Your complaint has been resolved" not in html
    assert "Resolved" not in html


def test_resolved_email_includes_the_workers_completion_note(monkeypatch):
    _mock_smtp_configured(monkeypatch)
    mock = _captured_deliver(monkeypatch)

    send_complaint_status_email(
        "citizen@example.com", "resolved", "JM-00042", "Pothole reported.", "Ward 5",
        worker_note="Patched and compacted, site cleaned up.",
    )

    html = _html_of(mock)
    assert "Completed" in html
    assert "Patched and compacted, site cleaned up." in html


def test_started_email_includes_the_workers_assessment_note(monkeypatch):
    _mock_smtp_configured(monkeypatch)
    mock = _captured_deliver(monkeypatch)

    send_complaint_status_email(
        "citizen@example.com", "started", "JM-00042", "Pothole reported.", "Ward 5",
        worker_note="Inspected the site, will patch it today.",
    )

    html = _html_of(mock)
    assert "Initial assessment" in html
    assert "Inspected the site, will patch it today." in html


def test_created_email_never_shows_a_worker_note_field_even_if_one_is_passed(monkeypatch):
    """created/accepted have no worker note at that point in the lifecycle -- a stray
    worker_note argument must be silently ignored, not shown under the wrong label."""
    _mock_smtp_configured(monkeypatch)
    mock = _captured_deliver(monkeypatch)

    send_complaint_status_email(
        "citizen@example.com", "created", "JM-00042", "Pothole reported.", "Ward 5",
        worker_note="this should never appear",
    )

    html = _html_of(mock)
    assert "this should never appear" not in html


def test_status_email_defaults_to_english(monkeypatch):
    _mock_smtp_configured(monkeypatch)
    mock = _captured_deliver(monkeypatch)

    send_complaint_status_email("citizen@example.com", "created", "JM-00042", "Pothole reported.", "Ward 5")

    html = _html_of(mock)
    assert "We received your complaint" in html
    assert "Submitted" in html


def test_status_email_falls_back_to_english_for_an_unrecognized_language(monkeypatch):
    """Defensive fallback (see _email_strings) -- must never crash on a lang code this module
    doesn't have copy for."""
    _mock_smtp_configured(monkeypatch)
    mock = _captured_deliver(monkeypatch)

    send_complaint_status_email("citizen@example.com", "accepted", "JM-00042", "Pothole reported.", "Ward 5", lang="zz")

    html = _html_of(mock)
    assert "A worker has accepted your complaint" in html


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


def test_lifecycle_email_uses_the_citizens_own_preferred_language(client, monkeypatch, make_citizen, make_worker, db_session):
    """_send_lifecycle_email_best_effort must read the citizen's actual preferred_language --
    not always send English -- so a citizen who set their account to Marathi gets a Marathi email
    for their own complaint, the same way the app's own UI already follows that setting."""
    mock = Mock()
    monkeypatch.setattr(complaints_module, "send_complaint_status_email", mock)
    citizen_token, citizen = make_citizen(phone="9000000001", preferred_language="mr")
    worker_token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_assigned_complaint_for_citizen(db_session, citizen["id"], worker["id"])

    response = client.post(
        f"/complaints/{complaint_id}/accept", headers={"Authorization": f"Bearer {worker_token}"},
    )
    assert response.status_code == 200

    mock.assert_called_once()
    _args, kwargs = mock.call_args
    assert kwargs["lang"] == "mr"


def test_lifecycle_email_defaults_to_english_for_a_citizen_without_a_language_preference(
    client, monkeypatch, make_citizen, make_worker, db_session
):
    mock = Mock()
    monkeypatch.setattr(complaints_module, "send_complaint_status_email", mock)
    citizen_token, citizen = make_citizen(phone="9000000001", preferred_language="en")
    worker_token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_assigned_complaint_for_citizen(db_session, citizen["id"], worker["id"])

    response = client.post(
        f"/complaints/{complaint_id}/accept", headers={"Authorization": f"Bearer {worker_token}"},
    )
    assert response.status_code == 200

    mock.assert_called_once()
    _args, kwargs = mock.call_args
    assert kwargs["lang"] == "en"


def test_lifecycle_email_worker_note_translated_into_citizens_own_language(
    client, monkeypatch, make_citizen, make_worker, db_session
):
    """LIVE-REPORTED: the worker's own assessment/completion note shown in the lifecycle email
    used to always be passed through exactly as the worker wrote it (always English) -- the same
    bug as the on-page Updates timeline and the in-app notification message, just one more call
    site that had never been fixed. Reuses the same per-update translation cache those other views
    already read through (see _send_lifecycle_email_best_effort's own docstring)."""
    mock = Mock()
    monkeypatch.setattr(complaints_module, "send_complaint_status_email", mock)
    fake_translation_service = Mock()
    fake_translation_service.to_language.return_value = "कचरा उचलला नाही."
    fake_translation_service.translate_auto_detecting_source.return_value = "मी साइटची तपासणी केली, आज दुरुस्त करेन."
    monkeypatch.setattr(complaints_module, "_translation_service", fake_translation_service)

    citizen_token, citizen = make_citizen(phone="9000000001", preferred_language="mr")
    worker_token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_assigned_complaint_for_citizen(db_session, citizen["id"], worker["id"])
    client.post(f"/complaints/{complaint_id}/accept", headers={"Authorization": f"Bearer {worker_token}"})

    response = client.post(
        f"/complaints/{complaint_id}/start", headers={"Authorization": f"Bearer {worker_token}"},
        data={"assessment": "Inspected the site, will fix it today."},
    )
    assert response.status_code == 200

    assert mock.call_count == 2  # "accepted" (no worker_note), then "started" (with one)
    _args, kwargs = mock.call_args
    assert kwargs["worker_note"] == "मी साइटची तपासणी केली, आज दुरुस्त करेन."
    assert kwargs["worker_note"] != "Inspected the site, will fix it today."
