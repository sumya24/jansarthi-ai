"""Deletes uploaded photo files that were never attached to any real complaint.

LIVE-REPORTED CONCERN: every photo attached in Ask Sarthi chat is written to
`settings.UPLOAD_FOLDER` immediately, the moment it's uploaded -- needed so the vision model can
caption it right away (see ask_janmitra_service.py's `_process_image()`), well before the citizen
ever decides whether to file a complaint from it. A citizen who attaches a photo and then abandons
the conversation (asks something else, closes the tab, never confirms) leaves that file on disk
with nothing ever pointing back to it -- unlike the dedicated "Report an Issue" form, which only
uploads at final submit and so never creates this kind of orphan.

Rather than redesigning the chat's upload timing (a bigger change: a temp/staging area, move-on-
confirm logic, and cleanup of THAT area too), this closes the actual problem -- unbounded disk
growth -- with a periodic sweep: any file in the upload folder that no `Complaint`/`ComplaintUpdate`/
`ComplaintEvidence` row references, AND that's older than a retention window (so a photo still
mid-conversation is never touched), gets deleted. Wired to run on its own schedule in
backend/main.py's lifespan, not on the request path.
"""

import logging
import time
from pathlib import Path

from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import Complaint, ComplaintEvidence, ComplaintUpdate

logger = logging.getLogger(__name__)


def _referenced_filenames(db: Session) -> set[str]:
    """Every filename currently pointed to by a real complaint record, across all three photo-
    reference columns this app has ever used (see ComplaintEvidence's own docstring for why there
    are three: `Complaint.photo_path`/`ComplaintUpdate.photo_path` are the older single-file
    columns, kept for backward compatibility; `ComplaintEvidence.file_path` is the current
    multi-file table). A file matching ANY of these must never be deleted, regardless of age."""
    referenced: set[str] = set()
    referenced.update(
        row[0]
        for row in db.query(Complaint.photo_path).filter(Complaint.photo_path.isnot(None)).all()
    )
    referenced.update(
        row[0]
        for row in db.query(ComplaintUpdate.photo_path).filter(ComplaintUpdate.photo_path.isnot(None)).all()
    )
    referenced.update(row[0] for row in db.query(ComplaintEvidence.file_path).all())
    return referenced


def cleanup_orphaned_uploads(
    db: Session,
    *,
    upload_folder: str | None = None,
    retention_hours: int | None = None,
) -> int:
    """Deletes files in `upload_folder` (default settings.UPLOAD_FOLDER) that are both unreferenced
    by any complaint record and older than `retention_hours` (default
    settings.ORPHANED_UPLOAD_RETENTION_HOURS) -- see this module's own docstring for why both
    conditions matter: age alone would risk deleting a photo a citizen is still actively
    conversing about before confirming; reference-check alone would never clean up anything, since
    an unconfirmed photo is never referenced by definition.

    Best-effort per file: a single file that can't be removed (permissions, already gone) is
    logged and skipped, never raises -- one bad file must not abort the whole sweep.

    Returns the number of files actually deleted.
    """
    folder = Path(upload_folder if upload_folder is not None else settings.UPLOAD_FOLDER)
    if not folder.is_dir():
        return 0
    effective_retention_hours = (
        retention_hours if retention_hours is not None else settings.ORPHANED_UPLOAD_RETENTION_HOURS
    )
    cutoff = time.time() - effective_retention_hours * 3600
    referenced = _referenced_filenames(db)

    deleted = 0
    for entry in folder.iterdir():
        # Dotfiles (e.g. `.gitkeep`, the placeholder that keeps this otherwise-empty folder
        # tracked in git) are never a citizen's uploaded photo -- an uploaded file is always a
        # generated uuid4().hex name (see evidence_service.validate_and_write()), never one
        # starting with `.`. Skipping them outright means this job can never delete one, no
        # matter how old it is.
        if not entry.is_file() or entry.name.startswith(".") or entry.name in referenced:
            continue
        try:
            if entry.stat().st_mtime > cutoff:
                continue
            entry.unlink()
            deleted += 1
        except OSError as exc:
            logger.warning("Orphaned-upload cleanup: could not remove %s: %s", entry.name, exc)

    if deleted:
        logger.info(
            "Orphaned-upload cleanup: removed %d unreferenced file(s) older than %dh",
            deleted, effective_retention_hours,
        )
    return deleted
