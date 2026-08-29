"""FastAPI dependencies for authenticating requests, enforcing roles, and rate limiting."""

import logging
import secrets

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.models import User
from backend.services import metrics as sentry_metrics
from backend.services.auth_service import InvalidTokenError, decode_access_token
from backend.services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# --- Cookie-based session auth -----------------------------------------------------------------
# A browser session authenticates via httpOnly cookies (never readable by JS -- closing off the
# XSS-reads-localStorage risk the old client-side token storage had); a non-browser caller (every
# existing backend test, or any future API client) can still use a plain Authorization: Bearer
# header instead, checked first below. Both are accepted everywhere on purpose -- this is a
# genuine "web clients use cookies, API clients use bearer tokens" split, not a half-migrated
# in-between state.
ACCESS_TOKEN_COOKIE = "access_token"
REFRESH_TOKEN_COOKIE = "refresh_token"
CSRF_TOKEN_COOKIE = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"


def extract_access_token(request: Request) -> str | None:
    """The access token for this request, from whichever of the two sources above carried it."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.removeprefix("Bearer ").strip()
    return request.cookies.get(ACCESS_TOKEN_COOKIE)


def extract_refresh_token(request: Request, body_token: str | None) -> str | None:
    """Same dual-source idea for the refresh token: an explicit value in the request body (tests/
    manual API use -- see RefreshRequest/LogoutRequest's own docstrings) takes precedence, falling
    back to the httpOnly refresh_token cookie a real browser sends automatically -- a browser
    session has no JS-level access to that cookie's value to put it in a body itself."""
    return body_token or request.cookies.get(REFRESH_TOKEN_COOKIE)


def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """Sets the three cookies a browser session actually runs on: the two httpOnly auth cookies,
    plus a separate, deliberately NON-httpOnly CSRF token the frontend reads back and echoes as a
    header on every mutating request (see middleware.CSRFMiddleware) -- the standard double-
    submit-cookie pattern, needed because cookies alone (unlike a header a script has to
    deliberately attach) ride along with a cross-site request whether the citizen intended it or
    not.

    `secure` only turns on in production (settings.ENVIRONMENT) -- a Secure cookie is dropped
    entirely by the browser over plain http, which local dev serves both the frontend (:5173) and
    backend (:8000) over; the real deployment is HTTPS-only (see deploy/Caddyfile), so this is
    never a gap in the environment where it would matter.

    refresh_token is scoped to path="/auth" (the only paths that ever need to read it) rather than
    the whole site, purely to limit which requests it rides along on -- it never needs to reach
    e.g. GET /complaints the way access_token does.
    """
    secure = settings.ENVIRONMENT == "production"
    response.set_cookie(
        ACCESS_TOKEN_COOKIE, access_token, max_age=settings.JWT_EXPIRE_MINUTES * 60,
        httponly=True, secure=secure, samesite="lax", path="/",
    )
    response.set_cookie(
        REFRESH_TOKEN_COOKIE, refresh_token, max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        httponly=True, secure=secure, samesite="lax", path="/auth",
    )
    response.set_cookie(
        CSRF_TOKEN_COOKIE, secrets.token_urlsafe(32), max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        httponly=False, secure=secure, samesite="lax", path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    """Logout -- deletes all three cookies set above. delete_cookie needs the same `path` each
    cookie was originally set with, or the browser treats it as an unrelated cookie and leaves the
    real one in place."""
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/")
    response.delete_cookie(REFRESH_TOKEN_COOKIE, path="/auth")
    response.delete_cookie(CSRF_TOKEN_COOKIE, path="/")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Resolve the authenticated user from this request's access token.

    Args:
        request: The incoming request, expected to carry either an "Authorization: Bearer <token>"
            header or an access_token cookie (see extract_access_token).
        db: Active database session.

    Returns:
        The authenticated User.

    Raises:
        HTTPException: 401 if no token is present, the token is invalid/expired, or the user it
            refers to no longer exists.
    """
    token = extract_access_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header.")

    try:
        payload = decode_access_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if user is None:
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    return user


def require_role(*allowed_roles: str):
    """Build a dependency that only allows users whose role is in allowed_roles.

    Args:
        *allowed_roles: Role names that may access the route, e.g. "admin".

    Returns:
        A FastAPI dependency raising 403 for any other authenticated role.
    """

    def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(status_code=403, detail="You do not have access to this action.")
        return current_user

    return _check


# --- Rate limiting -----------------------------------------------------------------------------
# See backend/services/rate_limiter.py's module docstring for the design (in-process sliding
# window, single-process deployment only) and docs/RATE_LIMITING.md for the full picture. Three
# separate limiter instances -- login, AI, and the general baseline (see backend/middleware.py)
# never share a counting namespace, so exhausting one never affects another. A request to POST
# /auth/login or POST /ask-janmitra* is checked against BOTH its own strict, purpose-specific
# limit here AND the general middleware's coarser one -- the general limiter is a safety net for
# every OTHER route, not a replacement for these two.

_login_limiter = RateLimiter()
_signup_limiter = RateLimiter()
_ai_limiter = RateLimiter()
_otp_limiter = RateLimiter()


def client_ip(request: Request) -> str:
    """The safest available pre-authentication identifier -- used for login rate limiting, and
    reused by backend/middleware.py's general limiter as its fallback for requests with no valid
    token.

    Only trusts `X-Forwarded-For` when `settings.TRUST_PROXY_HEADERS` is explicitly on (see that
    setting's own docstring in config.py) -- an arbitrary client can set this header to anything,
    so honoring it by default would let an attacker either bypass the limit entirely (claim a
    fresh IP on every request) or frame another real client (claim their IP). Off by default;
    this project's production Caddy sets it truthfully and the backend container publishes no
    port of its own for anyone to bypass Caddy and spoof it directly -- see docker-compose.prod.yml.
    Falls back to a fixed placeholder (never crashes) on the rare test/ASGI-transport case where
    `request.client` is None.
    """
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # The client's own original IP is the FIRST hop Caddy appends -- anything after that
            # was added by intermediate proxies Caddy itself saw, not attacker-controlled once
            # TRUST_PROXY_HEADERS is only enabled behind a topology where Caddy is the sole entry
            # point (see this setting's docstring).
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def require_login_rate_limit(request: Request) -> None:
    """Dependency for POST /auth/login -- throttles login attempts per client IP, independent of
    whether the credentials turn out to be valid (a brute-force script's failed attempts must
    count too, not just successful logins)."""
    allowed, retry_after = _login_limiter.check(
        client_ip(request), settings.LOGIN_RATE_LIMIT, settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS
    )
    if not allowed:
        sentry_metrics.count("rate_limit.exceeded", 1, attributes={"limiter": "login"})
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )


def require_signup_rate_limit(request: Request) -> None:
    """Dependency for POST /auth/signup -- keyed per client IP, its own dedicated (and much
    stricter, per-hour rather than per-minute) limit rather than relying on the loose
    GENERAL_RATE_LIMIT baseline every other route falls back to -- mass fake-account creation is a
    real, distinct abuse pattern from a normal signup, which happens once per real person."""
    allowed, retry_after = _signup_limiter.check(
        client_ip(request), settings.SIGNUP_RATE_LIMIT, settings.SIGNUP_RATE_LIMIT_WINDOW_SECONDS
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many signup attempts. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )


def require_otp_rate_limit(request: Request) -> None:
    """Dependency for both OTP-sending routes (POST /auth/email/send-verification and POST
    /auth/forgot-password) -- keyed per client IP (not per user, since forgot-password has no
    auth to key on) so the two share one counting namespace, stopping someone from working around
    the limit by alternating between them. Stops a spam script from flooding a victim's inbox or
    burning the 300/day Brevo free quota."""
    allowed, retry_after = _otp_limiter.check(
        client_ip(request), settings.OTP_RATE_LIMIT, settings.OTP_RATE_LIMIT_WINDOW_SECONDS
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many code requests. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )


def require_ai_rate_limit(current_user: User = Depends(get_current_user)) -> None:
    """Dependency for the POST /ask-janmitra* endpoints -- throttles expensive Sarvam/LLM/vision
    calls per authenticated user, shared across all three routes (text/image/voice) so a citizen
    can't dodge the limit by switching endpoints. Depends on `get_current_user` itself (rather
    than reading a raw token) so an invalid/expired token is rejected with its own real 401 before
    any rate-limit bookkeeping happens -- an unauthenticated request never consumes any specific
    user's quota. FastAPI resolves/caches `get_current_user` once per request, so routes that also
    declare it directly don't pay for a second DB lookup."""
    allowed, retry_after = _ai_limiter.check(
        f"user:{current_user.id}", settings.AI_RATE_LIMIT, settings.AI_RATE_LIMIT_WINDOW_SECONDS
    )
    if not allowed:
        sentry_metrics.count("rate_limit.exceeded", 1, attributes={"limiter": "ai"})
        raise HTTPException(
            status_code=429,
            detail="Too many requests to Ask Sarthi. Please wait a moment and try again.",
            headers={"Retry-After": str(retry_after)},
        )
