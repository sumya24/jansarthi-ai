"""API endpoints for creating, listing, and tracking complaints through their lifecycle.

Lifecycle: pending -> assigned -> accepted -> resolved, with citizen feedback afterward.
A complaint starts "pending" (or "assigned" immediately, if its ward already has an eligible
worker). The assigned worker can accept (moves to "accepted", unlocking their phone number for
the citizen) or reject (assignment_service.py hands it to the next worker in that ward, or back
to "pending" if none are left). Only an accepted complaint's assigned worker can resolve it.

Access is scoped by the authenticated user's role (see backend/deps.py): citizens only ever see
their own complaints, workers only see complaints currently assigned to them, and super admins
see everything.
"""

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.deps import get_current_user, require_ai_rate_limit, require_role
from backend.models import ULB, Complaint, ComplaintRejection, ComplaintStatusHistory, ComplaintUpdate, District, State, User
from backend.repositories import complaint_workflow_repository, evidence_repository, notification_repository
from backend.schemas.rag_knowledge import ServiceCategory
from backend.services import complaint_report_service
from backend.services import metrics as sentry_metrics
from backend.services.assignment_service import assign_next_worker
from backend.services.complaint_agent import ComplaintAgent
from backend.services.complaint_category_service import ComplaintCategoryService
from backend.services.complaint_translation_cache import get_display_text_and_summary
from backend.services.complaint_update_translation_cache import get_display_text as get_display_update_text
# Relocated to backend/services/evidence_service.py (pure move, no behavior change) so the Ask
# Sarthi image-to-complaint flow (orchestration/nodes.py) can reuse this exact validate/write/
# DB-row logic without a service module importing a route module. Aliased back to their original
# names here so every call site in this file is unchanged.
from backend.services.evidence_service import (
    SavedFile as _SavedFile,
    save_evidence_files as _save_evidence_files,
    save_photo as _save_photo,
    validate_and_write as _validate_and_write,
)
from backend.services.email_service import EmailServiceError, _email_strings, send_complaint_status_email
from backend.services.location_resolver import LocationResolver
from backend.services.sarvam_client import AIServiceError
from backend.services.translation_service import TranslationService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/complaints", tags=["complaints"])

_agent = ComplaintAgent()
_translation_service = TranslationService()
_location_resolver = LocationResolver()
_category_service = ComplaintCategoryService()

# Worker-workflow statuses that must precede each mandatory-field action -- named once here so
# the checks below and their error messages can't drift out of sync with each other.
_STATUS_ACCEPTED = "accepted"
_STATUS_IN_PROGRESS = "in_progress"
_STATUS_RESOLVED = "resolved"

# Same 80-char snippet-truncation convention as assignment_service.py's worker-facing
# notifications (_SUMMARY_SNIPPET_LENGTH there) -- kept as a local constant/helper here rather
# than imported, since that one is a private module constant of a different file, and the two
# notification "audiences" (worker vs citizen) are populated from two different call sites for
# unrelated reasons (assignment vs status change) that only coincidentally share a format.
_CITIZEN_NOTIFICATION_SNIPPET_LENGTH = 80

# Every real value Complaint.status can hold (see ComplaintStatusHistory/assignment_service.py) --
# used to validate the new `status` filter on GET /complaints and GET /complaints/area-summary
# below, the same way `lang` is validated against settings.SUPPORTED_LANGUAGES just above each of
# those. "open" is included even though it's rare/transient (assignment normally runs in the same
# request a complaint is created in) -- real data can still be seen sitting at it.
_ALL_COMPLAINT_STATUSES = ("open", "pending", "assigned", "accepted", "in_progress", "resolved")

# LIVE-REPORTED GAP: GET /complaints and GET /complaints/area-summary always returned every
# matching row -- fine at this app's current demo scale, not fine once a citizen/worker/ward has
# hundreds of complaints (every one gets sent over the wire and, for area-summary, translated on
# every single load, regardless of how much of it is ever actually looked at). `page`/`page_size`
# below are opt-in and additive: a caller that passes neither gets the exact same "everything, in
# one response" behavior as before this fix (so nothing existing breaks), while a caller that
# opts in gets a real, bounded slice plus an accurate `X-Total-Count` response header.
_DEFAULT_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 200


def _parse_status_filter(status: str | None) -> list[str] | None:
    """Accepts either one status ("resolved") or a comma-separated set ("pending,assigned,
    accepted") -- LIVE-REPORTED NEED: My Area's own "Pending" stat bucket (see get_area_summary's
    docstring) groups three raw statuses into one citizen-legible label, so a single-value filter
    alone can't express "match this whole bucket" the way a status FILTER CHIP needs to. Returns
    None for "no filter" (status was None), otherwise the parsed list -- never an empty list from
    a non-None input, so callers can safely check `if statuses:` for "was a real filter given"."""
    if status is None:
        return None
    statuses = [s for s in status.split(",") if s]
    for s in statuses:
        if s not in _ALL_COMPLAINT_STATUSES:
            raise HTTPException(status_code=400, detail=f"Unknown status: {s}")
    return statuses


def _parse_category_filter(category: str | None) -> list[str] | None:
    """Same shape/contract as _parse_status_filter above, for Complaint.service_category --
    LIVE-REPORTED NEED: My Area's own per-service filter chips (Waste/Water/Roads/Streetlights)
    need to filter the list by category the same way the status chips filter by status."""
    if category is None:
        return None
    categories = [c for c in category.split(",") if c]
    valid = {c.value for c in ServiceCategory}
    for c in categories:
        if c not in valid:
            raise HTTPException(status_code=400, detail=f"Unknown category: {c}")
    return categories


def _apply_complaint_search(query, search: str | None, db: Session, *, include_worker_name: bool = True):
    """Server-side substring search over a Complaint query -- id, ward, and the stored English
    summary/text. Matches only the stored English fields, never a per-viewer translated display
    string -- doing that against every candidate row would mean an AI translation call per row
    just to filter, which is neither fast nor something worth spending Sarvam credits on for a
    search box.

    `include_worker_name=False` (used by GET /complaints/area-summary) drops the assigned-worker
    match entirely -- that endpoint's own response never includes worker identity at all (see
    AreaSummaryResponse's docstring: "no assigned-worker name/phone anywhere in this response"),
    so letting a search term still silently reveal "a complaint assigned to worker X exists"
    through match/no-match would undercut that same anonymization by a side channel."""
    q = (search or "").strip()
    if not q:
        return query
    qid = q.lstrip("#")  # "#15" and "15" both match complaint id 15
    like = f"%{q}%"
    clauses = [
        cast(Complaint.id, String).like(f"%{qid}%"),
        Complaint.ward.ilike(like),
        Complaint.summary.ilike(like),
        Complaint.translated_text.ilike(like),
    ]
    if include_worker_name:
        worker_ids = db.query(User.id).filter(User.role == "worker", User.full_name.ilike(like))
        clauses.append(Complaint.assigned_worker_id.in_(worker_ids))
    return query.filter(or_(*clauses))


def _paginate(query, page: int | None, page_size: int | None):
    """Applies offset/limit only when the caller actually passed page and/or page_size -- see
    _DEFAULT_PAGE_SIZE's docstring above for why omitting both must return every matching row
    unchanged. Returns (possibly-sliced query, total-matching-row-count-or-None); None means "no
    slicing happened, the caller already has everything, no header needed"."""
    if page is None and page_size is None:
        return query, None
    total = query.count()
    safe_page = max(page or 1, 1)
    safe_size = max(min(page_size or _DEFAULT_PAGE_SIZE, _MAX_PAGE_SIZE), 1)
    return query.offset((safe_page - 1) * safe_size).limit(safe_size), total


def _citizen_preferred_language(db: Session, complaint: Complaint) -> str:
    """Same lookup _send_lifecycle_email_best_effort already does for the lifecycle email of the
    same event -- kept as its own small query here rather than threading the User row through
    every call site, matching this file's existing style of small, self-contained lookups.
    Defaults to "en" if the citizen row is somehow missing (should not happen in practice) or has
    no preferred_language set, same fallback _email_strings itself uses."""
    citizen = db.query(User).filter(User.id == int(complaint.citizen_id)).first()
    return (citizen.preferred_language if citizen else None) or "en"


def _citizen_notification_title(event: Literal["accepted", "started", "resolved"], lang: str) -> str:
    """LIVE-REPORTED: a citizen who picked Marathi (or any non-English UI language) still saw
    every in-app notification title in plain English ("Your complaint was accepted", etc.) --
    confirmed directly against the real dashboard. These titles were hardcoded English literals at
    each create_notification() call site below, never routed through this citizen's own
    preferred_language at all, unlike the lifecycle EMAIL for the exact same event (see
    _send_lifecycle_email_best_effort below), which already renders correctly in all 6 languages.

    Reuses email_service.py's own per-language heading table (_email_strings/_EMAIL_STRINGS)
    rather than a second, independently-invented translation set -- so the in-app notification and
    the email a citizen gets for the same event say the exact same thing in the exact same
    language, matching this codebase's existing "one wording, not a second one that could drift"
    principle (see that module's own docstring on _EMAIL_STRINGS). Falls back to English for any
    language code without copy, same as _email_strings itself."""
    return _email_strings(lang)[f"heading.{event}"]


def _citizen_notification_message(db: Session, complaint: Complaint, lang: str) -> str:
    """Same snippet+ward format as assignment_service.py's worker notifications, built from the
    complaint's own summary/translated text -- real data already on hand, nothing inferred.

    `lang`: same LIVE-REPORTED gap as _citizen_notification_title above for the "Ward:" label,
    fixed the same way -- but the label was only half of it. The snippet itself was `complaint.
    summary`/`complaint.translated_text` verbatim, both of which are always canonical English
    (see ComplaintAgent) -- so a citizen using any non-English UI still saw a correctly-translated
    "Location:" label glued onto a raw English complaint snippet in the exact same notification.
    Confirmed directly: the notification TITLE was already fixed and rendered in Marathi, but the
    home-screen notification's own message text stayed English regardless. Reuses the same
    get_display_text_and_summary cache every other view of this complaint's text already reads
    through (GET /complaints, the report, etc.) -- falls back to the raw English snippet on a
    translation failure, same as every other caller of that function."""
    location_label = _email_strings(lang)["label.location"]
    snippet = (complaint.summary or complaint.translated_text or "").strip()
    if lang and lang != "en" and snippet:
        try:
            display_text, display_summary = get_display_text_and_summary(db, complaint, lang, _translation_service)
            snippet = (display_summary or display_text or snippet).strip()
        except AIServiceError as exc:
            logger.error("On-read translation failed for complaint %s notification: %s", complaint.id, exc)
    if len(snippet) > _CITIZEN_NOTIFICATION_SNIPPET_LENGTH:
        snippet = snippet[:_CITIZEN_NOTIFICATION_SNIPPET_LENGTH].rstrip() + "…"
    ward_label = f"{location_label}: {complaint.ward}" if complaint.ward else f"{location_label}: —"
    return f"{snippet} — {ward_label}" if snippet else ward_label


def _send_lifecycle_email_best_effort(
    db: Session,
    complaint: Complaint,
    event: Literal["created", "accepted", "started", "resolved"],
    worker_update: ComplaintUpdate | None = None,
) -> None:
    """Fire-and-forget: sends the citizen a real email for one of their complaint's lifecycle
    moments, if (and only if) they have a verified email -- silently skipped otherwise, exactly
    the same as every other email_verified check in this codebase, never an error. Never raises:
    an EmailServiceError (SMTP not configured, or a real send failure) is caught and logged here,
    not surfaced to the caller -- see send_complaint_status_email's own docstring for why this
    must never fail the actual accept/start/resolve/create action it's attached to.

    Renders in the citizen's own preferred_language, translating the summary the same way the
    on-page complaint detail already does for that citizen (get_display_text_and_summary, the
    exact helper _to_response uses) -- reusing its cache rather than a separate translation path,
    and falling back to the stored English summary on an AIServiceError exactly like _to_response
    does, so a translation hiccup degrades to an English email rather than losing the send.

    LIVE-REPORTED, same bug as the on-page Updates timeline and the notification message snippet
    (see _citizen_notification_message's own docstring): `worker_note` used to be passed straight
    through untranslated -- "exactly as the worker wrote it" was really just this call site never
    having been fixed, not a real design choice, since every OTHER citizen-facing view of this
    exact same worker-authored text (the Updates timeline, the Resolution Report, the in-app
    notification) already translates it. `worker_update` is now the actual ComplaintUpdate row
    (start_work()/resolve_complaint() already have it on hand right after creating it) so this
    reuses the SAME per-update translation cache those other views already read through, instead
    of a second, uncached translation call for the same row.
    """
    citizen = db.query(User).filter(User.id == int(complaint.citizen_id)).first()
    if citizen is None or not citizen.email or not citizen.email_verified:
        return
    lang = citizen.preferred_language or "en"
    summary = complaint.summary
    if lang != "en":
        try:
            _, summary = get_display_text_and_summary(db, complaint, lang, _translation_service)
        except AIServiceError as exc:
            logger.error("Complaint %s: failed to translate lifecycle email into %s: %s", complaint.id, lang, exc)
            summary = complaint.summary
    worker_note = worker_update.text if worker_update is not None else None
    if worker_update is not None and lang != "en":
        try:
            worker_note = get_display_update_text(db, worker_update, lang, _translation_service)
        except AIServiceError as exc:
            logger.error("Complaint %s: failed to translate lifecycle email's worker note into %s: %s", complaint.id, lang, exc)
            worker_note = worker_update.text
    cta_url = f"{settings.FRONTEND_BASE_URL}/citizen/complaints/{complaint.id}" if settings.FRONTEND_BASE_URL else None
    try:
        send_complaint_status_email(
            citizen.email, event, f"JM-{complaint.id:05d}", summary or "", complaint.ward or "",
            cta_url=cta_url, lang=lang, worker_note=worker_note,
        )
    except EmailServiceError as exc:
        logger.error("Complaint %s: failed to send '%s' lifecycle email: %s", complaint.id, event, exc)


class ComplaintResponse(BaseModel):
    """Response body representing a single complaint."""

    id: int
    citizen_id: str
    original_text: str
    original_language: str
    translated_text: str
    display_text: str
    summary: str
    display_summary: str
    photo_path: str | None
    status: str
    # ServiceCategory.value, or None -- either this complaint predates the column, or
    # classification itself came back unsure (see Complaint.service_category's own docstring).
    service_category: str | None
    ward: str | None
    latitude: float | None
    longitude: float | None
    address: str | None
    # Human-readable names for whatever structured location could be resolved -- None at any
    # level that wasn't resolved (see LocationResolver). Never a fabricated value.
    location_state: str | None
    location_district: str | None
    location_ulb: str | None
    # The same resolution, as ids -- so a caller (the admin "assign worker" picker, see
    # AssignWorkerModal.tsx) can match this complaint's own location against a worker's
    # state_id/district_id/ward_id without a name-based lookup of its own. Straight off the
    # Complaint row already loaded above, not a fresh query.
    state_id: int | None
    district_id: int | None
    ward_id: int | None
    assigned_worker_name: str | None
    # Deliberately withheld until the assigned worker accepts — see _to_response.
    assigned_worker_phone: str | None
    rejection_count: int
    feedback_rating: int | None
    feedback_comment: str | None
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class FeedbackRequest(BaseModel):
    """Request body for a citizen leaving feedback on a resolved complaint."""

    rating: int = Field(ge=1, le=5)
    comment: str | None = None


class RejectComplaintRequest(BaseModel):
    """Request body for a worker rejecting a complaint. `reason` is mandatory -- enforced here
    (min_length catches the empty-string case; the route handler additionally .strip()s to catch
    whitespace-only input, which min_length alone would not)."""

    reason: str = Field(min_length=1)


class EvidenceResponse(BaseModel):
    """One uploaded evidence file -- see backend/models.py's ComplaintEvidence docstring. The
    frontend groups a complaint's flat evidence list by `stage` (CITIZEN_COMPLAINT /
    INITIAL_ASSESSMENT / PROGRESS_UPDATE / COMPLETION) and, within PROGRESS_UPDATE, by
    `update_id` (there can be more than one progress update, each with its own photos)."""

    id: int
    update_id: int | None
    uploaded_by: int
    uploader_role: str
    file_name: str
    file_path: str
    file_type: str
    file_size: int
    stage: str
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class ComplaintUpdateResponse(BaseModel):
    """One worker-authored update (initial assessment / progress update / completion) as shown
    in a complaint's timeline/detail view."""

    id: int
    update_type: str
    text: str
    photo_path: str | None
    worker_name: str | None
    created_at: str
    evidence: list[EvidenceResponse]

    model_config = ConfigDict(from_attributes=True)


class StatusHistoryEntryResponse(BaseModel):
    """One row of a complaint's status timeline."""

    from_status: str | None
    to_status: str
    actor_role: str
    note: str | None
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class RejectionResponse(BaseModel):
    """One worker's rejection of this complaint, with their reason -- admin-only (see
    _to_detail_response's viewer_role gating below). Never sent to a citizen or worker, even one
    currently assigned to the same complaint -- see reject_complaint()'s own docstring for why
    this stays purely an admin/worker-ops matter."""

    worker_name: str
    reason: str | None
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class ComplaintDetailResponse(ComplaintResponse):
    """The full detail view (GET /complaints/{id}) -- everything ComplaintResponse already has,
    plus the update/status timeline and every evidence file. A separate model from
    ComplaintResponse (not just always returning this shape from the list endpoint too) so
    GET /complaints stays a cheap list query -- fetching every complaint's full update/status/
    evidence history on every list load would be wasted work the list view never displays."""

    updates: list[ComplaintUpdateResponse]
    status_history: list[StatusHistoryEntryResponse]
    evidence: list[EvidenceResponse]
    # Always present, but only ever non-empty for an admin viewer -- see _to_detail_response.
    rejections: list[RejectionResponse]


class ComplaintReportResponse(BaseModel):
    """JSON shape for GET /complaints/{id}/report ("View Report") -- the same facts the PDF
    download renders, see complaint_report_service.ComplaintReportData."""

    complaint_id: int
    display_id: str
    service_summary: str
    original_description: str
    created_at: str
    location_ward: str | None
    location_state: str | None
    location_district: str | None
    location_ulb: str | None
    location_address: str | None
    assigned_worker_name: str | None
    initial_assessment: str | None
    initial_assessment_at: str | None
    progress_updates: list[dict]
    completion_status: str | None
    completion_evidence_photo: str | None
    resolved_at: str | None
    timeline: list[dict]
    citizen_evidence: list[str]
    initial_assessment_evidence: list[str]
    completion_evidence: list[str]


def _to_response(db: Session, complaint: Complaint, display_language: str | None) -> ComplaintResponse:
    """Build a ComplaintResponse, translating the display text and summary on read if requested.

    Args:
        db: Active database session, used to read/write the translation cache.
        complaint: The complaint ORM record.
        display_language: Short language code to translate the English text into,
            or None to display the stored English text as-is.
    """
    display_text = complaint.translated_text
    display_summary = complaint.summary
    if display_language and display_language != "en":
        try:
            display_text, display_summary = get_display_text_and_summary(
                db, complaint, display_language, _translation_service
            )
        except AIServiceError as exc:
            logger.error("On-read translation failed for complaint %s: %s", complaint.id, exc)
            display_text = complaint.translated_text
            display_summary = complaint.summary

    assigned_worker_name = None
    assigned_worker_phone = None
    if complaint.assigned_worker_id is not None:
        worker = db.query(User).filter(User.id == complaint.assigned_worker_id).first()
        if worker is not None:
            assigned_worker_name = worker.full_name
            # The citizen can see who it's assigned to as soon as it's assigned, but the phone
            # number only unlocks once that worker has actually accepted it — showing it earlier
            # would let a citizen contact someone who hasn't agreed to take the complaint yet.
            if complaint.status in ("accepted", "resolved"):
                assigned_worker_phone = worker.phone

    rejection_count = db.query(ComplaintRejection).filter(ComplaintRejection.complaint_id == complaint.id).count()

    # Join to human-readable names only for whatever was actually resolved -- one small lookup
    # each, not a fabricated value when the ID is null.
    location_state = location_district = location_ulb = None
    if complaint.state_id is not None:
        row = db.query(State).filter(State.id == complaint.state_id).first()
        location_state = row.name if row else None
    if complaint.district_id is not None:
        row = db.query(District).filter(District.id == complaint.district_id).first()
        location_district = row.name if row else None
    if complaint.ulb_id is not None:
        row = db.query(ULB).filter(ULB.id == complaint.ulb_id).first()
        location_ulb = row.name if row else None

    return ComplaintResponse(
        id=complaint.id,
        citizen_id=complaint.citizen_id,
        original_text=complaint.original_text,
        original_language=complaint.original_language,
        translated_text=complaint.translated_text,
        display_text=display_text,
        summary=complaint.summary,
        display_summary=display_summary,
        photo_path=complaint.photo_path,
        status=complaint.status,
        service_category=complaint.service_category,
        ward=complaint.ward,
        latitude=complaint.latitude,
        longitude=complaint.longitude,
        address=complaint.address,
        location_state=location_state,
        location_district=location_district,
        location_ulb=location_ulb,
        state_id=complaint.state_id,
        district_id=complaint.district_id,
        ward_id=complaint.ward_id,
        assigned_worker_name=assigned_worker_name,
        assigned_worker_phone=assigned_worker_phone,
        rejection_count=rejection_count,
        feedback_rating=complaint.feedback_rating,
        feedback_comment=complaint.feedback_comment,
        created_at=complaint.created_at.isoformat(),
    )


def _get_owned_complaint(db: Session, complaint_id: int, worker: User) -> Complaint:
    """Fetch a complaint and verify it's currently assigned to `worker`, or raise."""
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    if complaint is None:
        raise HTTPException(status_code=404, detail="Complaint not found.")
    if complaint.assigned_worker_id != worker.id:
        raise HTTPException(status_code=403, detail="This complaint isn't assigned to you.")
    return complaint


def _get_visible_complaint(db: Session, complaint_id: int, user: User) -> Complaint:
    """Fetch a complaint and verify `user` is allowed to see it -- the same visibility rule
    list_complaints() already applies (citizen owns it / worker is currently assigned / admin
    sees everything), reused here for GET /{id} and the report endpoints so authorization can
    never be bypassed by hitting a complaint id directly (see backend enforcement requirement
    in this phase's spec §21/§30 -- the frontend hiding a button is not a security boundary).
    """
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    if complaint is None:
        raise HTTPException(status_code=404, detail="Complaint not found.")
    if user.role == "citizen" and complaint.citizen_id != str(user.id):
        raise HTTPException(status_code=403, detail="This isn't your complaint.")
    if user.role == "worker" and complaint.assigned_worker_id != user.id:
        raise HTTPException(status_code=403, detail="This complaint isn't assigned to you.")
    # admins: no additional check, matches list_complaints()'s "admins see everything"
    return complaint


def _to_update_response(db: Session, update, display_language: str | None = None) -> ComplaintUpdateResponse:
    """LIVE PRODUCT FINDING: this update's `text` used to always be the raw, as-typed worker
    text, even when the surrounding complaint (via _to_response's own get_display_text_and_summary
    call) and the resolution report (via complaint_report_service.py's own
    get_display_update_text call) both already translate their own text into the viewer's own
    language on read. A Marathi-speaking citizen saw the on-page Updates timeline render a
    worker's note in raw English while the exact same note, on the same complaint's Resolution
    Report, rendered correctly in Marathi -- the cache/translation mechanism
    (ComplaintUpdateTranslation, complaint_update_translation_cache.py) already existed and
    already worked; this response just never called it. Same optional-translation-with-fallback
    shape as _to_response/get_display_text_and_summary: a None/"en" display_language (or a
    translation failure) returns the stored text exactly as before."""
    worker = db.query(User).filter(User.id == update.worker_id).first()
    evidence = [_to_evidence_response(e) for e in evidence_repository.get_evidence_for_update(db, update.id)]
    display_text = update.text
    if display_language and display_language != "en":
        try:
            display_text = get_display_update_text(db, update, display_language, _translation_service)
        except AIServiceError as exc:
            logger.error("On-read translation failed for complaint update %s: %s", update.id, exc)
            display_text = update.text
    return ComplaintUpdateResponse(
        id=update.id,
        update_type=update.update_type,
        text=display_text,
        photo_path=update.photo_path,
        worker_name=worker.full_name if worker else None,
        created_at=update.created_at.isoformat(),
        evidence=evidence,
    )


def _to_history_response(entry) -> StatusHistoryEntryResponse:
    return StatusHistoryEntryResponse(
        from_status=entry.from_status,
        to_status=entry.to_status,
        actor_role=entry.actor_role,
        note=entry.note,
        created_at=entry.created_at.isoformat(),
    )


def _to_evidence_response(entry) -> EvidenceResponse:
    return EvidenceResponse(
        id=entry.id,
        update_id=entry.update_id,
        uploaded_by=entry.uploaded_by,
        uploader_role=entry.uploader_role,
        file_name=entry.file_name,
        file_path=entry.file_path,
        file_type=entry.file_type,
        file_size=entry.file_size,
        stage=entry.stage,
        created_at=entry.created_at.isoformat(),
    )


def _to_rejection_response(db: Session, rejection: ComplaintRejection) -> RejectionResponse:
    worker = db.query(User).filter(User.id == rejection.worker_id).first()
    return RejectionResponse(
        worker_name=worker.full_name if worker else "Unknown worker",
        reason=rejection.reason,
        created_at=rejection.created_at.isoformat(),
    )


def _to_detail_response(
    db: Session, complaint: Complaint, display_language: str | None, viewer_role: str
) -> ComplaintDetailResponse:
    base = _to_response(db, complaint, display_language)
    updates = [
        _to_update_response(db, u, display_language=display_language)
        for u in complaint_workflow_repository.get_complaint_updates(db, complaint.id)
    ]
    history = [_to_history_response(h) for h in complaint_workflow_repository.get_status_history(db, complaint.id)]
    evidence = [_to_evidence_response(e) for e in evidence_repository.get_evidence_for_complaint(db, complaint.id)]
    # Admin-only, enforced here (not just hidden in the frontend) -- see RejectionResponse's own
    # docstring for why citizens/workers never get this, even for their own complaint.
    rejections = (
        [_to_rejection_response(db, r) for r in complaint_workflow_repository.get_rejections_for_complaint(db, complaint.id)]
        if viewer_role == "admin"
        else []
    )
    return ComplaintDetailResponse(
        **base.model_dump(), updates=updates, status_history=history, evidence=evidence, rejections=rejections,
    )


@router.get("/wards", response_model=list[str])
def list_wards(db: Session = Depends(get_db)) -> list[str]:
    """List every ward that has a worker assigned to it.

    Backs the ward picker on the citizen's complaint form (and now the signup form's optional
    ward field, see routes/auth.py's SignupRequest.ward): a complaint can only ever reach a
    worker if its ward exactly matches a worker's ward, so the dropdown only ever offers wards
    that actually have someone staffing them — it's impossible to pick a name that doesn't route
    anywhere. Deliberately unauthenticated (no current_user dependency) -- just a list of ward
    name strings, nothing sensitive, and Signup needs it before the citizen has a token at all.
    """
    rows = db.query(User.ward).filter(User.role == "worker", User.ward.isnot(None)).distinct().all()
    return sorted({row[0] for row in rows if row[0]})


class AreaComplaintSummary(BaseModel):
    """One complaint in a ward-wide anonymized view -- deliberately excludes citizen_id and any
    assigned-worker identity field (see AreaSummaryResponse's docstring)."""

    id: int
    status: str
    # ServiceCategory.value, or None -- not identity-revealing (unlike citizen_id/worker name),
    # so exposing it here doesn't undercut this view's anonymization.
    service_category: str | None
    display_text: str
    created_at: str
    status_updated_at: str

    model_config = ConfigDict(from_attributes=True)


class AreaSummaryResponse(BaseModel):
    """'My Area' dashboard data -- every complaint filed in the citizen's own ward, not just
    their own, so they can see what's happening around them. Deliberately anonymized: no
    citizen_id, no assigned-worker name/phone anywhere in this response -- a neighbor's
    complaint is visible by status, rough content, and dates, never by who filed it."""

    ward: str
    pending_count: int
    in_progress_count: int
    resolved_count: int
    complaints: list[AreaComplaintSummary]
    # Total complaints matching `search` (or every complaint in the ward, if no search given) --
    # NOT the same as len(complaints) once page/page_size are used, since that list is only the
    # current page. Lets the frontend build real Prev/Next controls without a separate request.
    total: int = 0


@router.get("/area-summary", response_model=AreaSummaryResponse)
def get_area_summary(
    lang: str | None = None,
    status: str | None = None,
    category: str | None = None,
    search: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
    db: Session = Depends(get_db),
    citizen: User = Depends(require_role("citizen")),
) -> AreaSummaryResponse:
    """Every complaint filed in the citizen's own ward (see User.ward's docstring), anonymized.
    Citizen-only (a worker's or admin's own "ward" means something different -- their
    operational area, not a place they live) -- requires the citizen to already have a ward set
    on their profile (signup or PATCH /auth/me), since there's nothing to key off otherwise.

    `status`/`category`/`search`/`page`/`page_size`: same opt-in, additive filtering/pagination as
    GET /complaints (see _parse_status_filter's/_parse_category_filter's/_paginate's docstrings)
    -- applies only to the `complaints` list below, never to the pending/in_progress/resolved
    stat counts. `status` accepts My Area's own three citizen-legible buckets as a comma-set --
    e.g. "pending,assigned,accepted" for the "Pending" stat's own filter chip -- not just one raw
    status.

    `category` is different on purpose: picking a service (Waste/Water/Roads/Streetlights) is
    meant to reframe the WHOLE mini-dashboard into "how's this one service doing," not just
    narrow the list below -- LIVE-REPORTED REQUEST: "if I select Roads, it should also show like
    a dashboard for Roads (how many pending, etc.)". So the stat counts stay ward-wide when
    `category` is "all" (the original "unfiltered totals above, filtered list below" behavior,
    same as every other dashboard's stat cards), but scope down to that one category's own
    pending/in_progress/resolved breakdown once a specific category is picked -- `status`/`search`
    still only narrow the list underneath that, never the stat counts themselves.
    """
    if not citizen.ward:
        raise HTTPException(status_code=400, detail="Set your ward in Settings to see your area's complaints.")
    if lang is not None and lang not in settings.SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {lang}")
    display_language = lang or citizen.preferred_language
    statuses = _parse_status_filter(status)
    categories = _parse_category_filter(category)

    ward_query = db.query(Complaint).filter(Complaint.ward == citizen.ward)
    stats_query = ward_query
    if categories:
        stats_query = stats_query.filter(Complaint.service_category.in_(categories))

    # "Pending" here groups every not-yet-started state (pending/assigned/accepted) into one
    # citizen-legible bucket -- the finer-grained worker-facing states aren't meaningful to a
    # neighbor who just wants to know "hasn't started" vs "being worked on" vs "done". Computed
    # as SQL aggregates, not a Python loop over a fetched-and-paginated list.
    pending_count = stats_query.filter(Complaint.status.in_(("pending", "assigned", "accepted"))).count()
    in_progress_count = stats_query.filter(Complaint.status == "in_progress").count()
    resolved_count = stats_query.filter(Complaint.status == "resolved").count()

    listing_query = stats_query
    if statuses:
        listing_query = listing_query.filter(Complaint.status.in_(statuses))
    listing_query = _apply_complaint_search(listing_query, search, db, include_worker_name=False).order_by(Complaint.created_at.desc())
    listing_query, total = _paginate(listing_query, page, page_size)
    if total is None:
        total = listing_query.count()
    complaints = listing_query.all()

    summaries: list[AreaComplaintSummary] = []
    for c in complaints:
        display_text = c.translated_text
        if display_language and display_language != "en":
            try:
                display_text, _ = get_display_text_and_summary(db, c, display_language, _translation_service)
            except AIServiceError as exc:
                logger.error("On-read translation failed for complaint %s (area-summary): %s", c.id, exc)
                display_text = c.translated_text

        # The date of the complaint's current status, not just when it was filed -- "this was
        # started on <date>" / "this was completed on <date>", not one flat filed-date for
        # everything regardless of where it is in the lifecycle.
        latest_history = (
            db.query(ComplaintStatusHistory)
            .filter(ComplaintStatusHistory.complaint_id == c.id)
            .order_by(ComplaintStatusHistory.created_at.desc())
            .first()
        )
        status_updated_at = latest_history.created_at.isoformat() if latest_history else c.created_at.isoformat()

        summaries.append(
            AreaComplaintSummary(
                id=c.id,
                status=c.status,
                service_category=c.service_category,
                display_text=display_text,
                created_at=c.created_at.isoformat(),
                status_updated_at=status_updated_at,
            )
        )

    return AreaSummaryResponse(
        ward=citizen.ward,
        pending_count=pending_count,
        in_progress_count=in_progress_count,
        resolved_count=resolved_count,
        complaints=summaries,
        total=total,
    )


class ClassifyCategoryRequest(BaseModel):
    """Request body for POST /complaints/classify-category."""

    text: str


class ClassifyCategoryResponse(BaseModel):
    """category is None whenever the real model layer couldn't classify this text with
    confidence -- see ComplaintCategoryService's own docstring for every reason that can happen
    (missing API key, network failure, timeout, or a genuine "I don't know"). The wizard's own
    keyword match is the next fallback layer, not this endpoint's concern."""

    category: ServiceCategory | None


@router.post(
    "/classify-category", response_model=ClassifyCategoryResponse, dependencies=[Depends(require_ai_rate_limit)]
)
def classify_complaint_category(
    body: ClassifyCategoryRequest,
    citizen: User = Depends(require_role("citizen")),
) -> ClassifyCategoryResponse:
    """First layer of the Report an Issue wizard's three-layer category classification (real
    model here -> client-side keyword match -> manual picker, see ReportIssue.tsx). A real,
    rate-limited Sarvam call -- share require_ai_rate_limit's own per-user quota with Ask Sarthi
    rather than a separate one, since both are the same kind of paid, abusable LLM call. Never
    errors on a low-confidence or failed classification: `category` is simply None, and the
    frontend falls through to its own next layer exactly as if this endpoint didn't exist."""
    category = _category_service.classify(body.text)
    return ClassifyCategoryResponse(category=category)


@router.post("", response_model=ComplaintResponse)
def create_complaint(
    language: str = Form(...),
    text: str | None = Form(None),
    ward: str | None = Form(None),
    # The Report an Issue wizard's own 3-layer classification (real model -> keyword -> manual
    # picker, see ReportIssue.tsx) already resolves one of these before the citizen ever hits
    # submit -- LIVE-REPORTED GAP: it was classified and then thrown away, never sent along with
    # the rest of the form, so Complaint.service_category stayed unset for every form-filed
    # complaint. Validated against the real enum below rather than trusted as free text.
    category: str | None = Form(None),
    latitude: float | None = Form(None),
    longitude: float | None = Form(None),
    accuracy: float | None = Form(None),
    address: str | None = Form(None),
    photo: UploadFile | None = File(None),
    photos: list[UploadFile] | None = File(None),
    audio: list[UploadFile] | None = File(None),
    db: Session = Depends(get_db),
    citizen: User = Depends(require_role("citizen")),
) -> ComplaintResponse:
    """Create a new complaint from typed text or a voice recording, with optional photo evidence.

    `photo` (singular) is kept for backward compatibility and still populates the legacy
    `Complaint.photo_path` column; `photos` (plural, evidence upload phase) is the current way to
    attach one or more images -- each is saved as its own ComplaintEvidence row
    (stage="CITIZEN_COMPLAINT") once the complaint has an id. A citizen may use either field, or
    both -- there's no reason to reject `photo` callers just because `photos` now exists.

    `audio` may contain more than one file: Sarvam's speech-to-text endpoint hard-caps a
    single request at 30 seconds of audio (see docs/ai_pipeline_limits.md), so a longer
    recording arrives as several short chunks — the citizen-facing recorder splits into
    ~28s segments client-side (frontend-react/src/lib/useAudioRecorder.ts) rather than
    sending one file that would just get rejected. Each chunk is transcribed independently
    and stitched back into one transcript (see ComplaintAgent._transcribe_chunks).

    The citizen is taken from the authenticated session — there is no way to
    submit a complaint on someone else's behalf. Immediately assigned to the first
    eligible worker in its ward, if any (see assignment_service.py).

    `latitude`/`longitude`/`accuracy` are optional — GPS is never required; a citizen who
    declines location permission (or whose browser doesn't support it) still submits normally
    with just `ward`, exactly as before this field existed. This complaint's location is always
    independent of anything on the citizen's own account — there is no "home location" to fall
    back to or conflict with (see docs/location_data_audit.md §4 and the location migration
    plan's §D/§6).
    """
    if language not in settings.SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {language}")
    parsed_category: ServiceCategory | None = None
    if category is not None and category.strip():
        try:
            parsed_category = ServiceCategory(category.strip().upper())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Unknown category: {category}") from exc

    audio_chunks = [a.file.read() for a in audio if a.filename] if audio else None
    if not text and not audio_chunks:
        raise HTTPException(status_code=400, detail="Either text or audio must be provided.")

    photo_path = _save_photo(photo) if photo is not None and photo.filename else None

    try:
        complaint = _agent.create_complaint(
            db=db,
            citizen_id=str(citizen.id),
            language_code=language,
            text=text,
            audio_chunks=audio_chunks,
            photo_path=photo_path,
            category=parsed_category,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    needs_commit = False

    if ward is not None and ward.strip():
        complaint.ward = ward.strip()
        needs_commit = True

    # Out-of-range coordinates (a malformed/buggy client, not a real GPS fix) are silently
    # dropped rather than stored as garbage or rejecting the whole complaint -- treated exactly
    # like "no GPS provided" from here on, consistent with GPS never being required. A citizen's
    # ability to file a complaint must never depend on their device sending well-formed
    # coordinates.
    if latitude is not None and longitude is not None and not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        logger.warning("Complaint for citizen %s: discarding out-of-range coordinates (%s, %s)", citizen.id, latitude, longitude)
        latitude = longitude = accuracy = None

    # Raw coordinates are always stored exactly as received, regardless of whether any
    # administrative detail can be resolved from them (see LocationResolver's module docstring).
    if latitude is not None and longitude is not None:
        complaint.latitude = latitude
        complaint.longitude = longitude
        complaint.gps_accuracy = accuracy
        needs_commit = True
    if address is not None and address.strip():
        complaint.address = address.strip()
        needs_commit = True

    # Structured hierarchy: prefer resolving the citizen's own ward *text* pick (more precise
    # than city-level GPS, and it's what assignment already keys on) -- only fall back to a live
    # reverse-geocode of the coordinates when the ward text didn't resolve to a known ward, so a
    # normal submission with a recognized ward never pays for a network round-trip it doesn't
    # need. Either path only ever fills in what could actually be resolved -- never a guess.
    #
    # Wrapped in try/except as defense-in-depth: LocationResolver's real implementation never
    # raises (see its module docstring), but this is also the one deliberately swappable seam
    # for a future provider (§ the resolver's docstring) -- a badly-behaved future geocoder must
    # still never be able to turn "GPS failed" into "complaint creation failed" (an explicit
    # requirement, see docs/location_migration_plan.md). Resolution is best-effort location
    # enrichment, never a precondition for filing a complaint at all.
    try:
        resolved_ward = _location_resolver.resolve_ward_by_text(db, complaint.ward) if complaint.ward else None
        if resolved_ward is not None:
            chain = _location_resolver.location_chain_for_ward(db, resolved_ward)
            for field, value in chain.items():
                setattr(complaint, field, value)
            needs_commit = True
        elif latitude is not None and longitude is not None:
            resolved = _location_resolver.resolve_coordinates(latitude, longitude)
            if resolved.warnings:
                logger.info("Complaint %s: GPS resolution warnings: %s", complaint.id, resolved.warnings)
            ids = _location_resolver.normalize_location(db, resolved)
            for field, value in ids.items():
                if value is not None:
                    setattr(complaint, field, value)
            if resolved.formatted_address and not complaint.address:
                complaint.address = resolved.formatted_address
            needs_commit = True
    except Exception:
        logger.exception(
            "Complaint %s: location resolution raised unexpectedly -- continuing without "
            "resolved administrative location. Raw ward text / GPS coordinates already stored "
            "above are unaffected.",
            complaint.id,
        )

    if needs_commit:
        db.commit()
        db.refresh(complaint)

    # Evidence photos are saved after the complaint has an id (ComplaintEvidence.complaint_id is
    # a real FK) -- do this before assignment, not after, so evidence is never silently lost if
    # assignment somehow raised (it doesn't today, but ordering here costs nothing and removes
    # the question).
    _save_evidence_files(
        db, files=photos, complaint_id=complaint.id, update_id=None,
        uploaded_by=citizen.id, uploader_role="citizen", stage="CITIZEN_COMPLAINT",
    )

    assign_next_worker(db, complaint)
    db.refresh(complaint)

    # A no-op when Sentry metrics aren't enabled -- see backend/services/metrics.py's own
    # docstring for why that gating lives there (the SDK's own enable_metrics flag is a confirmed
    # no-op) rather than a settings check here too. Tagged by ward -- a more directly actionable
    # breakdown for Sentry's own dashboards ("which ward is generating the most complaints") than
    # category would be; category is still stored on the row itself (service_category above), just
    # not also duplicated into this specific metric's attributes.
    sentry_metrics.count("complaint.created", 1, attributes={"ward": complaint.ward or "unknown"})
    _send_lifecycle_email_best_effort(db, complaint, "created")

    return _to_response(db, complaint, display_language=None)


@router.get("", response_model=list[ComplaintResponse])
def list_complaints(
    response: Response,
    lang: str | None = None,
    worker_id: int | None = None,
    status: str | None = None,
    category: str | None = None,
    search: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int | None = None,
    page_size: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ComplaintResponse]:
    """List complaints visible to the current user, translated into `lang` on read.

    Citizens see only their own complaints; workers see only complaints currently
    assigned to them specifically (not just anyone in their ward — see
    assignment_service.py); super admins see every complaint. Defaults to the
    user's own preferred language if `lang` is not given.

    `worker_id` narrows an admin's own "everything" view down to one worker's complaint
    history — the data source for the Admin "Worker Detail" performance page
    (AdminWorkerDetail.tsx: assigned/accepted/in_progress/resolved counts, and a "View Report"
    link per resolved complaint). Deliberately a no-op for citizen/worker callers (their own
    role filter above already applies and takes precedence) rather than a 403 — there's no
    security question to answer for a param that silently does nothing outside admin's own
    already-unrestricted "see everything" case.

    `status`/`category`/`search`/`date_from`/`date_to`/`page`/`page_size`: real, server-side
    filtering and pagination -- see _parse_status_filter's/_parse_category_filter's,
    _apply_complaint_search's, and _paginate's own docstrings (`status`/`category` each accept a
    comma-separated set, e.g. "pending,assigned,accepted", not just one value). `date_from`/
    `date_to` are an inclusive calendar-day range over `created_at` (when the complaint was
    filed). All are optional and additive; omitting page/page_size preserves the exact old
    "every matching row, no header" behavior.
    """
    if lang is not None and lang not in settings.SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {lang}")
    display_language = lang or current_user.preferred_language
    statuses = _parse_status_filter(status)
    categories = _parse_category_filter(category)

    query = db.query(Complaint)
    if current_user.role == "citizen":
        query = query.filter(Complaint.citizen_id == str(current_user.id))
    elif current_user.role == "worker":
        query = query.filter(Complaint.assigned_worker_id == current_user.id)
    elif current_user.role == "admin" and worker_id is not None:
        query = query.filter(Complaint.assigned_worker_id == worker_id)
    # admins with no worker_id: everything, unchanged

    if statuses:
        query = query.filter(Complaint.status.in_(statuses))
    if categories:
        query = query.filter(Complaint.service_category.in_(categories))
    if date_from is not None:
        query = query.filter(Complaint.created_at >= datetime.combine(date_from, time.min))
    if date_to is not None:
        query = query.filter(Complaint.created_at < datetime.combine(date_to, time.min) + timedelta(days=1))
    query = _apply_complaint_search(query, search, db)
    query = query.order_by(Complaint.created_at.desc())

    query, total = _paginate(query, page, page_size)
    if total is not None:
        response.headers["X-Total-Count"] = str(total)

    complaints = query.all()
    return [_to_response(db, c, display_language=display_language) for c in complaints]


class ServiceStatusCount(BaseModel):
    """One row of the "complaints by service" breakdown (service category + status, each showing
    a total) -- feeds the zoom-drilldown donut on the Worker/Admin dashboards. A service/status
    combo with zero complaints is simply absent, not returned as a zero row, same convention
    GET /complaints/by-location already uses."""

    service_category: str
    status: str
    total: int


@router.get("/by-service", response_model=list[ServiceStatusCount])
def complaints_by_service(
    worker_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ServiceStatusCount]:
    """Complaint counts grouped by service category AND status, scoped by role exactly like
    list_complaints() above (citizens/workers see only their own; admins see everything, or one
    worker's own complaints via `worker_id`). Registered before GET /{complaint_id} so "by-service"
    is never captured as a complaint_id path param."""
    query = db.query(Complaint.service_category, Complaint.status, func.count(Complaint.id))
    if current_user.role == "citizen":
        query = query.filter(Complaint.citizen_id == str(current_user.id))
    elif current_user.role == "worker":
        query = query.filter(Complaint.assigned_worker_id == current_user.id)
    elif current_user.role == "admin" and worker_id is not None:
        query = query.filter(Complaint.assigned_worker_id == worker_id)
    # admins with no worker_id: everything, unchanged

    rows = query.group_by(Complaint.service_category, Complaint.status).all()
    # LIVE-REPORTED BUG: a complaint filed with no category set at all -- `category` is optional
    # on POST /complaints (create_complaint), and older/incomplete submissions can genuinely have
    # `service_category IS NULL` -- crashed this ENTIRE endpoint for every citizen/worker/admin
    # with a 500, since ServiceStatusCount.service_category is a required string. The donut this
    # feeds (ServiceDonutPanel.tsx) is built around SERVICE_CATEGORY_DEFS' fixed, known set of
    # categories -- an uncategorized complaint doesn't belong in any of those slices, so it's
    # correctly excluded here, not crashed on -- same "absent, not a zero row" convention this
    # endpoint's own docstring already documents for a service/status combo with no complaints.
    return [
        ServiceStatusCount(service_category=service_category, status=status, total=total)
        for service_category, status, total in rows
        if service_category is not None
    ]


class DailyComplaintTrend(BaseModel):
    """One calendar day's real opened/resolved/accepted/rejected counts -- feeds the worker
    dashboard's "Opened vs. resolved" activity line chart."""

    date: str
    opened: int
    resolved: int
    accepted: int
    rejected: int


@router.get("/trend", response_model=list[DailyComplaintTrend])
def complaints_trend(
    worker_id: int | None = None,
    days: int = 7,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DailyComplaintTrend]:
    """Real daily opened/resolved/accepted/rejected counts for the last `days` calendar days,
    scoped by role exactly like list_complaints()/complaints_by_service() above (and, like
    complaints_by_service, registered before GET /{complaint_id} so "trend" is never captured as a
    complaint_id path param). "Opened" comes from Complaint.created_at; "resolved"/"accepted" come
    from ComplaintStatusHistory.to_status -- deliberately NOT Complaint.status, since a complaint's
    current status says nothing about WHEN it reached that status, only the status-change audit
    trail does (see ComplaintStatusHistory's own docstring). "rejected" comes from
    ComplaintRejection, a separate table (not a status -- a rejected complaint goes right back to
    "pending"/reassignment, so there's no "rejected" value ComplaintStatusHistory.to_status could
    ever hold; see ComplaintRejection's own docstring).

    Aggregated in Python, same reasoning as ai_request_log_repository.get_daily_ai_stats' identical
    pattern -- but unlike that endpoint (which only returns days that actually had activity), this
    always returns exactly `days` calendar days including zero-count ones, since the chart this
    feeds needs a fixed x-axis, not a range that shrinks on a quiet day.
    """
    opened_query = db.query(Complaint.created_at)
    resolved_query = (
        db.query(ComplaintStatusHistory.created_at)
        .join(Complaint, Complaint.id == ComplaintStatusHistory.complaint_id)
        .filter(ComplaintStatusHistory.to_status == "resolved")
    )
    accepted_query = (
        db.query(ComplaintStatusHistory.created_at)
        .join(Complaint, Complaint.id == ComplaintStatusHistory.complaint_id)
        .filter(ComplaintStatusHistory.to_status == "accepted")
    )
    rejected_query = db.query(ComplaintRejection.created_at).join(Complaint, Complaint.id == ComplaintRejection.complaint_id)
    if current_user.role == "citizen":
        opened_query = opened_query.filter(Complaint.citizen_id == str(current_user.id))
        resolved_query = resolved_query.filter(Complaint.citizen_id == str(current_user.id))
        accepted_query = accepted_query.filter(Complaint.citizen_id == str(current_user.id))
        rejected_query = rejected_query.filter(Complaint.citizen_id == str(current_user.id))
    elif current_user.role == "worker":
        opened_query = opened_query.filter(Complaint.assigned_worker_id == current_user.id)
        resolved_query = resolved_query.filter(Complaint.assigned_worker_id == current_user.id)
        accepted_query = accepted_query.filter(Complaint.assigned_worker_id == current_user.id)
        rejected_query = rejected_query.filter(ComplaintRejection.worker_id == current_user.id)
    elif current_user.role == "admin" and worker_id is not None:
        opened_query = opened_query.filter(Complaint.assigned_worker_id == worker_id)
        resolved_query = resolved_query.filter(Complaint.assigned_worker_id == worker_id)
        accepted_query = accepted_query.filter(Complaint.assigned_worker_id == worker_id)
        rejected_query = rejected_query.filter(ComplaintRejection.worker_id == worker_id)
    # admins with no worker_id: everything, unchanged -- same convention as complaints_by_service.

    def _count_by_day(rows: list[tuple[datetime]]) -> dict[str, int]:
        by_day: dict[str, int] = {}
        for (created_at,) in rows:
            day = created_at.strftime("%Y-%m-%d")
            by_day[day] = by_day.get(day, 0) + 1
        return by_day

    opened_by_day = _count_by_day(opened_query.all())
    resolved_by_day = _count_by_day(resolved_query.all())
    accepted_by_day = _count_by_day(accepted_query.all())
    rejected_by_day = _count_by_day(rejected_query.all())

    today = datetime.now(timezone.utc)
    days_list = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
    return [
        DailyComplaintTrend(
            date=day,
            opened=opened_by_day.get(day, 0),
            resolved=resolved_by_day.get(day, 0),
            accepted=accepted_by_day.get(day, 0),
            rejected=rejected_by_day.get(day, 0),
        )
        for day in days_list
    ]


@router.get("/{complaint_id}", response_model=ComplaintDetailResponse)
def get_complaint(
    complaint_id: int,
    lang: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ComplaintDetailResponse:
    """Full detail view of one complaint -- the citizen/worker/admin task-detail page's data
    source. Same visibility rule as list_complaints() (see _get_visible_complaint), enforced
    server-side regardless of what the frontend does or doesn't render."""
    if lang is not None and lang not in settings.SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {lang}")
    complaint = _get_visible_complaint(db, complaint_id, current_user)
    display_language = lang or current_user.preferred_language
    return _to_detail_response(db, complaint, display_language=display_language, viewer_role=current_user.role)


@router.post("/{complaint_id}/accept", response_model=ComplaintResponse)
def accept_complaint(
    complaint_id: int,
    db: Session = Depends(get_db),
    worker: User = Depends(require_role("worker")),
) -> ComplaintResponse:
    """Accept a complaint currently assigned to you. Unlocks your phone number for the citizen."""
    complaint = _get_owned_complaint(db, complaint_id, worker)
    if complaint.status != "assigned":
        raise HTTPException(status_code=400, detail=f"Cannot accept a complaint that is '{complaint.status}'.")

    previous_status = complaint.status
    complaint.status = "accepted"
    db.commit()
    db.refresh(complaint)
    complaint_workflow_repository.record_status_change(
        db, complaint, from_status=previous_status, to_status="accepted",
        actor_role="worker", actor_user_id=worker.id,
    )
    citizen_lang = _citizen_preferred_language(db, complaint)
    notification_repository.create_notification(
        db,
        recipient_id=int(complaint.citizen_id),
        type="COMPLAINT_ACCEPTED",
        title=_citizen_notification_title("accepted", citizen_lang),
        message=_citizen_notification_message(db, complaint, citizen_lang),
        complaint_id=complaint.id,
    )
    _send_lifecycle_email_best_effort(db, complaint, "accepted")
    logger.info("Complaint %s accepted by worker %s", complaint_id, worker.id)
    return _to_response(db, complaint, display_language=None)


@router.post("/{complaint_id}/reject", response_model=ComplaintResponse)
def reject_complaint(
    complaint_id: int,
    body: RejectComplaintRequest,
    db: Session = Depends(get_db),
    worker: User = Depends(require_role("worker")),
) -> ComplaintResponse:
    """Reject a complaint currently assigned to you — hands it to the next worker in your ward.

    `reason` is mandatory (see RejectComplaintRequest) -- Pydantic's min_length catches an empty
    string, and the explicit .strip() check below additionally catches whitespace-only input,
    which min_length alone would not reject.

    Deliberately never notifies (or otherwise informs) the citizen -- a rejection is an internal
    worker/admin operational matter, not something a citizen needs or should know happened; the
    citizen only ever sees the same generic "waiting to be assigned" state either way. Every admin
    IS notified (see the broadcast below and RejectionResponse in this file), since they're the
    ones who need to know a worker declined an assignment and why.
    """
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="A rejection reason is required.")

    complaint = _get_owned_complaint(db, complaint_id, worker)
    if complaint.status != "assigned":
        raise HTTPException(status_code=400, detail=f"Cannot reject a complaint that is '{complaint.status}'.")

    complaint_workflow_repository.record_rejection(db, complaint_id=complaint.id, worker_id=worker.id, reason=reason)
    logger.info("Complaint %s rejected by worker %s (reason recorded)", complaint_id, worker.id)

    # Broadcast to every admin -- same per-admin loop pattern as ai_request_log_repository.py's
    # _try_fire (AI_ALERT), except this one DOES carry a complaint_id (AI_ALERT never does, since
    # it isn't about any single complaint). Same LIVE-REPORTED gap as the citizen/worker
    # notifications above -- the title was hardcoded English; each admin may have their own
    # preferred_language, so this is localized PER admin inside the loop, not once outside it.
    #
    # LIVE-REPORTED, part two: fixing the title wasn't the whole gap here either -- the message's
    # own sentence ("{worker} rejected a complaint in {ward}.") was a hardcoded English f-string
    # regardless of the admin's preferred_language, the same class of bug as the citizen/worker
    # notification messages above. The worker's name and the ward name themselves are still never
    # translated (real proper nouns/data, not prose) -- only the surrounding sentence template.
    for admin in db.query(User).filter(User.role == "admin").all():
        admin_strings = _email_strings(admin.preferred_language or "en")
        notification_repository.create_notification(
            db,
            recipient_id=admin.id,
            type="COMPLAINT_REJECTED",
            title=admin_strings["heading.workerRejected"],
            message=admin_strings["message.workerRejected"].format(worker=worker.full_name, ward=complaint.ward),
            complaint_id=complaint.id,
        )

    # assign_next_worker() records its own status-history row for the reassignment outcome
    # (assigned-to-someone-else, or pending) -- that IS the record of this rejection's effect, so
    # no separate "rejected" status-history row is added here (status doesn't actually pass
    # through a persisted "rejected" state in this architecture -- see ComplaintRejection's
    # docstring and assignment_service.py's module docstring).
    assign_next_worker(db, complaint)
    db.refresh(complaint)
    return _to_response(db, complaint, display_language=None)


@router.post("/{complaint_id}/start", response_model=ComplaintResponse)
def start_work(
    complaint_id: int,
    assessment: str = Form(...),
    photos: list[UploadFile] | None = File(None),
    db: Session = Depends(get_db),
    worker: User = Depends(require_role("worker")),
) -> ComplaintResponse:
    """Start work on an accepted complaint -- requires a mandatory initial assessment, and moves
    the complaint from "accepted" to "in_progress". Cannot be called again once already
    in_progress/resolved (see the status check below), matching this phase's explicit rule that
    "Start Work" must not be repeatable.

    Now `multipart/form-data` (was a plain JSON body) so it can accept optional evidence photos
    -- the same breaking contract change `/resolve` already went through when completion evidence
    was added; the frontend's StartWorkModal sends FormData accordingly.
    """
    assessment = assessment.strip()
    if not assessment:
        raise HTTPException(status_code=400, detail="An initial assessment is required to start work.")

    complaint = _get_owned_complaint(db, complaint_id, worker)
    if complaint.status != _STATUS_ACCEPTED:
        raise HTTPException(status_code=400, detail=f"Cannot start work on a complaint that is '{complaint.status}'.")

    previous_status = complaint.status
    complaint.status = _STATUS_IN_PROGRESS
    db.commit()
    db.refresh(complaint)

    update = complaint_workflow_repository.add_complaint_update(
        db, complaint_id=complaint.id, worker_id=worker.id, update_type="INITIAL_ASSESSMENT", text=assessment,
    )
    _save_evidence_files(
        db, files=photos, complaint_id=complaint.id, update_id=update.id,
        uploaded_by=worker.id, uploader_role="worker", stage="INITIAL_ASSESSMENT",
    )
    complaint_workflow_repository.record_status_change(
        db, complaint, from_status=previous_status, to_status=_STATUS_IN_PROGRESS,
        actor_role="worker", actor_user_id=worker.id, note="Work started.",
    )
    citizen_lang = _citizen_preferred_language(db, complaint)
    notification_repository.create_notification(
        db,
        recipient_id=int(complaint.citizen_id),
        type="COMPLAINT_STARTED",
        title=_citizen_notification_title("started", citizen_lang),
        message=_citizen_notification_message(db, complaint, citizen_lang),
        complaint_id=complaint.id,
    )
    _send_lifecycle_email_best_effort(db, complaint, "started", worker_update=update)
    logger.info("Complaint %s work started by worker %s", complaint_id, worker.id)
    return _to_response(db, complaint, display_language=None)


@router.post("/{complaint_id}/updates", response_model=ComplaintUpdateResponse)
def add_progress_update(
    complaint_id: int,
    text: str = Form(...),
    photos: list[UploadFile] | None = File(None),
    db: Session = Depends(get_db),
    worker: User = Depends(require_role("worker")),
) -> ComplaintUpdateResponse:
    """Add an OPTIONAL progress update while a complaint is in_progress. Calling this endpoint at
    all is optional (zero, one, or many updates) -- but the text of any update that IS submitted
    is mandatory, and evidence is optional (zero or more photos), matching this phase's explicit
    rule.
    """
    cleaned_text = text.strip()
    if not cleaned_text:
        raise HTTPException(status_code=400, detail="Update text is required.")

    complaint = _get_owned_complaint(db, complaint_id, worker)
    if complaint.status != _STATUS_IN_PROGRESS:
        raise HTTPException(status_code=400, detail=f"Cannot add a progress update to a complaint that is '{complaint.status}'.")

    update = complaint_workflow_repository.add_complaint_update(
        db, complaint_id=complaint.id, worker_id=worker.id, update_type="PROGRESS_UPDATE", text=cleaned_text,
    )
    _save_evidence_files(
        db, files=photos, complaint_id=complaint.id, update_id=update.id,
        uploaded_by=worker.id, uploader_role="worker", stage="PROGRESS_UPDATE",
    )
    logger.info("Complaint %s progress update added by worker %s", complaint_id, worker.id)
    return _to_update_response(db, update)


@router.post("/{complaint_id}/resolve", response_model=ComplaintResponse)
def resolve_complaint(
    complaint_id: int,
    completion_status: str = Form(...),
    photos: list[UploadFile] | None = File(None),
    db: Session = Depends(get_db),
    worker: User = Depends(require_role("worker")),
) -> ComplaintResponse:
    """Mark an in_progress complaint resolved -- requires a mandatory completion status/report
    and accepts optional completion evidence (zero or more photos), reusing the exact same
    upload/storage mechanism every other photo in this app already uses (_save_evidence_files).

    Order matters for data integrity (§17/§41 of this phase's spec): the ComplaintUpdate(
    COMPLETION) row is written and committed BEFORE `complaint.status` is set to "resolved" and
    committed, so a failure between those two steps leaves the complaint still "in_progress"
    (not silently resolved without a completion record) rather than the reverse. Evidence files
    are saved after that row commits (they reference its id), still before the status flip.
    """
    cleaned_status = completion_status.strip()
    if not cleaned_status:
        raise HTTPException(status_code=400, detail="A completion status is required before resolving.")

    complaint = _get_owned_complaint(db, complaint_id, worker)
    if complaint.status != _STATUS_IN_PROGRESS:
        raise HTTPException(status_code=400, detail=f"Cannot resolve a complaint that is '{complaint.status}'.")

    # Completion record written and committed FIRST -- see docstring above for why this ordering
    # is the data-integrity guarantee, not an arbitrary choice.
    completion_update = complaint_workflow_repository.add_complaint_update(
        db, complaint_id=complaint.id, worker_id=worker.id, update_type="COMPLETION", text=cleaned_status,
    )
    _save_evidence_files(
        db, files=photos, complaint_id=complaint.id, update_id=completion_update.id,
        uploaded_by=worker.id, uploader_role="worker", stage="COMPLETION",
    )

    previous_status = complaint.status
    complaint.status = _STATUS_RESOLVED
    db.commit()
    db.refresh(complaint)
    complaint_workflow_repository.record_status_change(
        db, complaint, from_status=previous_status, to_status=_STATUS_RESOLVED,
        actor_role="worker", actor_user_id=worker.id, note="Marked resolved.",
    )
    citizen_lang = _citizen_preferred_language(db, complaint)
    notification_repository.create_notification(
        db,
        recipient_id=int(complaint.citizen_id),
        type="COMPLAINT_RESOLVED",
        title=_citizen_notification_title("resolved", citizen_lang),
        message=_citizen_notification_message(db, complaint, citizen_lang),
        complaint_id=complaint.id,
    )
    _send_lifecycle_email_best_effort(db, complaint, "resolved", worker_update=completion_update)
    logger.info("Complaint %s resolved by worker %s", complaint_id, worker.id)
    return _to_response(db, complaint, display_language=None)


@router.post("/{complaint_id}/feedback", response_model=ComplaintResponse)
def submit_feedback(
    complaint_id: int,
    body: FeedbackRequest,
    db: Session = Depends(get_db),
    citizen: User = Depends(require_role("citizen")),
) -> ComplaintResponse:
    """Leave a rating (and optional comment) on your own resolved complaint."""
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    if complaint is None:
        raise HTTPException(status_code=404, detail="Complaint not found.")
    if complaint.citizen_id != str(citizen.id):
        raise HTTPException(status_code=403, detail="This isn't your complaint.")
    if complaint.status != "resolved":
        raise HTTPException(status_code=400, detail="You can only leave feedback once a complaint is resolved.")

    complaint.feedback_rating = body.rating
    complaint.feedback_comment = body.comment.strip() if body.comment else None
    db.commit()
    db.refresh(complaint)
    logger.info("Feedback submitted for complaint %s by citizen %s", complaint_id, citizen.id)
    return _to_response(db, complaint, display_language=None)


# ------------------------------------------------------------------
# Complaint Resolution Report -- available only once a complaint is "resolved" (§18/§22 of this
# phase's spec: never a fake/partial report before then). Both endpoints share the same
# authorization rule as get_complaint() (_get_visible_complaint) -- a worker/citizen/admin can
# only ever see a report for a complaint they're already allowed to view; there is no separate,
# weaker authorization path for reports. Report content is assembled entirely from real DB rows
# (complaint_report_service.build_report_data) -- never generated or embellished by an LLM.
# ------------------------------------------------------------------


def _require_resolved(complaint: Complaint) -> None:
    if complaint.status != _STATUS_RESOLVED:
        raise HTTPException(
            status_code=404,
            detail="No report is available yet -- this complaint has not been resolved.",
        )


@router.get("/{complaint_id}/report", response_model=ComplaintReportResponse)
def view_report(
    complaint_id: int,
    lang: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ComplaintReportResponse:
    """JSON report data for the in-app "View Report"/"Summary" screens -- `service_summary`/
    `original_description` are translated into the viewer's own language on read, same as
    GET /complaints already does (see `_to_response`), so this no longer shows raw English next
    to an otherwise fully-translated UI. Defaults to the viewer's own `preferred_language`,
    overridable via `?lang=`, matching /complaints and /complaints/area-summary's own convention."""
    if lang is not None and lang not in settings.SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {lang}")
    display_language = lang or current_user.preferred_language
    complaint = _get_visible_complaint(db, complaint_id, current_user)
    _require_resolved(complaint)
    data = complaint_report_service.build_report_data(db, complaint, display_language, _translation_service)
    return ComplaintReportResponse(
        complaint_id=data.complaint_id,
        display_id=data.display_id,
        service_summary=data.service_summary,
        original_description=data.original_description,
        created_at=data.created_at.isoformat(),
        location_ward=data.location_ward,
        location_state=data.location_state,
        location_district=data.location_district,
        location_ulb=data.location_ulb,
        location_address=data.location_address,
        assigned_worker_name=data.assigned_worker_name,
        initial_assessment=data.initial_assessment,
        initial_assessment_at=data.initial_assessment_at.isoformat() if data.initial_assessment_at else None,
        progress_updates=[
            {**u, "created_at": u["created_at"].isoformat()} for u in data.progress_updates
        ],
        completion_status=data.completion_status,
        completion_evidence_photo=data.completion_evidence_photo,
        resolved_at=data.resolved_at.isoformat() if data.resolved_at else None,
        timeline=[
            {**e, "created_at": e["created_at"].isoformat()} for e in data.timeline
        ],
        citizen_evidence=data.citizen_evidence,
        initial_assessment_evidence=data.initial_assessment_evidence,
        completion_evidence=data.completion_evidence,
    )


@router.get("/{complaint_id}/report/download")
def download_report(
    complaint_id: int,
    lang: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """PDF download of the same report data view_report() returns as JSON -- same `?lang=`
    convention (defaults to the viewer's own preferred_language, overridable), so the downloaded
    document and the in-app "View Report" popup show the same language by default."""
    if lang is not None and lang not in settings.SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {lang}")
    display_language = lang or current_user.preferred_language
    complaint = _get_visible_complaint(db, complaint_id, current_user)
    _require_resolved(complaint)
    data = complaint_report_service.build_report_data(db, complaint, display_language, _translation_service)
    try:
        pdf_bytes = complaint_report_service.generate_pdf_bytes(data, display_language)
    except Exception:
        logger.exception("Report PDF generation failed for complaint %s", complaint_id)
        raise HTTPException(status_code=500, detail="Could not generate the report. Please try again.")

    filename = f"JanSarthi_Complaint_{data.display_id}_Report.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
