"""Super-admin-only endpoints: creating and listing worker accounts.

Every route here requires the "admin" role via require_role("admin"). There is
no route anywhere in the app that lets a citizen or worker create another
worker or admin account — provisioning staff is exclusively a super admin action.
"""

import logging
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.deps import require_otp_rate_limit, require_role
from backend.models import Complaint, ComplaintRejection, ComplaintStatusHistory, ComplaintTranslation, ComplaintUpdate, Locality, ULB, User, Ward
from backend.repositories import ai_request_log_repository, complaint_workflow_repository
from backend.routes.auth import _dev_cache_otp, MIN_PASSWORD_LENGTH, UserResponse
from backend.services.auth_service import (
    consume_signup_email_verification,
    create_signup_email_otp,
    hash_password,
    verify_signup_email_otp,
)
from backend.services.email_service import EmailServiceError, send_otp_email
from backend.services.location_resolver import LocationResolver
from backend.services.observability import tracing

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])
_location_resolver = LocationResolver()

# Same shape as auth.py's own (private) _EMAIL_PATTERN -- kept as a separate constant here rather
# than importing that one across modules, since it's underscore-named there specifically to signal
# "not a cross-module contract".
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Same value as auth.py's own (private) _VERIFY_EMAIL_PURPOSE -- see send_otp_email's own purpose
# parameter; only affects which email template/copy is used, not the OTP logic itself.
_VERIFY_EMAIL_PURPOSE = "verify_email"

# "Open" = assigned to a worker but not yet resolved, at any stage of the worker's own workflow
# (see routes/complaints.py's `_STATUS_ACCEPTED`/`_STATUS_IN_PROGRESS`). Named once here so
# list_workers()'s open-count query and delete_worker()'s reset-to-pending query can't drift out
# of sync with each other the way they already did once (in_progress was added to that workflow
# without this file being updated to match -- see the ComplaintStatusHistory-related fixes below).
_OPEN_COMPLAINT_STATUSES = ("assigned", "accepted", "in_progress")


class CreateWorkerRequest(BaseModel):
    """Request body for a super admin creating a new worker account.

    `email`: optional, unlike `phone` -- a worker account is fully usable with phone-only login,
    same as a citizen's (see auth.py's LoginRequest docstring). When given, it must be PROVEN via
    the same OTP round trip citizen signup uses (POST /admin/workers/email/send-code ->
    POST /admin/workers/email/verify-code -> `email_verification_token` here), not merely typed --
    an admin can assert a worker's phone number and temporary password on their behalf with no
    verification step, but an email inbox is something only whoever holds it can actually prove,
    so this endpoint holds itself to the same standard signup already does rather than a laxer one
    just because an admin is the one submitting the form."""

    full_name: str
    phone: str
    password: str
    ward: str
    preferred_language: str
    email: str | None = None
    email_verification_token: str | None = None


class WorkerSummary(UserResponse):
    """A worker's profile plus a quick view of their current workload."""

    open_complaints: int
    resolved_complaints: int


class UpdateWorkerRequest(BaseModel):
    """Request body for a super admin editing a worker's profile. Every field optional -- only
    the fields actually sent are changed (a PATCH, not a full replace). Deliberately excludes
    `phone` -- it's the login identifier and changing it has uniqueness/re-login implications
    this endpoint doesn't take on; nothing in this app currently needs that.

    `ward_id`/`locality_id`: the structured counterpart of `ward`, picked from the same cascading
    State/City/Ward/Area lookups already used on Signup (see routes/locations.py) -- reused here
    so an admin assigns a worker's operational area from real, structured data instead of typing
    free text. `state_id`/`district_id` aren't accepted here: update_worker() derives the full
    parent chain itself from `ward_id` (see LocationResolver.location_chain_for_ward), so the
    intermediate picks the frontend cascades through never need to reach this endpoint. Optional
    and independent of `ward`: a caller can still send `ward` alone (the plain-text path keeps
    working unchanged), or `ward_id` alone (the display `ward` text is then derived from it), or
    both (an explicit `ward` always wins over the derived text).

    `email`: same OTP-proof requirement as CreateWorkerRequest.email -- but only when actually
    CHANGING to a new, different address; re-sending the worker's own already-verified current
    email back unchanged is always a no-op requiring no token (nothing to prove that isn't already
    proven), so a save that touches unrelated fields (ward, language, ...) can safely resend the
    unchanged email alongside them without forcing a fresh OTP round trip. An empty string ("")
    clears it back to no email; omitting the field entirely leaves it untouched, same PATCH
    semantics as every other field here."""

    full_name: str | None = None
    ward: str | None = None
    preferred_language: str | None = None
    ward_id: int | None = None
    locality_id: int | None = None
    email: str | None = None
    email_verification_token: str | None = None


class ResetWorkerPasswordRequest(BaseModel):
    """Request body for a super admin setting a new password for a worker who's lost access --
    there's no self-service "forgot password" flow anywhere in this app, so this is the only
    recovery path."""

    new_password: str


class SendWorkerEmailCodeRequest(BaseModel):
    """Request body for POST /admin/workers/email/send-code."""

    email: str


class VerifyWorkerEmailCodeRequest(BaseModel):
    """Request body for POST /admin/workers/email/verify-code."""

    email: str
    code: str


class VerifyWorkerEmailCodeResponse(BaseModel):
    """Response for POST /admin/workers/email/verify-code -- the proof token
    create_worker()/update_worker() need to actually attach `email` to a worker account."""

    email_verification_token: str


@router.post(
    "/workers/email/send-code",
    status_code=204,
    dependencies=[Depends(require_otp_rate_limit)],
)
def send_worker_email_code(
    body: SendWorkerEmailCodeRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role("admin")),
) -> None:
    """Send a 6-digit OTP to a candidate email address for a worker being added or edited --
    behind the same inline "Send code" button UX as Signup.tsx (see EmailVerifyField.tsx), reusing
    the exact same create_signup_email_otp/verify_signup_email_otp/consume_signup_email_verification
    functions citizen signup already uses (backend/services/auth_service.py), rather than a second
    copy of that logic: "prove this address before attaching it to an account" is the same
    requirement whether the account is a citizen signing themselves up or a worker a super admin
    is provisioning, so it's the same underlying mechanism -- only the route/auth wrapper differs
    (admin-authenticated here, since this is reached only from inside the admin-only Add/Edit
    Worker forms, vs. fully public+per-IP-rate-limited at actual signup)."""
    email = body.email.strip().lower()
    if not _EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    if db.query(User).filter(User.email == email).first() is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    code = create_signup_email_otp(db, email)
    # Unlike _EMAIL_PATTERN above (a stateless constant, safe to keep a separate copy of), this is
    # THE SAME stateful dev-only cache GET /auth/dev/otp-code already reads from -- populating a
    # second, separate dict here would make that endpoint unable to see codes this route issues,
    # so this reuses auth.py's actual cache rather than a duplicate.
    _dev_cache_otp(email, code)
    try:
        send_otp_email(email, code, _VERIFY_EMAIL_PURPOSE)
    except EmailServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    logger.info("Worker email verification code sent (admin_id=%s)", admin.id)


@router.post("/workers/email/verify-code", response_model=VerifyWorkerEmailCodeResponse)
def verify_worker_email_code(
    body: VerifyWorkerEmailCodeRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role("admin")),
) -> VerifyWorkerEmailCodeResponse:
    """Confirm the OTP from POST /admin/workers/email/send-code and issue the same kind of
    one-time proof token citizen signup's own POST /auth/signup/email/verify-code issues --
    create_worker()/update_worker() redeem it via the identical
    consume_signup_email_verification() call signup() itself uses."""
    email = body.email.strip().lower()
    proof_token = verify_signup_email_otp(db, email, body.code.strip())
    if proof_token is None:
        raise HTTPException(status_code=400, detail="Invalid or expired code.")
    return VerifyWorkerEmailCodeResponse(email_verification_token=proof_token)


@router.post("/workers", response_model=UserResponse)
def create_worker(
    body: CreateWorkerRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role("admin")),
) -> UserResponse:
    """Create a new worker account for a given ward. Super admin only."""
    full_name = body.full_name.strip()
    phone = body.phone.strip()
    ward = body.ward.strip()

    if not full_name:
        raise HTTPException(status_code=400, detail="Full name is required.")
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number is required.")
    if not ward:
        raise HTTPException(status_code=400, detail="Ward is required.")
    if len(body.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if body.preferred_language not in settings.SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {body.preferred_language}")

    if db.query(User).filter(User.phone == phone).first() is not None:
        raise HTTPException(status_code=409, detail="An account with this phone number already exists.")

    email = body.email.strip().lower() if body.email else None
    if email:
        if not _EMAIL_PATTERN.match(email):
            raise HTTPException(status_code=400, detail="Enter a valid email address.")
        if db.query(User).filter(User.email == email).first() is not None:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        if not body.email_verification_token or not consume_signup_email_verification(
            db, email, body.email_verification_token.strip()
        ):
            raise HTTPException(
                status_code=400, detail="Email is not verified. Please verify the email address first."
            )

    worker = User(
        full_name=full_name,
        phone=phone,
        password_hash=hash_password(body.password),
        role="worker",
        preferred_language=body.preferred_language,
        ward=ward,
        email=email,
        email_verified=email is not None,
    )
    db.add(worker)
    db.commit()
    db.refresh(worker)
    logger.info("Worker account created by admin (admin_id=%s, worker_id=%s, ward=%s)", admin.id, worker.id, ward)
    return UserResponse.model_validate(worker)


@router.get("/workers", response_model=list[WorkerSummary])
def list_workers(
    db: Session = Depends(get_db),
    admin: User = Depends(require_role("admin")),
) -> list[WorkerSummary]:
    """List every worker account with their current open/resolved complaint counts."""
    workers = db.query(User).filter(User.role == "worker").order_by(User.created_at.desc()).all()

    summaries = []
    for worker in workers:
        # Counted by actual assignment now, not ward text match — accurate even when a ward has
        # more than one worker (see assignment_service.py), unlike the old ward-only count.
        open_count = (
            db.query(Complaint)
            .filter(Complaint.assigned_worker_id == worker.id, Complaint.status.in_(_OPEN_COMPLAINT_STATUSES))
            .count()
        )
        resolved_count = (
            db.query(Complaint)
            .filter(Complaint.assigned_worker_id == worker.id, Complaint.status == "resolved")
            .count()
        )
        summaries.append(
            WorkerSummary(
                **UserResponse.model_validate(worker).model_dump(),
                open_complaints=open_count,
                resolved_complaints=resolved_count,
            )
        )
    return summaries


@router.patch("/workers/{worker_id}", response_model=UserResponse)
def update_worker(
    worker_id: int,
    body: UpdateWorkerRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role("admin")),
) -> UserResponse:
    """Edit a worker's profile (name/ward/preferred language/email). Super admin only. Only
    fields actually present in the request body are changed."""
    worker = db.query(User).filter(User.id == worker_id, User.role == "worker").first()
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found.")

    if body.full_name is not None:
        full_name = body.full_name.strip()
        if not full_name:
            raise HTTPException(status_code=400, detail="Full name cannot be empty.")
        worker.full_name = full_name

    if body.ward_id is not None:
        ward_row = db.query(Ward).filter(Ward.id == body.ward_id).first()
        if ward_row is None:
            raise HTTPException(status_code=400, detail="Ward not found.")
        chain = _location_resolver.location_chain_for_ward(db, ward_row)
        if body.locality_id is not None:
            locality_row = (
                db.query(Locality)
                .filter(Locality.id == body.locality_id, Locality.ward_id == ward_row.id)
                .first()
            )
            if locality_row is None:
                raise HTTPException(status_code=400, detail="Locality does not belong to the selected ward.")
            chain["locality_id"] = locality_row.id
        for field, value in chain.items():
            setattr(worker, field, value)
        if body.ward is None:
            # No explicit free-text override -- derive the display string the same way the
            # signup picker does (see HomeLocationPicker.tsx), so `ward` (still what assignment's
            # text-match fallback keys on -- see assignment_service.py) stays in sync with the
            # structured pick instead of going stale.
            locality_row = (
                db.query(Locality).filter(Locality.id == chain["locality_id"]).first()
                if chain["locality_id"] is not None
                else None
            )
            ulb_row = db.query(ULB).filter(ULB.id == ward_row.ulb_id).first()
            worker.ward = (
                f"{ward_row.name} — {locality_row.name}, {ulb_row.name}"
                if locality_row is not None and ulb_row is not None
                else ward_row.name
            )
    elif body.locality_id is not None:
        raise HTTPException(status_code=400, detail="locality_id requires ward_id.")

    if body.ward is not None:
        ward = body.ward.strip()
        if not ward:
            raise HTTPException(status_code=400, detail="Ward cannot be empty.")
        worker.ward = ward

    if body.preferred_language is not None:
        if body.preferred_language not in settings.SUPPORTED_LANGUAGES:
            raise HTTPException(status_code=400, detail=f"Unsupported language: {body.preferred_language}")
        worker.preferred_language = body.preferred_language

    if body.email is not None:
        email = body.email.strip().lower()
        if not email:
            worker.email = None
            worker.email_verified = False
        elif worker.email is not None and email == worker.email.lower():
            pass  # unchanged from the current, already-(un)verified address -- nothing to prove
        else:
            if not _EMAIL_PATTERN.match(email):
                raise HTTPException(status_code=400, detail="Enter a valid email address.")
            existing = db.query(User).filter(User.email == email, User.id != worker.id).first()
            if existing is not None:
                raise HTTPException(status_code=409, detail="An account with this email already exists.")
            if not body.email_verification_token or not consume_signup_email_verification(
                db, email, body.email_verification_token.strip()
            ):
                raise HTTPException(
                    status_code=400, detail="Email is not verified. Please verify the email address first."
                )
            worker.email = email
            worker.email_verified = True

    db.commit()
    db.refresh(worker)
    logger.info("Worker profile updated by admin (admin_id=%s, worker_id=%s)", admin.id, worker_id)
    return UserResponse.model_validate(worker)


@router.post("/workers/{worker_id}/reset-password", response_model=UserResponse)
def reset_worker_password(
    worker_id: int,
    body: ResetWorkerPasswordRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role("admin")),
) -> UserResponse:
    """Set a new password for a worker who's lost access to their account. Super admin only."""
    worker = db.query(User).filter(User.id == worker_id, User.role == "worker").first()
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found.")

    if len(body.new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")

    worker.password_hash = hash_password(body.new_password)
    db.commit()
    db.refresh(worker)
    logger.info("Worker password reset by admin (admin_id=%s, worker_id=%s)", admin.id, worker_id)
    return UserResponse.model_validate(worker)


# ------------------------------------------------------------------
# Worker/complaint management -- delete a worker, delete a complaint, or manually assign a
# complaint to a specific worker (an override for when assignment_service.py's automatic
# ward-matching left a complaint "pending" with nobody eligible, or an admin simply wants to
# reassign one). Every route here is super-admin-only, matching this file's own module
# docstring: provisioning/removing staff, and overriding assignment, is never available to a
# citizen or worker.
# ------------------------------------------------------------------


class AssignComplaintRequest(BaseModel):
    """Request body for a super admin manually assigning a complaint to a specific worker."""

    worker_id: int


class ComplaintAdminSummary(BaseModel):
    """Minimal confirmation of a complaint's state after an admin action -- not the full
    ComplaintResponse shape from routes/complaints.py (translation/display-text concerns that
    route owns), just enough for the Admin dashboard to update its own table row in place."""

    id: int
    status: str
    assigned_worker_id: int | None
    assigned_worker_name: str | None


class DeleteWorkerResponse(BaseModel):
    deleted_worker_id: int
    # Any complaint that was "assigned"/"accepted" to this worker is reset to "pending" (not
    # deleted) rather than left pointing at a worker id that no longer exists -- see
    # delete_worker()'s docstring. This count lets the admin dashboard tell the admin how many
    # complaints now need a new assignment.
    reset_to_pending: int


class DeleteComplaintResponse(BaseModel):
    deleted_complaint_id: int


@router.delete("/workers/{worker_id}", response_model=DeleteWorkerResponse)
def delete_worker(
    worker_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role("admin")),
) -> DeleteWorkerResponse:
    """Delete a worker account. Super admin only.

    Any complaint currently assigned to this worker (status "assigned", "accepted", or
    "in_progress" -- see `_OPEN_COMPLAINT_STATUSES`) is reset to "pending" with no assigned
    worker -- never silently deleted along with the worker, and never left with a dangling
    `assigned_worker_id`. The admin can reassign it via POST /admin/complaints/{id}/assign, or
    leave it for a new/existing worker in that ward. Each reset is also logged to the complaint's
    status-history timeline (see complaint_workflow_repository.record_status_change()), the same
    way every other status change in this app already is.
    """
    worker = db.query(User).filter(User.id == worker_id, User.role == "worker").first()
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found.")

    affected = (
        db.query(Complaint)
        .filter(Complaint.assigned_worker_id == worker_id, Complaint.status.in_(_OPEN_COMPLAINT_STATUSES))
        .all()
    )
    previous_statuses = {complaint.id: complaint.status for complaint in affected}
    for complaint in affected:
        complaint.status = "pending"
        complaint.assigned_worker_id = None

    db.query(ComplaintRejection).filter(ComplaintRejection.worker_id == worker_id).delete(synchronize_session=False)

    db.delete(worker)
    db.commit()

    for complaint in affected:
        complaint_workflow_repository.record_status_change(
            db, complaint, from_status=previous_statuses[complaint.id], to_status="pending",
            actor_role="admin", actor_user_id=admin.id, note="Worker account deleted.",
        )
    logger.info(
        "Worker deleted by admin (admin_id=%s, worker_id=%s, complaints_reset_to_pending=%s)",
        admin.id, worker_id, len(affected),
    )
    return DeleteWorkerResponse(deleted_worker_id=worker_id, reset_to_pending=len(affected))


@router.delete("/complaints/{complaint_id}", response_model=DeleteComplaintResponse)
def delete_complaint(
    complaint_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role("admin")),
) -> DeleteComplaintResponse:
    """Permanently delete a complaint and every record that references it (rejections,
    translations, status history, worker-authored updates). Super admin only -- irreversible,
    unlike a status change."""
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    if complaint is None:
        raise HTTPException(status_code=404, detail="Complaint not found.")

    db.query(ComplaintRejection).filter(ComplaintRejection.complaint_id == complaint_id).delete(synchronize_session=False)
    db.query(ComplaintTranslation).filter(ComplaintTranslation.complaint_id == complaint_id).delete(synchronize_session=False)
    db.query(ComplaintStatusHistory).filter(ComplaintStatusHistory.complaint_id == complaint_id).delete(synchronize_session=False)
    db.query(ComplaintUpdate).filter(ComplaintUpdate.complaint_id == complaint_id).delete(synchronize_session=False)
    db.delete(complaint)
    db.commit()
    logger.info("Complaint deleted by admin (admin_id=%s, complaint_id=%s)", admin.id, complaint_id)
    return DeleteComplaintResponse(deleted_complaint_id=complaint_id)


@router.post("/complaints/{complaint_id}/assign", response_model=ComplaintAdminSummary)
def assign_complaint(
    complaint_id: int,
    body: AssignComplaintRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role("admin")),
) -> ComplaintAdminSummary:
    """Manually assign a complaint to a specific worker, overriding the automatic ward-matching
    assignment (see assignment_service.py) -- for a complaint stuck "pending" because no worker
    matched its ward automatically, or to move it to a different worker. Super admin only.
    Logged to the complaint's status-history timeline like every other status change.
    """
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    if complaint is None:
        raise HTTPException(status_code=404, detail="Complaint not found.")

    worker = db.query(User).filter(User.id == body.worker_id, User.role == "worker").first()
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found.")

    previous_status = complaint.status
    complaint.assigned_worker_id = worker.id
    complaint.status = "assigned"
    db.commit()
    complaint_workflow_repository.record_status_change(
        db, complaint, from_status=previous_status, to_status="assigned",
        actor_role="admin", actor_user_id=admin.id, note=f"Manually assigned to {worker.full_name} by admin.",
    )
    logger.info(
        "Complaint manually assigned by admin (admin_id=%s, complaint_id=%s, worker_id=%s)",
        admin.id, complaint_id, worker.id,
    )
    return ComplaintAdminSummary(
        id=complaint.id, status=complaint.status, assigned_worker_id=complaint.assigned_worker_id, assigned_worker_name=worker.full_name
    )


# ------------------------------------------------------------------
# AI Monitoring -- application-level observability for the Ask Sarthi LangGraph pipeline (see
# backend/services/observability/tracing.py and docs/ask_janmitra_langsmith_observability.md).
#
# Deliberately NOT a LangSmith dashboard reimplementation: these two endpoints read only
# `AiRequestLog` (see backend/repositories/ai_request_log_repository.py), a local table populated
# by every `/ask-janmitra` call regardless of whether LangSmith tracing is configured -- so this
# section of the Admin dashboard keeps working identically whether LangSmith is fully wired up,
# misconfigured, or not set up at all. `trace_url` on each request is the only place LangSmith
# enters the picture, and even that is a locally-built string (see `tracing.get_trace_url()`) --
# no LangSmith API call happens on this read path.
# ------------------------------------------------------------------


class AiMonitoringSummary(BaseModel):
    """High-level AI Monitoring tiles for the Admin dashboard. See
    ai_request_log_repository.get_ai_monitoring_summary() for how each field is computed."""

    total_requests: int
    successful_requests: int
    failed_requests: int
    error_rate: float
    average_latency_ms: float
    rag_requests: int
    complaint_requests: int
    status_requests: int
    out_of_scope_requests: int
    clarification_requests: int


class AiRequestLogEntry(BaseModel):
    """One row of the Admin dashboard's recent-AI-requests table."""

    id: int
    request_id: str
    intent: str | None
    service_category: str | None
    routed_to: str
    success: bool
    error_type: str | None
    latency_ms: float
    created_at: datetime
    # None when LANGSMITH_TRACE_URL_TEMPLATE isn't configured, tracing was disabled for this
    # request, or the request predates this feature -- the frontend shows the raw request id
    # without a link in that case (see tracing.get_trace_url()'s docstring).
    trace_url: str | None


@router.get("/ai-monitoring", response_model=AiMonitoringSummary)
def ai_monitoring_summary(
    db: Session = Depends(get_db),
    admin: User = Depends(require_role("admin")),
) -> AiMonitoringSummary:
    """Aggregate Ask Sarthi request counts/rates for the Admin dashboard's AI Monitoring tiles."""
    return AiMonitoringSummary(**ai_request_log_repository.get_ai_monitoring_summary(db))


@router.get("/ai-monitoring/requests", response_model=list[AiRequestLogEntry])
def ai_monitoring_requests(
    limit: int = 50,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role("admin")),
) -> list[AiRequestLogEntry]:
    """Most recent Ask Sarthi requests, each with a "View Trace" link where configured."""
    rows = ai_request_log_repository.get_recent_ai_requests(db, limit=min(max(limit, 1), 200))
    return [
        AiRequestLogEntry(
            id=row.id,
            request_id=row.request_id,
            intent=row.intent,
            service_category=row.service_category,
            routed_to=row.routed_to,
            success=row.success,
            error_type=row.error_type,
            latency_ms=row.latency_ms,
            created_at=row.created_at,
            trace_url=tracing.get_trace_url(row.langsmith_trace_id),
        )
        for row in rows
    ]
