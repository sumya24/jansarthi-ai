"""Authentication endpoints: citizen sign-up, login, and account settings.

There is deliberately no role field anywhere in this file. Sign-up always
creates a "citizen" account — it is the only self-service entry point in the
system. Worker accounts are only ever created by a super admin (see
routes/admin.py); the first super admin account is seeded directly into the
database when the system is set up, not created through any API here.

Email verification is mandatory before an account exists at all, not an optional step added
later from Settings -- but it's decoupled from the rest of the signup form, behind its own
"Verify" button next to the email field (see Signup.tsx): POST /auth/signup/email/send-code and
POST /auth/signup/email/verify-code handle that round trip using only the email address, and
issue a one-time proof token on success (see models.SignupEmailVerification's own docstring).
POST /auth/signup itself is then a single call that creates the account directly, but only if it
can present that proof token -- a bare client-side "verified: true" claim is never trusted.
"""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.deps import (
    get_current_user,
    require_login_rate_limit,
    require_otp_rate_limit,
    require_signup_rate_limit,
)
from backend.models import District, Locality, State, ULB, User, Ward
from backend.services.auth_service import (
    consume_signup_email_verification,
    create_access_token,
    create_email_otp,
    create_refresh_token,
    create_signup_email_otp,
    hash_password,
    revoke_all_refresh_tokens,
    revoke_refresh_token,
    rotate_refresh_token,
    verify_email_otp,
    verify_password,
    verify_signup_email_otp,
)
from backend.services.email_service import EmailServiceError, send_otp_email

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# 8+ chars with at least one letter, one digit, and one special character -- the familiar
# production-app password bar (a step up from bare NIST length-only, which most citizens expect
# a "real" signup form to enforce). Only enforced at signup and change-password/reset-password
# (see _validate_password_strength below) -- never at login, so no pre-existing account created
# under an older, looser rule is ever locked out of its own login.
MIN_PASSWORD_LENGTH = 8
_PASSWORD_HAS_LETTER = re.compile(r"[A-Za-z]")
_PASSWORD_HAS_DIGIT = re.compile(r"\d")
_PASSWORD_HAS_SPECIAL = re.compile(r"[^A-Za-z0-9]")

# Standard Indian mobile number: 10 digits, first digit 6-9 -- matches every phone number already
# used across this codebase's tests/seed data (all 10 digits, all start with 9), and this is the
# real, deployed audience (see memory: Oracle Cloud India deployment). Previously `signup`/`login`
# only checked "non-empty" -- any string ("abc", "1") was accepted as a phone number, a real
# validation gap: it let a citizen register with something that could never receive an SMS/call,
# and let `phone` silently double as a free-text username instead of what it's actually used for
# (login identifier, `User.phone`, unique+indexed). Checked at signup only, matching how phone is
# otherwise immutable (no phone field in MeUpdateRequest) -- login itself must stay a plain
# "wrong credentials" check, not a format gate, so a citizen who signed up before this validation
# existed is never locked out of their own account.
_PHONE_PATTERN = re.compile(r"^[6-9]\d{9}$")

# Deliberately simple (not RFC 5322-complete) -- this is only ever used to (a) distinguish an
# email-shaped identifier from a phone-shaped one at login, and (b) reject an obviously-malformed
# address before spending an OTP send on it. The real proof of ownership is the OTP round-trip
# itself, not this regex.
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_VERIFY_EMAIL_PURPOSE = "verify_email"
_RESET_PASSWORD_PURPOSE = "reset_password"

# Dev/test-only OTP cache -- lets Playwright e2e specs (which hit this real running backend, not
# an in-process mock) complete an OTP flow without reading a real inbox, the same problem
# tests/test_email_otp.py and friends solve differently (monkeypatching send_otp_email in-process,
# not possible from a separate Node/Playwright process). Only ever populated/served when
# settings.ENVIRONMENT != "production" (see _dev_get_otp_code below) -- a real deployment can
# never read another citizen's OTP through this. Keyed by email, not persisted anywhere; lost on
# every backend restart, which is fine since it exists purely to bridge one local test run.
_dev_otp_cache: dict[str, str] = {}


def _dev_cache_otp(email: str, code: str) -> None:
    if settings.ENVIRONMENT != "production":
        _dev_otp_cache[email] = code


class SignupRequest(BaseModel):
    """Request body for citizen self-registration -- creates the account directly, in one call.

    Requires email_verification_token, proving the citizen already completed the
    POST /auth/signup/email/send-code -> POST /auth/signup/email/verify-code round trip for
    `email` (see this file's module docstring, and models.SignupEmailVerification). The email
    itself is never trusted or written to User.email/User.email_verified unless that token checks
    out server-side.
    """

    full_name: str
    phone: str
    email: str
    email_verification_token: str
    password: str
    preferred_language: str
    # Mandatory, one-time-at-signup -- not editable later (no ward field in MeUpdateRequest
    # below), so every citizen account is guaranteed to have one. Free text matching User.ward,
    # same field workers already have; picked from the same GET /complaints/wards list the
    # complaint-report wizard already uses, so "My Area" (routes/complaints.py's
    # /area-summary) always has something real to key off.
    ward: str
    # Optional, additive: the new cascading State/City/Ward/Area picker (see routes/locations.py)
    # feeds these into User.home_*_id -- the structured columns models.py already reserved for
    # exactly this (see User.home_state_id's own docstring), previously always null. Deliberately
    # NOT required and NOT used to derive/override `ward` above -- the existing free-text field
    # keeps working exactly as it does today (worker matching, "My Area", the AI's location
    # fallback) for every citizen, whether or not they complete this optional picker. Only the
    # deepest one the citizen actually reached needs to be sent; the signup handler derives the
    # rest of the parent chain from it server-side (see _resolve_home_location below), so the
    # frontend never has to track/send the whole chain itself.
    home_state_id: int | None = None
    home_district_id: int | None = None
    home_ward_id: int | None = None
    home_locality_id: int | None = None


class SendSignupEmailCodeRequest(BaseModel):
    """Request body for POST /auth/signup/email/send-code."""

    email: str


class VerifySignupEmailCodeRequest(BaseModel):
    """Request body for POST /auth/signup/email/verify-code."""

    email: str
    code: str


class VerifySignupEmailCodeResponse(BaseModel):
    """Response for POST /auth/signup/email/verify-code -- the proof token to carry through the
    rest of the signup form and present back at POST /auth/signup."""

    email_verification_token: str


class LoginRequest(BaseModel):
    """Request body for logging in -- with either a phone number or a verified email."""

    identifier: str
    password: str


class RefreshRequest(BaseModel):
    """Request body for POST /auth/refresh."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """Request body for POST /auth/logout. No auth dependency on that route -- the refresh
    token itself is already sufficient proof of ownership to revoke just itself, and requiring a
    still-valid access token too would make logout needlessly fail for the realistic case of a
    citizen returning to a stale tab whose access token already expired."""

    refresh_token: str


class ChangePasswordRequest(BaseModel):
    """Request body for POST /auth/change-password."""

    current_password: str
    new_password: str


class SendEmailVerificationRequest(BaseModel):
    """Request body for POST /auth/email/send-verification."""

    email: str


class VerifyEmailRequest(BaseModel):
    """Request body for POST /auth/email/verify."""

    code: str


class ForgotPasswordRequest(BaseModel):
    """Request body for POST /auth/forgot-password."""

    email: str


class ResetPasswordRequest(BaseModel):
    """Request body for POST /auth/reset-password."""

    email: str
    code: str
    new_password: str


class MeUpdateRequest(BaseModel):
    """Request body for updating the current user's own profile.

    ward/state_id/district_id/ward_id/locality_id: a citizen's residence -- previously fixed
    forever at signup (see SignupRequest.ward's own docstring); now editable, since citizens
    genuinely move. `ward` is the free-text label (same "{ward} — {locality}, {city}" format
    Signup composes -- see HomeLocationPicker.tsx's composeWard()), backing "My Area" and any
    free-text worker matching. The structured `..._id` fields are the same cascading state/city/
    ward/area picker signup uses (see routes/locations.py); only the deepest one reached needs
    to be sent, the rest of the parent chain is derived server-side the same way signup does
    (see _resolve_location_chain). assignment_service.py already prefers these structured ids
    over the free-text `ward` match whenever they're set, so updating them here immediately
    changes which worker team this citizen's FUTURE complaints route to -- already-filed
    complaints keep whatever ward/location they were actually filed under, untouched. Sent
    together as one field group (never partially -- see update_me): a half-updated location
    (e.g. new ward_id but stale free-text ward) would silently desync "My Area" from the
    structured routing it's supposed to mirror.
    """

    full_name: str | None = None
    preferred_language: str | None = None
    ward: str | None = None
    state_id: int | None = None
    district_id: int | None = None
    ward_id: int | None = None
    locality_id: int | None = None


class UserResponse(BaseModel):
    """Public-facing representation of a user account (never includes the password hash)."""

    id: int
    full_name: str
    phone: str
    email: str | None
    email_verified: bool
    role: str
    preferred_language: str
    ward: str | None
    # The structured counterpart of `ward` -- exposed so a citizen's current residence can be
    # pre-filled back into the cascading state/city/ward/area picker when editing it in Settings
    # (see MeUpdateRequest's own docstring), and so a worker's operational area can likewise be
    # pre-filled when a super admin edits it (see routes/admin.py's update_worker()). Null until
    # that picker has actually been used for this user.
    state_id: int | None = None
    district_id: int | None = None
    ward_id: int | None = None
    locality_id: int | None = None

    model_config = ConfigDict(from_attributes=True)


class AuthResponse(BaseModel):
    """Response returned by sign-up, login, and refresh."""

    access_token: str
    refresh_token: str
    user: UserResponse


def _validate_language(language: str) -> None:
    if language not in settings.SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {language}")


def _validate_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if not _PASSWORD_HAS_LETTER.search(password) or not _PASSWORD_HAS_DIGIT.search(password):
        raise HTTPException(status_code=400, detail="Password must include at least one letter and one number.")
    if not _PASSWORD_HAS_SPECIAL.search(password):
        raise HTTPException(status_code=400, detail="Password must include at least one special character.")


def _resolve_location_chain(
    db: Session,
    *,
    state_id: int | None,
    district_id: int | None,
    ward_id: int | None,
    locality_id: int | None,
) -> dict[str, int | None]:
    """Derives the full state -> locality chain from whichever ID a cascading state/city/ward/
    area picker (see routes/locations.py) actually reached -- only the deepest selection needs
    to be passed in; every ancestor is looked up from it here, authoritatively, rather than
    trusted from separate caller-supplied IDs that could in principle disagree with each other.
    Returns an all-None dict when nothing was provided (the common case today, since only 6 of
    36 states have real city/ward/locality data). Raises 400 if a given ID doesn't exist -- the
    same honest-validation standard as the rest of this handler, never silently ignored.

    Bare `state_id`/`district_id`/`ward_id`/`locality_id` keys -- callers needing the `home_`-
    prefixed columns (signup, see _resolve_home_location below) or the plain operational-area
    columns (update_me) re-key the result themselves rather than this function assuming either
    target."""
    ward: Ward | None = None
    locality: Locality | None = None
    if locality_id is not None:
        locality = db.query(Locality).filter(Locality.id == locality_id).first()
        if locality is None:
            raise HTTPException(status_code=400, detail="Selected area not found.")
        ward = db.query(Ward).filter(Ward.id == locality.ward_id).first()
    elif ward_id is not None:
        ward = db.query(Ward).filter(Ward.id == ward_id).first()
        if ward is None:
            raise HTTPException(status_code=400, detail="Selected ward not found.")

    ulb: ULB | None = None
    district: District | None = None
    if ward is not None:
        ulb = db.query(ULB).filter(ULB.id == ward.ulb_id).first()
        district = db.query(District).filter(District.id == ulb.district_id).first() if ulb else None
    elif district_id is not None:
        district = db.query(District).filter(District.id == district_id).first()
        if district is None:
            raise HTTPException(status_code=400, detail="Selected city not found.")

    resolved_state_id: int | None = None
    if district is not None:
        resolved_state_id = district.state_id
    elif state_id is not None:
        if db.query(State).filter(State.id == state_id).first() is None:
            raise HTTPException(status_code=400, detail="Selected state not found.")
        resolved_state_id = state_id

    return {
        "state_id": resolved_state_id,
        "district_id": district.id if district else None,
        "ulb_id": ulb.id if ulb else None,
        "ward_id": ward.id if ward else None,
        "locality_id": locality.id if locality else None,
    }


def _resolve_home_location(db: Session, body: SignupRequest) -> dict[str, int | None]:
    """Thin re-keying wrapper around _resolve_location_chain for signup's home_* columns --
    see that function for the actual derivation logic."""
    chain = _resolve_location_chain(
        db,
        state_id=body.home_state_id,
        district_id=body.home_district_id,
        ward_id=body.home_ward_id,
        locality_id=body.home_locality_id,
    )
    return {f"home_{key}": value for key, value in chain.items()}


@router.get("/_dev/otp-code", include_in_schema=False)
def dev_get_otp_code(email: str) -> dict[str, str]:
    """Dev/test-only: returns the plaintext code most recently cached for this email by
    _dev_cache_otp (see that function and _dev_otp_cache's own docstring for why this exists --
    letting Playwright e2e specs complete a real OTP round trip without reading a real inbox).
    404s outright in production, and 404s here if nothing's been cached for this email yet."""
    if settings.ENVIRONMENT == "production":
        raise HTTPException(status_code=404)
    code = _dev_otp_cache.get(email.strip().lower())
    if code is None:
        raise HTTPException(status_code=404, detail="No OTP cached for this email.")
    return {"code": code}


@router.post(
    "/signup/email/send-code",
    status_code=204,
    dependencies=[Depends(require_signup_rate_limit)],
)
def signup_send_email_code(body: SendSignupEmailCodeRequest, db: Session = Depends(get_db)) -> None:
    """Send a 6-digit OTP to a candidate email address, behind Signup.tsx's inline "Verify"
    button -- decoupled from the rest of the signup form (see this file's module docstring), so
    this only ever needs the email address itself, before name/phone/password/ward are
    necessarily filled in.

    Rate-limited per client IP via require_signup_rate_limit (SIGNUP_RATE_LIMIT, per-hour) --
    NOT require_otp_rate_limit (OTP_RATE_LIMIT, 3/10min): that limiter is sized for an already-
    authenticated citizen's own settings/forgot-password flows, one account at a time, and would
    badly throttle a legitimate shared-IP burst of DIFFERENT people signing up (a school/office
    network, or a NAT'd mobile carrier -- see config.py's own comment on SIGNUP_RATE_LIMIT's
    sizing). This is the real start of a signup attempt, so it gets that same generous, per-hour
    budget instead -- also stops someone spamming a victim's inbox or burning the transactional-
    email provider's daily quota. POST /auth/signup/email/verify-code and POST /auth/signup
    itself are deliberately NOT separately rate-limited: verify-code relies on
    OTP_MAX_ATTEMPTS/attempts (see verify_signup_email_otp) the same way POST /auth/email/verify
    already does, and by the time POST /auth/signup is reached, this rate limit has already gated
    how many distinct emails could get that far in the first place.
    """
    email = body.email.strip().lower()
    if not _EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")

    if db.query(User).filter(User.email == email, User.email_verified.is_(True)).first() is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    code = create_signup_email_otp(db, email)
    _dev_cache_otp(email, code)
    try:
        send_otp_email(email, code, _VERIFY_EMAIL_PURPOSE)
    except EmailServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    logger.info("Signup email verification code sent (email=%s)", email)


@router.post("/signup/email/verify-code", response_model=VerifySignupEmailCodeResponse)
def signup_verify_email_code(
    body: VerifySignupEmailCodeRequest, db: Session = Depends(get_db)
) -> VerifySignupEmailCodeResponse:
    """Confirm the OTP from POST /auth/signup/email/send-code and, on success, issue the one-time
    proof token POST /auth/signup needs to actually create the account."""
    email = body.email.strip().lower()
    proof_token = verify_signup_email_otp(db, email, body.code.strip())
    if proof_token is None:
        raise HTTPException(status_code=400, detail="Invalid or expired code.")
    return VerifySignupEmailCodeResponse(email_verification_token=proof_token)


@router.post("/signup", response_model=AuthResponse)
def signup(body: SignupRequest, db: Session = Depends(get_db)) -> AuthResponse:
    """Create a citizen account, in one call -- but only if body.email_verification_token proves
    (see consume_signup_email_verification) that POST /auth/signup/email/send-code and
    POST /auth/signup/email/verify-code were already completed for body.email.

    Not separately rate-limited -- see POST /auth/signup/email/send-code's own docstring for why
    that's where require_signup_rate_limit actually lives now (the real start of a signup
    attempt); by the time this call can succeed, that limiter has already gated how many distinct
    emails could get this far.
    """
    full_name = body.full_name.strip()
    phone = body.phone.strip()
    email = body.email.strip().lower()
    ward = body.ward.strip()

    if not full_name:
        raise HTTPException(status_code=400, detail="Full name is required.")
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number is required.")
    if not _PHONE_PATTERN.match(phone):
        raise HTTPException(status_code=400, detail="Enter a valid 10-digit mobile number.")
    if not _EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    if not ward:
        raise HTTPException(status_code=400, detail="Area / ward is required.")
    _validate_password_strength(body.password)
    _validate_language(body.preferred_language)

    # Pre-check only -- the same real TOCTOU gap the token-consumption check below closes for
    # email still applies to phone (two concurrent signups for the same number); the database's
    # own unique constraint at the INSERT below is the actual source of truth, this just turns a
    # violation into the same honest 409 for the overwhelmingly common case (an existing account,
    # not a race). Checked before consuming the email verification token so an already-taken
    # phone number fails fast, without spending the citizen's one-time proof token on a signup
    # that was never going to succeed anyway.
    if db.query(User).filter(User.phone == phone).first() is not None:
        raise HTTPException(status_code=409, detail="An account with this phone number already exists.")

    if not consume_signup_email_verification(db, email, body.email_verification_token.strip()):
        raise HTTPException(
            status_code=400, detail="Email is not verified. Please verify your email address first."
        )

    home_location = _resolve_home_location(db, body)
    user = User(
        full_name=full_name,
        phone=phone,
        password_hash=hash_password(body.password),
        role="citizen",
        preferred_language=body.preferred_language,
        ward=ward,
        email=email,
        email_verified=True,
        **home_location,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account with this phone number or email already exists.")
    db.refresh(user)
    logger.info("New citizen account created (user_id=%s)", user.id)

    access_token = create_access_token(user)
    refresh_token = create_refresh_token(user, db)
    return AuthResponse(access_token=access_token, refresh_token=refresh_token, user=UserResponse.model_validate(user))


@router.post("/login", response_model=AuthResponse, dependencies=[Depends(require_login_rate_limit)])
def login(body: LoginRequest, db: Session = Depends(get_db)) -> AuthResponse:
    """Log in with a phone number OR a verified email, plus password, for any role.

    Rate-limited per client IP (see backend/deps.py's require_login_rate_limit) -- counts every
    attempt, successful or not, before any credential check runs.
    """
    identifier = body.identifier.strip()
    if _PHONE_PATTERN.match(identifier):
        user = db.query(User).filter(User.phone == identifier).first()
    else:
        # Only ever matches a VERIFIED email -- an unverified pending address is never written to
        # User.email in the first place (see EmailOtp/verify_email_otp), so this can't be used to
        # log in as an account whose email someone merely typed in without proving ownership.
        user = db.query(User).filter(User.email == identifier, User.email_verified.is_(True)).first()
    if user is None or not verify_password(body.password, user.password_hash):
        # Deliberately identical error for "no such account" and "wrong password" —
        # never reveal which one it was.
        raise HTTPException(status_code=401, detail="Incorrect phone number/email or password.")

    logger.info("User logged in (user_id=%s, role=%s)", user.id, user.role)
    access_token = create_access_token(user)
    refresh_token = create_refresh_token(user, db)
    return AuthResponse(access_token=access_token, refresh_token=refresh_token, user=UserResponse.model_validate(user))


@router.post("/refresh", response_model=AuthResponse)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)) -> AuthResponse:
    """Redeem a refresh token for a new short-lived access token, rotating the refresh token in
    the process (see services/auth_service.py's rotate_refresh_token for the rotation/reuse-
    detection design). Deliberately not rate-limited the way /login is -- a legitimate client
    calls this automatically, silently, potentially several times per session (see the frontend's
    401-triggered silent-refresh), which is a fundamentally different traffic shape from a
    human/script guessing credentials.
    """
    result = rotate_refresh_token(db, body.refresh_token)
    if result is None:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token. Please log in again.")
    user, access_token, refresh_token = result
    return AuthResponse(access_token=access_token, refresh_token=refresh_token, user=UserResponse.model_validate(user))


@router.post("/logout", status_code=204)
def logout(body: LogoutRequest, db: Session = Depends(get_db)) -> None:
    """Real, server-side logout -- revokes the refresh token so it can never be redeemed again,
    unlike the old client-side-only "clear localStorage" logout (under which a captured token
    stayed valid until its natural expiry regardless). See LogoutRequest's own docstring for why
    this route has no auth dependency of its own."""
    revoke_refresh_token(db, body.refresh_token)


@router.post("/change-password", status_code=204)
def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Self-service password change -- previously there was no way for a citizen to change their
    own password at all (only an admin-mediated reset existed, for worker accounts). Revokes
    every one of the account's other active refresh tokens on success, the same "assume the old
    credential may be compromised" posture a password change implies -- every other
    logged-in device/tab gets signed out and must log in again with the new password.
    """
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")
    _validate_password_strength(body.new_password)

    current_user.password_hash = hash_password(body.new_password)
    db.commit()
    revoke_all_refresh_tokens(db, current_user.id)
    logger.info("Password changed (user_id=%s) -- all other sessions revoked.", current_user.id)


@router.post(
    "/email/send-verification",
    status_code=204,
    dependencies=[Depends(require_otp_rate_limit)],
)
def send_email_verification(
    body: SendEmailVerificationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Send a 6-digit OTP to a candidate email address, to prove the citizen owns it before it's
    attached to their account. The address is NOT written to User.email here -- only once
    POST /auth/email/verify confirms the code (see EmailOtp/verify_email_otp's own docstrings for
    why: an unverified/abandoned attempt must never block someone else from claiming it, or let
    anyone log in with an address nobody proved they own).
    """
    email = body.email.strip().lower()
    if not _EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")

    existing = db.query(User).filter(User.email == email, User.email_verified.is_(True)).first()
    if existing is not None and existing.id != current_user.id:
        raise HTTPException(status_code=409, detail="This email is already in use on another account.")

    code = create_email_otp(db, current_user.id, email, _VERIFY_EMAIL_PURPOSE)
    _dev_cache_otp(email, code)
    try:
        send_otp_email(email, code, _VERIFY_EMAIL_PURPOSE)
    except EmailServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    logger.info("Email verification code sent (user_id=%s)", current_user.id)


@router.post("/email/verify", response_model=UserResponse)
def verify_email(
    body: VerifyEmailRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserResponse:
    """Confirm the OTP sent by POST /auth/email/send-verification and, on success, attach its
    email to the account as verified."""
    otp_row = verify_email_otp(db, current_user.id, _VERIFY_EMAIL_PURPOSE, body.code.strip())
    if otp_row is None:
        raise HTTPException(status_code=400, detail="Invalid or expired code.")

    existing = db.query(User).filter(User.email == otp_row.email, User.email_verified.is_(True)).first()
    if existing is not None and existing.id != current_user.id:
        raise HTTPException(status_code=409, detail="This email is already in use on another account.")

    current_user.email = otp_row.email
    current_user.email_verified = True
    db.commit()
    db.refresh(current_user)
    logger.info("Email verified (user_id=%s)", current_user.id)
    return UserResponse.model_validate(current_user)


@router.post(
    "/forgot-password",
    status_code=204,
    dependencies=[Depends(require_otp_rate_limit)],
)
def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)) -> None:
    """Request a password-reset code. Explicitly tells the caller if the email isn't a
    registered, verified account -- a deliberate choice for this app (a citizen-facing civic
    service, not a high-value target where account enumeration is the primary threat model):
    clear, honest feedback ("this email isn't registered, go sign up") matters more here than
    the enumeration-resistance a bank or a mail provider needs. POST /auth/login still gives an
    identical error for "no such account" vs. "wrong password" -- that's a different, still-
    valid tradeoff (a login guess is trivially retried privately; this is a one-time lookup a
    citizen only does when genuinely locked out)."""
    email = body.email.strip().lower()
    user = db.query(User).filter(User.email == email, User.email_verified.is_(True)).first()
    if user is None:
        raise HTTPException(status_code=404, detail="This email isn't registered. Please sign up instead.")

    code = create_email_otp(db, user.id, email, _RESET_PASSWORD_PURPOSE)
    _dev_cache_otp(email, code)
    try:
        send_otp_email(email, code, _RESET_PASSWORD_PURPOSE)
    except EmailServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    logger.info("Password reset code requested (user_id=%s)", user.id)


@router.post("/reset-password", status_code=204)
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)) -> None:
    """Complete a password reset using the code from POST /auth/forgot-password. Revokes every
    refresh token for the account on success -- same "assume the old credential may be
    compromised" posture as POST /auth/change-password."""
    email = body.email.strip().lower()
    user = db.query(User).filter(User.email == email, User.email_verified.is_(True)).first()
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid or expired code.")

    otp_row = verify_email_otp(db, user.id, _RESET_PASSWORD_PURPOSE, body.code.strip())
    if otp_row is None:
        raise HTTPException(status_code=400, detail="Invalid or expired code.")

    _validate_password_strength(body.new_password)
    user.password_hash = hash_password(body.new_password)
    db.commit()
    revoke_all_refresh_tokens(db, user.id)
    logger.info("Password reset via email OTP (user_id=%s) -- all sessions revoked.", user.id)


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    """Return the current logged-in user's profile."""
    return UserResponse.model_validate(current_user)


@router.patch("/me", response_model=UserResponse)
def update_me(
    body: MeUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserResponse:
    """Update the current user's own display name, preferred language, and/or (citizens only)
    residence location -- see MeUpdateRequest's own docstring for why location is a single
    all-or-nothing group, not four independently-settable ids."""
    if body.preferred_language is not None:
        _validate_language(body.preferred_language)
        current_user.preferred_language = body.preferred_language
    if body.full_name is not None:
        full_name = body.full_name.strip()
        if not full_name:
            raise HTTPException(status_code=400, detail="Full name cannot be empty.")
        current_user.full_name = full_name

    # `ward` (the free-text label) is the group's signal that a location update was actually
    # requested -- workers/admins have no residence location to edit at all (see User.ward's own
    # docstring: ward means something entirely different for a worker), so this is silently a
    # no-op for them even if a client somehow sent these fields anyway.
    if body.ward is not None and current_user.role == "citizen":
        ward = body.ward.strip()
        if not ward:
            raise HTTPException(status_code=400, detail="Area / ward cannot be empty.")
        chain = _resolve_location_chain(
            db,
            state_id=body.state_id,
            district_id=body.district_id,
            ward_id=body.ward_id,
            locality_id=body.locality_id,
        )
        current_user.ward = ward
        current_user.state_id = chain["state_id"]
        current_user.district_id = chain["district_id"]
        current_user.ulb_id = chain["ulb_id"]
        current_user.ward_id = chain["ward_id"]
        current_user.locality_id = chain["locality_id"]

    db.commit()
    db.refresh(current_user)
    logger.info("Profile updated (user_id=%s)", current_user.id)
    return UserResponse.model_validate(current_user)
