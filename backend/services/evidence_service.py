"""Validates and saves uploaded complaint-evidence photos to disk, recording ComplaintEvidence
rows via backend.repositories.evidence_repository.

Relocated, unmodified, from backend/routes/complaints.py (a pure move -- see git history for the
original location) so the Ask Sarthi image-to-complaint flow (orchestration/nodes.py's
complaint_flow_node) can reuse this exact validate/write/DB-row logic without a route module
importing into a service module (the reverse of this project's normal layering) and without a
second implementation of the same file-handling rules. backend/routes/complaints.py now imports
from here instead of defining these itself -- its own call sites are unchanged.

**Content validation (security hardening)**: `Content-Type` and the filename extension are both
attacker-controlled and never authoritative on their own -- a citizen's browser sets `Content-Type`
from the file's extension, so a renamed .exe uploaded as "photo.jpg" arrives here claiming
"image/jpeg" with nothing to contradict it at that layer. The real check is `validate_and_write()`
actually decoding the uploaded bytes with Pillow (already a project dependency -- see
vision_service.py, which uses the same library for the same reason) and confirming they're a
genuinely valid JPEG or PNG: `Image.open()` verifies the file signature/structure, `img.format`
confirms it's one of this app's two genuinely supported formats (not some other real image format
the app doesn't otherwise handle), and `img.load()` forces a full decode -- catching a file whose
header looks right but whose body is truncated/corrupted, which a header-only open() can miss
(verified directly: a JPEG truncated only in its tail scan data opens fine but fails exactly at
`.load()`). This all happens BEFORE anything is written to disk and BEFORE
`ask_sarthi_service.py`'s `_process_image()` ever hands bytes to VisionService/Moondream2 -- an
invalid file is rejected here, it never reaches either.
"""

import io
import logging
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.config import settings
from backend.repositories import evidence_repository

logger = logging.getLogger(__name__)

# The real, decoded-format equivalent of settings.ALLOWED_PHOTO_CONTENT_TYPES -- Pillow reports a
# decoded image's format as "JPEG"/"PNG" regardless of which of the two JPEG content-type spellings
# ("image/jpeg" vs the non-standard but still-accepted "image/jpg") a client sent.
_ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG"}

# Evidence upload phase: at most this many files per single upload call, across every stage
# (citizen complaint, initial assessment, progress update, completion) -- a generous cap on a
# per-request multipart body, not a per-complaint total (a complaint can accumulate more evidence
# across multiple progress updates over time).
MAX_EVIDENCE_FILES_PER_UPLOAD = 10


class SavedFile(BaseModel):
    """Everything a ComplaintEvidence row needs about one file that was just validated and
    written to disk -- not a response model, just an internal return shape for
    validate_and_write() so its callers don't each re-derive content-type/size/original-name
    from the UploadFile after its .file has already been consumed."""

    filename: str
    original_name: str
    content_type: str
    size: int


def validate_and_write(photo: UploadFile) -> SavedFile:
    """Validate one uploaded photo (type, size, and actual decoded content) and write it to
    settings.UPLOAD_FOLDER under a fresh random filename. Client-provided `Content-Type` is only
    ever a cheap, non-authoritative first-pass filter here -- see the module docstring for why
    the real check is decoding the bytes themselves, which happens further down.

    Raises:
        HTTPException: If the photo type, size, or actual decoded content is not allowed.
    """
    if photo.content_type not in settings.ALLOWED_PHOTO_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported photo type: {photo.content_type}. Use JPEG or PNG.",
        )

    # Bounded read (MAX + 1, not the whole file) so an oversized upload -- however large the
    # client actually sends -- is never fully materialized into memory just to then be rejected
    # for its size a moment later. `photo.file` is already a SpooledTemporaryFile by this point
    # (Starlette's own multipart parser wrote it there while receiving the request); this only
    # bounds what THIS function additionally reads from it into a plain `bytes` object.
    contents = photo.file.read(settings.MAX_PHOTO_SIZE_BYTES + 1)
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(contents) > settings.MAX_PHOTO_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="Image exceeds the maximum allowed size (5MB).")

    # The authoritative check -- see module docstring. Runs after the cheap checks above (never
    # worth decoding a file that's already empty/oversized/wrong-Content-Type) but before
    # anything is written to disk.
    try:
        with Image.open(io.BytesIO(contents)) as img:
            if img.format not in _ALLOWED_IMAGE_FORMATS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported image format: {img.format or 'unknown'}. Use JPEG or PNG.",
                )
            img.load()  # Forces a full decode, catching a truncated/corrupted body a lazy
            # header-only open() can miss -- verified directly against a real truncated JPEG.
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Rejected an evidence upload that is not a valid, decodable image: %s", exc)
        raise HTTPException(
            status_code=400,
            detail="The uploaded file is not a valid image. Please upload a real JPEG or PNG photo.",
        ) from exc

    upload_dir = Path(settings.UPLOAD_FOLDER)
    upload_dir.mkdir(parents=True, exist_ok=True)

    extension = Path(photo.filename or "").suffix or ".jpg"
    filename = f"{uuid.uuid4().hex}{extension}"
    (upload_dir / filename).write_bytes(contents)

    return SavedFile(
        filename=filename,
        original_name=photo.filename or filename,
        content_type=photo.content_type or "application/octet-stream",
        size=len(contents),
    )


def save_photo(photo: UploadFile) -> str:
    """Validate and save a single uploaded photo -- unchanged behavior for the pre-existing
    single-photo columns (Complaint.photo_path, ComplaintUpdate.photo_path), kept only for
    backward compatibility with rows written before evidence upload existed. New uploads go
    through save_evidence_files() instead.

    Returns:
        The saved photo's filename (not a full path), so it can be served from the
        /uploads static mount and stored as a small, portable value in the database.
    """
    return validate_and_write(photo).filename


def save_evidence_files(
    db: Session,
    *,
    files: list[UploadFile] | None,
    complaint_id: int,
    update_id: int | None,
    uploaded_by: int,
    uploader_role: str,
    stage: str,
) -> list:
    """Validates and saves zero or more evidence files, recording one ComplaintEvidence row per
    file. Returns the created rows (empty list if `files` is None/empty -- evidence is always
    optional at the HTTP layer; callers that need to *require* at least one file check that
    themselves, matching how photo-optionality is already handled everywhere else in this file).
    """
    if not files:
        return []
    real_files = [f for f in files if f.filename]
    if len(real_files) > MAX_EVIDENCE_FILES_PER_UPLOAD:
        raise HTTPException(status_code=400, detail=f"Too many files -- upload at most {MAX_EVIDENCE_FILES_PER_UPLOAD} at a time.")

    rows = []
    for f in real_files:
        saved = validate_and_write(f)
        rows.append(
            evidence_repository.add_evidence(
                db,
                complaint_id=complaint_id,
                update_id=update_id,
                uploaded_by=uploaded_by,
                uploader_role=uploader_role,
                file_name=saved.original_name,
                file_path=saved.filename,
                file_type=saved.content_type,
                file_size=saved.size,
                stage=stage,
            )
        )
    return rows
