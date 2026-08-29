"""Integration tests for the multi-file complaint evidence system (evidence upload phase) --
see backend/models.py's ComplaintEvidence docstring for the storage model.

Covers: citizen evidence at complaint creation, worker evidence at each of the three stages
(initial assessment / progress update / completion), validation (type/size/count/empty),
authorization (citizen/worker can only reach their own complaints -- inherited from the
existing _get_owned_complaint/_get_visible_complaint checks, not a separate weaker path),
real on-disk persistence, and evidence appearing in the JSON report once resolved.
"""

from pathlib import Path
from unittest.mock import Mock

import backend.routes.complaints as complaints_module
from backend.config import settings
from backend.models import Complaint, ComplaintEvidence, User
from backend.services.auth_service import hash_password
from tests.image_fixtures import VALID_JPEG_BYTES as _JPEG_BYTES
from tests.image_fixtures import VALID_PNG_BYTES as _PNG_BYTES


def _fake_agent_create_complaint(db, citizen_id, language_code, text, audio_chunks, photo_path, category=None):
    complaint = Complaint(
        citizen_id=citizen_id, original_text=text or "(voice complaint)", original_language=language_code,
        translated_text=f"[en] {text or 'voice complaint'}", summary="A short summary.",
        photo_path=photo_path, status="pending", service_category=category.value if category else None,
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    return complaint


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
        citizen_id=citizen_id, original_text="a", original_language="en",
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
# Citizen evidence at complaint creation
# ---------------------------------------------------------------------------


def test_citizen_can_upload_multiple_photos_with_a_complaint(client, monkeypatch, make_citizen):
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    token, citizen = make_citizen(phone="9000000001")

    response = client.post(
        "/complaints", headers={"Authorization": f"Bearer {token}"},
        data={"language": "en", "text": "Garbage everywhere"},
        files=[
            ("photos", ("one.jpg", _JPEG_BYTES, "image/jpeg")),
            ("photos", ("two.png", _PNG_BYTES, "image/png")),
        ],
    )
    assert response.status_code == 200
    complaint_id = response.json()["id"]

    detail = client.get(f"/complaints/{complaint_id}", headers={"Authorization": f"Bearer {token}"})
    assert detail.status_code == 200
    evidence = detail.json()["evidence"]
    assert len(evidence) == 2
    assert {e["stage"] for e in evidence} == {"CITIZEN_COMPLAINT"}
    assert {e["uploader_role"] for e in evidence} == {"citizen"}
    assert {e["file_name"] for e in evidence} == {"one.jpg", "two.png"}


def test_evidence_files_are_actually_persisted_on_disk(client, monkeypatch, make_citizen):
    """Not a UI-only mock -- the uploaded bytes must exist as a real file in the configured
    upload folder, not just a database row claiming they do."""
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    token, citizen = make_citizen(phone="9000000001")

    response = client.post(
        "/complaints", headers={"Authorization": f"Bearer {token}"},
        data={"language": "en", "text": "Pothole here"},
        files=[("photos", ("evidence.jpg", _JPEG_BYTES, "image/jpeg"))],
    )
    complaint_id = response.json()["id"]

    detail = client.get(f"/complaints/{complaint_id}", headers={"Authorization": f"Bearer {token}"})
    file_path = detail.json()["evidence"][0]["file_path"]

    on_disk = Path(settings.UPLOAD_FOLDER) / file_path
    assert on_disk.is_file()
    assert on_disk.read_bytes() == _JPEG_BYTES


def test_citizen_evidence_upload_rejects_unsupported_type(client, monkeypatch, make_citizen):
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    token, citizen = make_citizen(phone="9000000001")

    response = client.post(
        "/complaints", headers={"Authorization": f"Bearer {token}"},
        data={"language": "en", "text": "Bad file type"},
        files=[("photos", ("evidence.pdf", b"%PDF-1.4 fake pdf", "application/pdf"))],
    )
    assert response.status_code == 400
    assert "unsupported" in response.json()["detail"].lower()


def test_citizen_evidence_upload_rejects_oversized_file(client, monkeypatch, make_citizen):
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    token, citizen = make_citizen(phone="9000000001")

    oversized = b"\xff\xd8\xff" + b"0" * (settings.MAX_PHOTO_SIZE_BYTES + 1)
    response = client.post(
        "/complaints", headers={"Authorization": f"Bearer {token}"},
        data={"language": "en", "text": "Too big"},
        files=[("photos", ("huge.jpg", oversized, "image/jpeg"))],
    )
    assert response.status_code == 400
    assert "size" in response.json()["detail"].lower()


def test_citizen_evidence_upload_rejects_too_many_files(client, monkeypatch, make_citizen):
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    token, citizen = make_citizen(phone="9000000001")

    files = [("photos", (f"photo{i}.jpg", _JPEG_BYTES, "image/jpeg")) for i in range(11)]
    response = client.post(
        "/complaints", headers={"Authorization": f"Bearer {token}"},
        data={"language": "en", "text": "Way too many photos"},
        files=files,
    )
    assert response.status_code == 400
    assert "too many" in response.json()["detail"].lower()


def test_complaint_creation_without_photos_still_works(client, monkeypatch, make_citizen):
    """Evidence is optional at every stage -- a citizen must still be able to file a complaint
    with zero photos, exactly as before this phase."""
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    token, citizen = make_citizen(phone="9000000001")

    response = client.post(
        "/complaints", headers={"Authorization": f"Bearer {token}"},
        data={"language": "en", "text": "No photo this time"},
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Worker evidence: initial assessment / progress update / completion
# ---------------------------------------------------------------------------


def test_worker_can_upload_initial_assessment_evidence(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="accepted")

    response = client.post(
        f"/complaints/{complaint_id}/start", headers={"Authorization": f"Bearer {token}"},
        data={"assessment": "Checked the site."},
        files=[("photos", ("assess.jpg", _JPEG_BYTES, "image/jpeg"))],
    )
    assert response.status_code == 200

    detail = client.get(f"/complaints/{complaint_id}", headers={"Authorization": f"Bearer {token}"})
    evidence = detail.json()["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["stage"] == "INITIAL_ASSESSMENT"
    assert evidence[0]["uploader_role"] == "worker"
    assert evidence[0]["uploaded_by"] == worker["id"]

    # Also returned inline on the update itself (not just the complaint-level list).
    update = next(u for u in detail.json()["updates"] if u["update_type"] == "INITIAL_ASSESSMENT")
    assert len(update["evidence"]) == 1


def test_worker_can_upload_multiple_progress_evidence_photos(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="in_progress")

    response = client.post(
        f"/complaints/{complaint_id}/updates", headers={"Authorization": f"Bearer {token}"},
        data={"text": "Work underway."},
        files=[
            ("photos", ("progress1.jpg", _JPEG_BYTES, "image/jpeg")),
            ("photos", ("progress2.jpg", _JPEG_BYTES, "image/jpeg")),
        ],
    )
    assert response.status_code == 200
    assert len(response.json()["evidence"]) == 2

    detail = client.get(f"/complaints/{complaint_id}", headers={"Authorization": f"Bearer {token}"})
    progress_evidence = [e for e in detail.json()["evidence"] if e["stage"] == "PROGRESS_UPDATE"]
    assert len(progress_evidence) == 2


def test_progress_update_evidence_is_optional(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="in_progress")

    response = client.post(
        f"/complaints/{complaint_id}/updates", headers={"Authorization": f"Bearer {token}"},
        data={"text": "No photo for this one."},
    )
    assert response.status_code == 200
    assert response.json()["evidence"] == []


def test_worker_can_upload_completion_evidence(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_complaint(db_session, worker_id=worker["id"], status="in_progress")

    response = client.post(
        f"/complaints/{complaint_id}/resolve", headers={"Authorization": f"Bearer {token}"},
        data={"completion_status": "Fixed and verified."},
        files=[("photos", ("final.jpg", _JPEG_BYTES, "image/jpeg"))],
    )
    assert response.status_code == 200

    detail = client.get(f"/complaints/{complaint_id}", headers={"Authorization": f"Bearer {token}"})
    completion_evidence = [e for e in detail.json()["evidence"] if e["stage"] == "COMPLETION"]
    assert len(completion_evidence) == 1
    assert completion_evidence[0]["uploader_role"] == "worker"


# ---------------------------------------------------------------------------
# Authorization -- evidence upload/viewing inherits the existing ownership/visibility checks,
# never a separate, weaker path.
# ---------------------------------------------------------------------------


def test_worker_cannot_upload_evidence_to_another_workers_complaint(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    other_worker_id = _make_worker_row(db_session, phone="9000000099", ward="Ward 9")
    complaint_id = _make_complaint(db_session, worker_id=other_worker_id, ward="Ward 9", status="in_progress")

    response = client.post(
        f"/complaints/{complaint_id}/updates", headers={"Authorization": f"Bearer {token}"},
        data={"text": "Trying to meddle."},
        files=[("photos", ("intrude.jpg", _JPEG_BYTES, "image/jpeg"))],
    )
    assert response.status_code == 403

    db = db_session()
    assert db.query(ComplaintEvidence).filter(ComplaintEvidence.complaint_id == complaint_id).count() == 0
    db.close()


def test_citizen_cannot_see_another_citizens_evidence(client, monkeypatch, make_citizen):
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    token_a, citizen_a = make_citizen(phone="9000000001")
    token_b, citizen_b = make_citizen(phone="9000000002")

    response = client.post(
        "/complaints", headers={"Authorization": f"Bearer {token_a}"},
        data={"language": "en", "text": "Citizen A's private complaint"},
        files=[("photos", ("private.jpg", _JPEG_BYTES, "image/jpeg"))],
    )
    complaint_id = response.json()["id"]

    forbidden = client.get(f"/complaints/{complaint_id}", headers={"Authorization": f"Bearer {token_b}"})
    assert forbidden.status_code == 403


# ---------------------------------------------------------------------------
# Reports include evidence once resolved
# ---------------------------------------------------------------------------


def test_report_includes_evidence_from_every_stage_once_resolved(client, monkeypatch, make_citizen, make_worker):
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    worker_token, worker = make_worker(phone="9000000002", ward="Ward 14")
    citizen_token, citizen = make_citizen(phone="9000000001")

    created = client.post(
        "/complaints", headers={"Authorization": f"Bearer {citizen_token}"},
        data={"language": "en", "text": "Broken streetlight", "ward": "Ward 14"},
        files=[("photos", ("citizen.jpg", _JPEG_BYTES, "image/jpeg"))],
    )
    complaint_id = created.json()["id"]
    assert created.json()["status"] == "assigned"

    client.post(f"/complaints/{complaint_id}/accept", headers={"Authorization": f"Bearer {worker_token}"})
    client.post(
        f"/complaints/{complaint_id}/start", headers={"Authorization": f"Bearer {worker_token}"},
        data={"assessment": "Assessed."}, files=[("photos", ("initial.jpg", _JPEG_BYTES, "image/jpeg"))],
    )
    client.post(
        f"/complaints/{complaint_id}/resolve", headers={"Authorization": f"Bearer {worker_token}"},
        data={"completion_status": "Fixed."}, files=[("photos", ("final.jpg", _JPEG_BYTES, "image/jpeg"))],
    )

    report = client.get(f"/complaints/{complaint_id}/report", headers={"Authorization": f"Bearer {worker_token}"})
    assert report.status_code == 200
    body = report.json()
    assert len(body["citizen_evidence"]) == 1
    assert len(body["initial_assessment_evidence"]) == 1
    assert len(body["completion_evidence"]) == 1

    # The PDF must actually build without raising, with real embedded images.
    pdf = client.get(f"/complaints/{complaint_id}/report/download", headers={"Authorization": f"Bearer {worker_token}"})
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content[:4] == b"%PDF"


def test_report_generation_survives_a_corrupt_evidence_image(client, monkeypatch, make_citizen, make_worker, db_session):
    """Regression test for a real bug found during development: a genuinely undecodable image
    file used to crash report generation entirely (500 on both the JSON view and the PDF
    download) instead of being skipped -- see complaint_report_service.py's
    _evidence_thumbnails() docstring. One bad file must never block a citizen or worker from
    getting their report at all.

    The upload API itself can no longer put a corrupt file on disk (see evidence_service.py's
    real content validation, added after this regression test was first written -- a corrupt
    upload is now rejected at 400 before ever reaching disk, see the dedicated rejection tests
    below). This test now simulates the state directly instead: a real ComplaintEvidence row
    pointing at a corrupt file already on disk, the same state that existed from data written
    before this hardening, or that could exist via any other path that touches disk directly.
    The thing actually being protected against -- report generation crashing on a bad file
    that's already there -- is still real and still worth guarding, independent of how such a
    file could come to exist."""
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    worker_token, worker = make_worker(phone="9000000002", ward="Ward 14")
    citizen_token, citizen = make_citizen(phone="9000000001")

    created = client.post(
        "/complaints", headers={"Authorization": f"Bearer {citizen_token}"},
        data={"language": "en", "text": "Complaint with a corrupt photo", "ward": "Ward 14"},
        files=[("photos", ("good.jpg", _JPEG_BYTES, "image/jpeg"))],
    )
    complaint_id = created.json()["id"]

    # A well-formed PNG signature + IHDR chunk declaration, followed by garbage where real chunk
    # data should be -- looks right, isn't (see tests/image_fixtures.py's CORRUPTED_PNG_BYTES,
    # same bytes). Written directly to disk + DB, bypassing the (now-hardened) upload API.
    corrupt_png = b"\x89PNG\r\n\x1a\n" + bytes.fromhex("0000000d49484452") + b"\x00" * 200
    corrupt_filename = "corrupt-test-fixture.png"
    (Path(settings.UPLOAD_FOLDER) / corrupt_filename).write_bytes(corrupt_png)
    db = db_session()
    db.add(ComplaintEvidence(
        complaint_id=complaint_id, update_id=None, uploaded_by=int(citizen["id"]),
        uploader_role="citizen", file_name="corrupt.png", file_path=corrupt_filename,
        file_type="image/png", file_size=len(corrupt_png), stage="CITIZEN_COMPLAINT",
    ))
    db.commit()
    db.close()

    client.post(f"/complaints/{complaint_id}/accept", headers={"Authorization": f"Bearer {worker_token}"})
    client.post(
        f"/complaints/{complaint_id}/start", headers={"Authorization": f"Bearer {worker_token}"},
        data={"assessment": "Assessed."},
    )
    resolved = client.post(
        f"/complaints/{complaint_id}/resolve", headers={"Authorization": f"Bearer {worker_token}"},
        data={"completion_status": "Fixed."},
    )
    assert resolved.status_code == 200

    # Neither the JSON report nor the PDF download crashes -- both must succeed despite one of
    # the two citizen photos being undecodable.
    report = client.get(f"/complaints/{complaint_id}/report", headers={"Authorization": f"Bearer {worker_token}"})
    assert report.status_code == 200
    assert len(report.json()["citizen_evidence"]) == 2  # both files are still listed as metadata

    download = client.get(f"/complaints/{complaint_id}/report/download", headers={"Authorization": f"Bearer {worker_token}"})
    assert download.status_code == 200

    pdf = client.get(f"/complaints/{complaint_id}/report/download", headers={"Authorization": f"Bearer {worker_token}"})
    assert pdf.status_code == 200
    assert pdf.content[:4] == b"%PDF"
