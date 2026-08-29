"""Tests for upload_cleanup_service.cleanup_orphaned_uploads -- the periodic sweep that removes
Ask Sarthi chat photos that were saved to disk (for captioning) but never ended up attached to a
real complaint. See that module's own docstring for why this exists: unlike the dedicated
"Report an Issue" form (which only uploads at final submit), the chat flow writes every attached
photo immediately, so an abandoned conversation would otherwise leave orphaned files forever.
"""

import os
import time

from backend.models import Complaint, ComplaintEvidence, ComplaintUpdate
from backend.services.upload_cleanup_service import cleanup_orphaned_uploads


def _touch(path, *, age_hours: float = 0):
    """Creates a file at `path` whose mtime is `age_hours` in the past."""
    path.write_bytes(b"fake-jpeg-bytes")
    if age_hours:
        old = time.time() - age_hours * 3600
        os.utime(path, (old, old))


def test_deletes_unreferenced_file_older_than_retention(tmp_path, db_session):
    _touch(tmp_path / "orphan.jpg", age_hours=72)
    db = db_session()

    deleted = cleanup_orphaned_uploads(db, upload_folder=str(tmp_path), retention_hours=48)

    assert deleted == 1
    assert not (tmp_path / "orphan.jpg").exists()
    db.close()


def test_never_deletes_a_file_still_within_the_retention_window(tmp_path, db_session):
    """LIVE-REPORTED CONCERN this guards against: a citizen mid-conversation who attached a photo
    minutes ago, and hasn't decided yet whether to file a complaint, must never have that photo
    swept away out from under them."""
    _touch(tmp_path / "just_attached.jpg", age_hours=0)
    db = db_session()

    deleted = cleanup_orphaned_uploads(db, upload_folder=str(tmp_path), retention_hours=48)

    assert deleted == 0
    assert (tmp_path / "just_attached.jpg").exists()
    db.close()


def test_never_deletes_a_file_referenced_by_complaint_photo_path(tmp_path, db_session):
    _touch(tmp_path / "kept.jpg", age_hours=200)
    db = db_session()
    db.add(
        Complaint(
            citizen_id="1", original_text="pothole", original_language="en",
            translated_text="pothole", summary="pothole", photo_path="kept.jpg",
        )
    )
    db.commit()

    deleted = cleanup_orphaned_uploads(db, upload_folder=str(tmp_path), retention_hours=48)

    assert deleted == 0
    assert (tmp_path / "kept.jpg").exists()
    db.close()


def test_never_deletes_a_file_referenced_by_complaint_evidence(tmp_path, db_session):
    _touch(tmp_path / "evidence.jpg", age_hours=200)
    db = db_session()
    complaint = Complaint(
        citizen_id="1", original_text="pothole", original_language="en",
        translated_text="pothole", summary="pothole",
    )
    db.add(complaint)
    db.flush()
    db.add(
        ComplaintEvidence(
            complaint_id=complaint.id, uploaded_by=1, uploader_role="citizen",
            file_name="evidence.jpg", file_path="evidence.jpg", file_type="image/jpeg",
            file_size=123, stage="CITIZEN_COMPLAINT",
        )
    )
    db.commit()

    deleted = cleanup_orphaned_uploads(db, upload_folder=str(tmp_path), retention_hours=48)

    assert deleted == 0
    assert (tmp_path / "evidence.jpg").exists()
    db.close()


def test_never_deletes_a_file_referenced_by_complaint_update_photo_path(tmp_path, db_session):
    _touch(tmp_path / "update.jpg", age_hours=200)
    db = db_session()
    complaint = Complaint(
        citizen_id="1", original_text="pothole", original_language="en",
        translated_text="pothole", summary="pothole",
    )
    db.add(complaint)
    db.flush()
    db.add(
        ComplaintUpdate(
            complaint_id=complaint.id, worker_id=1, update_type="COMPLETION",
            text="Fixed.", photo_path="update.jpg",
        )
    )
    db.commit()

    deleted = cleanup_orphaned_uploads(db, upload_folder=str(tmp_path), retention_hours=48)

    assert deleted == 0
    assert (tmp_path / "update.jpg").exists()
    db.close()


def test_never_deletes_a_dotfile_regardless_of_age(tmp_path, db_session):
    """LIVE-REPORTED BUG: `.gitkeep` (the placeholder that keeps this otherwise-empty folder
    tracked in git) is never referenced by any complaint and is always old -- the first real
    sweep deleted it. An uploaded photo is always a generated uuid4().hex filename, never one
    starting with `.`, so dotfiles are categorically never a citizen's file to clean up."""
    _touch(tmp_path / ".gitkeep", age_hours=10_000)
    db = db_session()

    deleted = cleanup_orphaned_uploads(db, upload_folder=str(tmp_path), retention_hours=48)

    assert deleted == 0
    assert (tmp_path / ".gitkeep").exists()
    db.close()


def test_returns_zero_and_does_not_raise_when_upload_folder_missing(tmp_path, db_session):
    db = db_session()
    missing = tmp_path / "does-not-exist"

    deleted = cleanup_orphaned_uploads(db, upload_folder=str(missing), retention_hours=48)

    assert deleted == 0
    db.close()


def test_mixed_folder_only_removes_the_orphaned_stale_file(tmp_path, db_session):
    """One sweep, three files, three different reasons to keep two of them and remove the third --
    the realistic case (not every file in the folder is orphaned)."""
    _touch(tmp_path / "orphan_old.jpg", age_hours=72)
    _touch(tmp_path / "orphan_recent.jpg", age_hours=1)
    _touch(tmp_path / "referenced_old.jpg", age_hours=72)
    db = db_session()
    db.add(
        Complaint(
            citizen_id="1", original_text="pothole", original_language="en",
            translated_text="pothole", summary="pothole", photo_path="referenced_old.jpg",
        )
    )
    db.commit()

    deleted = cleanup_orphaned_uploads(db, upload_folder=str(tmp_path), retention_hours=48)

    assert deleted == 1
    assert not (tmp_path / "orphan_old.jpg").exists()
    assert (tmp_path / "orphan_recent.jpg").exists()
    assert (tmp_path / "referenced_old.jpg").exists()
    db.close()
