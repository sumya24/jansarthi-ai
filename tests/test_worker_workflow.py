"""Integration tests for the worker complaint-resolution workflow added in this phase:

  ASSIGNED -> ACCEPTED -> (start work, mandatory initial assessment) -> IN_PROGRESS
  -> (optional progress updates) -> (resolve, mandatory completion status, optional evidence)
  -> RESOLVED -> report (view + download)

Also covers: status history, worker notifications (assignment/reassignment), citizen visibility
of worker-authored updates, and authorization (a worker can never act on or read report data for
another worker's complaint).

Complements tests/test_complaints_api.py (accept/reject/basic resolve-gating already covered
there) -- this file is the rest of this phase's spec §33 test list.
"""

from backend.models import Complaint, ComplaintStatusHistory, ComplaintUpdate, Notification, User
from tests.image_fixtures import VALID_JPEG_BYTES
from backend.services.auth_service import hash_password


def _make_worker_row(db_session, phone: str, ward: str, full_name: str = "Worker") -> int:
    db = db_session()
    worker = User(
        full_name=full_name, phone=phone, password_hash=hash_password("secret123!"),
        role="worker", preferred_language="en", ward=ward,
    )
    db.add(worker)
    db.commit()
    db.refresh(worker)
    worker_id = worker.id
    db.close()
    return worker_id


def _make_complaint(db_session, citizen_id: str = "1", worker_id: int | None = None, ward: str = "Ward 14", status: str = "assigned") -> int:
    db = db_session()
    complaint = Complaint(
        citizen_id=citizen_id, original_text="Streetlight broken", original_language="en",
        translated_text="Streetlight broken", summary="Streetlight not working.",
        ward=ward, status=status, assigned_worker_id=worker_id,
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    complaint_id = complaint.id
    db.close()
    return complaint_id


# ---------------------------------------------------------------------------
# Start work / mandatory initial assessment / ACCEPTED -> IN_PROGRESS
# ---------------------------------------------------------------------------


def test_start_work_requires_accepted_status(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="assigned")

    response = client.post(
        f"/complaints/{complaint_id}/start", headers={"Authorization": f"Bearer {token}"},
        data={"assessment": "Looks like a blown fuse."},
    )
    assert response.status_code == 400


def test_start_work_requires_an_assessment(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="accepted")

    # /start is multipart/form-data (not JSON) so it can also accept evidence photos -- an empty
    # Form(...) field is syntactically valid (unlike Pydantic's old min_length=1), so both the
    # empty and whitespace-only cases are caught by the same explicit .strip() check, both 400.
    empty = client.post(f"/complaints/{complaint_id}/start", headers={"Authorization": f"Bearer {token}"}, data={"assessment": ""})
    assert empty.status_code == 400

    whitespace = client.post(f"/complaints/{complaint_id}/start", headers={"Authorization": f"Bearer {token}"}, data={"assessment": "   "})
    assert whitespace.status_code == 400

    db = db_session()
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    assert complaint.status == "accepted"  # unchanged
    db.close()


def test_start_work_moves_to_in_progress_and_stores_assessment(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="accepted")

    response = client.post(
        f"/complaints/{complaint_id}/start", headers={"Authorization": f"Bearer {token}"},
        data={"assessment": "Fuse box damaged, needs replacement part."},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"

    db = db_session()
    update = db.query(ComplaintUpdate).filter(ComplaintUpdate.complaint_id == complaint_id).first()
    assert update is not None
    assert update.update_type == "INITIAL_ASSESSMENT"
    assert update.text == "Fuse box damaged, needs replacement part."
    assert update.worker_id == worker["id"]
    db.close()


def test_start_work_cannot_be_repeated_once_in_progress(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="in_progress")

    response = client.post(
        f"/complaints/{complaint_id}/start", headers={"Authorization": f"Bearer {token}"},
        data={"assessment": "Second attempt."},
    )
    assert response.status_code == 400


def test_citizen_sees_initial_assessment_in_complaint_detail(client, make_citizen, make_worker, db_session):
    citizen_token, citizen = make_citizen(phone="9000000001")
    worker_token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, citizen_id=str(citizen["id"]), worker_id=worker["id"], status="accepted")

    start = client.post(
        f"/complaints/{complaint_id}/start", headers={"Authorization": f"Bearer {worker_token}"},
        data={"assessment": "Initial assessment visible to citizen."},
    )
    assert start.status_code == 200

    detail = client.get(f"/complaints/{complaint_id}", headers={"Authorization": f"Bearer {citizen_token}"})
    assert detail.status_code == 200
    updates = detail.json()["updates"]
    assert any(u["update_type"] == "INITIAL_ASSESSMENT" and u["text"] == "Initial assessment visible to citizen." for u in updates)


# ---------------------------------------------------------------------------
# Optional progress updates
# ---------------------------------------------------------------------------


def test_progress_update_requires_in_progress_status(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="accepted")

    response = client.post(
        f"/complaints/{complaint_id}/updates", headers={"Authorization": f"Bearer {token}"},
        data={"text": "Parts ordered."},
    )
    assert response.status_code == 400


def test_progress_update_requires_non_blank_text(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="in_progress")

    response = client.post(
        f"/complaints/{complaint_id}/updates", headers={"Authorization": f"Bearer {token}"},
        data={"text": "   "},
    )
    assert response.status_code == 400


def test_progress_updates_are_optional_zero_is_fine(client, make_worker, db_session):
    """A complaint can go straight from IN_PROGRESS to resolve() with zero progress updates --
    this endpoint is never required to be called."""
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="in_progress")

    response = client.post(
        f"/complaints/{complaint_id}/resolve", headers={"Authorization": f"Bearer {token}"},
        data={"completion_status": "Fixed without needing a progress update."},
    )
    assert response.status_code == 200


def test_multiple_progress_updates_are_stored_in_order(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="in_progress")

    first = client.post(f"/complaints/{complaint_id}/updates", headers={"Authorization": f"Bearer {token}"}, data={"text": "Parts ordered."})
    second = client.post(f"/complaints/{complaint_id}/updates", headers={"Authorization": f"Bearer {token}"}, data={"text": "Parts arrived, work starting tomorrow."})
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["update_type"] == "PROGRESS_UPDATE"

    db = db_session()
    updates = db.query(ComplaintUpdate).filter(
        ComplaintUpdate.complaint_id == complaint_id, ComplaintUpdate.update_type == "PROGRESS_UPDATE"
    ).order_by(ComplaintUpdate.id).all()
    assert [u.text for u in updates] == ["Parts ordered.", "Parts arrived, work starting tomorrow."]
    db.close()


def test_citizen_sees_progress_update_in_complaint_detail(client, make_citizen, make_worker, db_session):
    citizen_token, citizen = make_citizen(phone="9000000001")
    worker_token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, citizen_id=str(citizen["id"]), worker_id=worker["id"], status="in_progress")

    update = client.post(
        f"/complaints/{complaint_id}/updates", headers={"Authorization": f"Bearer {worker_token}"},
        data={"text": "Progress update visible to citizen."},
    )
    assert update.status_code == 200

    detail = client.get(f"/complaints/{complaint_id}", headers={"Authorization": f"Bearer {citizen_token}"})
    updates = detail.json()["updates"]
    assert any(u["update_type"] == "PROGRESS_UPDATE" and u["text"] == "Progress update visible to citizen." for u in updates)


def test_worker_cannot_add_update_to_another_workers_complaint(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    other_worker_id = _make_worker_row(db_session, phone="9000000099", ward="Ward 9")
    complaint_id = _make_complaint(db_session, worker_id=other_worker_id, ward="Ward 9", status="in_progress")

    response = client.post(
        f"/complaints/{complaint_id}/updates", headers={"Authorization": f"Bearer {token}"},
        data={"text": "Trying to meddle."},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Completion status / evidence / resolve data-integrity ordering
# ---------------------------------------------------------------------------


def test_resolve_stores_completion_status_as_a_complaint_update(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="in_progress")

    response = client.post(
        f"/complaints/{complaint_id}/resolve", headers={"Authorization": f"Bearer {token}"},
        data={"completion_status": "Streetlight repaired and tested working."},
    )
    assert response.status_code == 200

    db = db_session()
    completion = db.query(ComplaintUpdate).filter(
        ComplaintUpdate.complaint_id == complaint_id, ComplaintUpdate.update_type == "COMPLETION"
    ).first()
    assert completion is not None
    assert completion.text == "Streetlight repaired and tested working."
    assert completion.photo_path is None
    db.close()


def test_resolve_with_evidence_photo_is_optional_but_supported(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="in_progress")

    response = client.post(
        f"/complaints/{complaint_id}/resolve", headers={"Authorization": f"Bearer {token}"},
        data={"completion_status": "Repaired, see photo."},
        files=[("photos", ("evidence.jpg", VALID_JPEG_BYTES, "image/jpeg"))],
    )
    assert response.status_code == 200

    db = db_session()
    completion = db.query(ComplaintUpdate).filter(
        ComplaintUpdate.complaint_id == complaint_id, ComplaintUpdate.update_type == "COMPLETION"
    ).first()
    # New uploads go through ComplaintEvidence (evidence upload phase), not the legacy
    # single-photo column -- see backend/models.py's ComplaintEvidence docstring.
    from backend.models import ComplaintEvidence
    evidence = db.query(ComplaintEvidence).filter(ComplaintEvidence.update_id == completion.id).all()
    assert len(evidence) == 1
    assert evidence[0].stage == "COMPLETION"
    assert evidence[0].file_type == "image/jpeg"
    db.close()


def test_cannot_resolve_without_completion_status_leaves_complaint_in_progress(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="in_progress")

    response = client.post(
        f"/complaints/{complaint_id}/resolve", headers={"Authorization": f"Bearer {token}"},
        data={"completion_status": ""},
    )
    assert response.status_code in (400, 422)

    db = db_session()
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    assert complaint.status == "in_progress"
    completion = db.query(ComplaintUpdate).filter(
        ComplaintUpdate.complaint_id == complaint_id, ComplaintUpdate.update_type == "COMPLETION"
    ).first()
    assert completion is None  # no partial/orphaned completion record either
    db.close()


def test_worker_cannot_resolve_another_workers_complaint(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    other_worker_id = _make_worker_row(db_session, phone="9000000099", ward="Ward 9")
    complaint_id = _make_complaint(db_session, worker_id=other_worker_id, ward="Ward 9", status="in_progress")

    response = client.post(
        f"/complaints/{complaint_id}/resolve", headers={"Authorization": f"Bearer {token}"},
        data={"completion_status": "Trying to resolve someone else's complaint."},
    )
    assert response.status_code == 403

    db = db_session()
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    assert complaint.status == "in_progress"
    db.close()


# ---------------------------------------------------------------------------
# Status history
# ---------------------------------------------------------------------------


def test_status_history_records_full_lifecycle(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="assigned")

    assert client.post(f"/complaints/{complaint_id}/accept", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.post(
        f"/complaints/{complaint_id}/start", headers={"Authorization": f"Bearer {token}"}, data={"assessment": "Assessing."}
    ).status_code == 200
    assert client.post(
        f"/complaints/{complaint_id}/resolve", headers={"Authorization": f"Bearer {token}"}, data={"completion_status": "Resolved."}
    ).status_code == 200

    detail = client.get(f"/complaints/{complaint_id}", headers={"Authorization": f"Bearer {token}"})
    history = detail.json()["status_history"]
    transitions = [(h["from_status"], h["to_status"]) for h in history]
    assert ("assigned", "accepted") in transitions
    assert ("accepted", "in_progress") in transitions
    assert ("in_progress", "resolved") in transitions
    # from_status must never equal to_status (would indicate the ordering bug this phase fixed).
    assert all(f != t for f, t in transitions)


def test_status_history_from_status_is_not_mutated_before_recording(client, make_worker, db_session):
    """Regression guard for the specific bug found during development: record_status_change()
    must receive the PRE-mutation status explicitly, not read complaint.status after it was
    already changed to the new value (which would silently record a from==to no-op row)."""
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="assigned")

    client.post(f"/complaints/{complaint_id}/accept", headers={"Authorization": f"Bearer {token}"})

    db = db_session()
    rows = db.query(ComplaintStatusHistory).filter(ComplaintStatusHistory.complaint_id == complaint_id).all()
    assert len(rows) == 1
    assert rows[0].from_status == "assigned"
    assert rows[0].to_status == "accepted"
    assert rows[0].actor_role == "worker"
    assert rows[0].actor_user_id == worker["id"]
    db.close()


def test_status_history_records_assignment_and_reassignment_as_system_actor(client, make_worker, db_session):
    token1, worker1 = make_worker(phone="9000000002", ward="Ward 14")
    _worker2_id = _make_worker_row(db_session, phone="9000000098", ward="Ward 14", full_name="Second Worker")
    complaint_id = _make_complaint(db_session, worker_id=worker1["id"], status="assigned")

    client.post(
        f"/complaints/{complaint_id}/reject", headers={"Authorization": f"Bearer {token1}"},
        json={"reason": "Not available."},
    )

    db = db_session()
    rows = db.query(ComplaintStatusHistory).filter(ComplaintStatusHistory.complaint_id == complaint_id).order_by(ComplaintStatusHistory.id).all()
    assert any(r.actor_role == "system" and r.to_status == "assigned" for r in rows)
    db.close()


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def test_notification_created_on_new_assignment(client, make_citizen, make_worker, monkeypatch, db_session):
    import backend.routes.complaints as complaints_module
    from unittest.mock import Mock
    from backend.models import Complaint as ComplaintModel

    def _fake_agent_create_complaint(db, citizen_id, language_code, text, audio_chunks, photo_path, category=None):
        complaint = ComplaintModel(
            citizen_id=citizen_id, original_text=text or "(voice)", original_language=language_code,
            translated_text=f"[en] {text}", summary="Streetlight is broken near the market.", status="pending",
            service_category=category.value if category else None,
        )
        db.add(complaint)
        db.commit()
        db.refresh(complaint)
        return complaint

    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    worker_token, worker = make_worker(phone="9000000002", ward="Ward 14")
    citizen_token, _citizen = make_citizen(phone="9000000001")

    response = client.post(
        "/complaints", headers={"Authorization": f"Bearer {citizen_token}"},
        data={"language": "en", "text": "Streetlight broken", "ward": "Ward 14"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "assigned"

    notifs = client.get("/notifications", headers={"Authorization": f"Bearer {worker_token}"})
    assert notifs.status_code == 200
    body = notifs.json()
    assert body["unread_count"] == 1
    assert len(body["notifications"]) == 1
    assert body["notifications"][0]["type"] == "NEW_ASSIGNMENT"
    assert body["notifications"][0]["complaint_id"] == response.json()["id"]
    assert body["notifications"][0]["read_at"] is None


def test_notification_created_on_reassignment(client, make_worker, db_session):
    token1, worker1 = make_worker(phone="9000000002", ward="Ward 14")
    worker2_id = _make_worker_row(db_session, phone="9000000098", ward="Ward 14", full_name="Second Worker")
    complaint_id = _make_complaint(db_session, worker_id=worker1["id"], status="assigned")

    client.post(
        f"/complaints/{complaint_id}/reject", headers={"Authorization": f"Bearer {token1}"},
        json={"reason": "Reassign to the next worker."},
    )

    worker2_login = client.post("/auth/login", json={"identifier": "9000000098", "password": "secret123!"})
    token2 = worker2_login.json()["access_token"]

    notifs = client.get("/notifications", headers={"Authorization": f"Bearer {token2}"})
    assert notifs.status_code == 200
    body = notifs.json()
    assert any(n["type"] == "REASSIGNED" and n["complaint_id"] == complaint_id for n in body["notifications"])


def test_notification_mark_read_is_idempotent(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    db = db_session()
    notification = Notification(
        recipient_id=worker["id"], type="NEW_ASSIGNMENT", title="New complaint assigned",
        message="Test message.", complaint_id=None,
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)
    notification_id = notification.id
    db.close()

    unread = client.get("/notifications", headers={"Authorization": f"Bearer {token}"})
    assert unread.json()["unread_count"] == 1

    first_read = client.post(f"/notifications/{notification_id}/read", headers={"Authorization": f"Bearer {token}"})
    assert first_read.status_code == 200
    read_at_1 = first_read.json()["read_at"]
    assert read_at_1 is not None

    second_read = client.post(f"/notifications/{notification_id}/read", headers={"Authorization": f"Bearer {token}"})
    assert second_read.status_code == 200
    assert second_read.json()["read_at"] == read_at_1  # unchanged, not bumped

    after = client.get("/notifications", headers={"Authorization": f"Bearer {token}"})
    assert after.json()["unread_count"] == 0


def test_worker_cannot_mark_another_workers_notification_read(client, make_worker, db_session):
    token1, worker1 = make_worker(phone="9000000002", ward="Ward 14")
    _worker2_id = _make_worker_row(db_session, phone="9000000098", ward="Ward 14", full_name="Second Worker")
    worker2_login = client.post("/auth/login", json={"identifier": "9000000098", "password": "secret123!"})
    token2 = worker2_login.json()["access_token"]

    db = db_session()
    notification = Notification(
        recipient_id=worker1["id"], type="NEW_ASSIGNMENT", title="New complaint assigned",
        message="Belongs to worker1.", complaint_id=None,
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)
    notification_id = notification.id
    db.close()

    response = client.post(f"/notifications/{notification_id}/read", headers={"Authorization": f"Bearer {token2}"})
    assert response.status_code == 404  # not 403 -- doesn't reveal it exists


# ---------------------------------------------------------------------------
# Reports: unavailable before resolution, available after, download, authorization
# ---------------------------------------------------------------------------


def test_report_unavailable_before_resolution(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    for status in ("assigned", "accepted", "in_progress"):
        complaint_id = _make_complaint(db_session, worker_id=worker["id"], status=status)
        view = client.get(f"/complaints/{complaint_id}/report", headers={"Authorization": f"Bearer {token}"})
        assert view.status_code == 404
        download = client.get(f"/complaints/{complaint_id}/report/download", headers={"Authorization": f"Bearer {token}"})
        assert download.status_code == 404


def test_report_available_after_resolution(client, make_worker, db_session):
    # Explicit "en" -- this test is about the report becoming available with the right fields
    # after resolution, not translation. make_worker's own default preferred_language is "hi"
    # (see conftest.py), which used to make no visible difference here only because Sarvam was
    # out of credits at the time and every translation call silently degraded back to the
    # original English text (see TranslationService's own honest-fallback docstring) -- once
    # real translation credits were restored, a Hindi-preferring worker's completion_status
    # correctly started coming back translated, which this test was never actually checking for.
    token, worker = make_worker(phone="9000000002", ward="Ward 14", preferred_language="en")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="in_progress")
    client.post(
        f"/complaints/{complaint_id}/resolve", headers={"Authorization": f"Bearer {token}"},
        data={"completion_status": "All fixed."},
    )

    view = client.get(f"/complaints/{complaint_id}/report", headers={"Authorization": f"Bearer {token}"})
    assert view.status_code == 200
    body = view.json()
    assert body["complaint_id"] == complaint_id
    assert body["display_id"] == f"JM-{complaint_id:05d}"
    assert body["completion_status"] == "All fixed."
    assert body["resolved_at"] is not None


def test_report_download_returns_a_pdf(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="in_progress")
    client.post(
        f"/complaints/{complaint_id}/resolve", headers={"Authorization": f"Bearer {token}"},
        data={"completion_status": "All fixed."},
    )

    download = client.get(f"/complaints/{complaint_id}/report/download", headers={"Authorization": f"Bearer {token}"})
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/pdf"
    assert f"JanSarthi_Complaint_JM-{complaint_id:05d}_Report.pdf" in download.headers["content-disposition"]
    assert download.content[:4] == b"%PDF"


def test_worker_cannot_access_another_workers_report(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    other_worker_id = _make_worker_row(db_session, phone="9000000099", ward="Ward 9")
    complaint_id = _make_complaint(db_session, worker_id=other_worker_id, ward="Ward 9", status="resolved")

    view = client.get(f"/complaints/{complaint_id}/report", headers={"Authorization": f"Bearer {token}"})
    assert view.status_code == 403

    download = client.get(f"/complaints/{complaint_id}/report/download", headers={"Authorization": f"Bearer {token}"})
    assert download.status_code == 403


def test_citizen_can_view_but_not_impersonate_report_authorization(client, make_citizen, make_worker, db_session):
    citizen_token, citizen = make_citizen(phone="9000000001")
    worker_token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, citizen_id=str(citizen["id"]), worker_id=worker["id"], status="in_progress")
    client.post(
        f"/complaints/{complaint_id}/resolve", headers={"Authorization": f"Bearer {worker_token}"},
        data={"completion_status": "All fixed."},
    )

    # The owning citizen can see their own report.
    own = client.get(f"/complaints/{complaint_id}/report", headers={"Authorization": f"Bearer {citizen_token}"})
    assert own.status_code == 200

    # A different citizen cannot.
    other_citizen_token, _other = make_citizen(phone="9000000005")
    other = client.get(f"/complaints/{complaint_id}/report", headers={"Authorization": f"Bearer {other_citizen_token}"})
    assert other.status_code == 403


# ---------------------------------------------------------------------------
# Invalid status transitions (general)
# ---------------------------------------------------------------------------


def test_cannot_add_update_or_resolve_a_pending_complaint(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=None, status="pending")
    # Not assigned to this worker at all -- _get_owned_complaint should 403 before any status check.
    response = client.post(
        f"/complaints/{complaint_id}/start", headers={"Authorization": f"Bearer {token}"},
        data={"assessment": "Trying anyway."},
    )
    assert response.status_code in (403, 404)


def test_cannot_start_work_on_a_resolved_complaint(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="resolved")

    response = client.post(
        f"/complaints/{complaint_id}/start", headers={"Authorization": f"Bearer {token}"},
        data={"assessment": "Too late."},
    )
    assert response.status_code == 400


def test_cannot_resolve_an_already_resolved_complaint(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="resolved")

    response = client.post(
        f"/complaints/{complaint_id}/resolve", headers={"Authorization": f"Bearer {token}"},
        data={"completion_status": "Again?"},
    )
    assert response.status_code == 400
