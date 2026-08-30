"""ASGI middleware -- currently just the general rate-limit safety net.

See backend/services/rate_limiter.py (the limiter itself) and backend/deps.py's
require_login_rate_limit/require_ai_rate_limit (the two stricter, purpose-specific limits already
on POST /auth/login and POST /ask-sarthi*). This module is what gives every OTHER route
(complaint creation, uploads, worker/admin actions, etc.) baseline coverage too, without hand-
wiring a dependency into each one individually -- registered once in main.py, applies
automatically to every request.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from backend.config import settings
from backend.deps import (
    ACCESS_TOKEN_COOKIE,
    CSRF_HEADER_NAME,
    CSRF_TOKEN_COOKIE,
    REFRESH_TOKEN_COOKIE,
    client_ip,
    extract_access_token,
)
from backend.services import metrics as sentry_metrics
from backend.services.auth_service import InvalidTokenError, decode_access_token
from backend.services.rate_limiter import RateLimiter

_general_limiter = RateLimiter()

# GET /health must stay reachable for monitoring/deploy healthchecks regardless of load elsewhere
# -- the only exemption; every other route (including /auth/signup and every GET) is covered.
_EXEMPT_PATHS = {"/health"}


def _general_identifier(request: Request) -> str:
    """Authenticated user id when a valid token is present, client IP otherwise -- matches
    require_ai_rate_limit's "prefer real user identity" preference. Checks both auth sources
    extract_access_token supports (Authorization header, then the access_token cookie), but still
    decodes the token directly here (verifies signature + expiry, no DB lookup) rather than
    depending on get_current_user, to keep this middleware cheap on every single request. An
    invalid/missing/expired token is NOT rejected here -- that 401 is each route's own auth
    dependency's job; this middleware only needs *an* identifier to count against, never
    authenticates anything itself."""
    token = extract_access_token(request)
    if token:
        try:
            payload = decode_access_token(token)
            return f"user:{payload['sub']}"
        except InvalidTokenError:
            pass
    return f"ip:{client_ip(request)}"


class GeneralRateLimitMiddleware(BaseHTTPMiddleware):
    """A generous baseline limit (settings.GENERAL_RATE_LIMIT per
    settings.GENERAL_RATE_LIMIT_WINDOW_SECONDS) applied to every request except /health -- see
    module docstring. Registered BEFORE CORSMiddleware in main.py (so CORS ends up outermost,
    handling preflight and attaching CORS headers to every response including a 429 from here --
    getting this registration order backwards would make a cross-origin browser request unable to
    even read this middleware's 429 body). Also skips OPTIONS defensively regardless of ordering,
    since a preflight is the browser's own automatic traffic, never a real user action to count."""

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        identifier = _general_identifier(request)
        allowed, retry_after = _general_limiter.check(
            identifier, settings.GENERAL_RATE_LIMIT, settings.GENERAL_RATE_LIMIT_WINDOW_SECONDS
        )
        if not allowed:
            sentry_metrics.count("rate_limit.exceeded", 1, attributes={"limiter": "general"})
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please try again later."},
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """A small, deliberately conservative set of standard response headers -- none of these have
    any per-route tuning risk, unlike a Content-Security-Policy (which needs real per-page allow-
    listing to avoid breaking the app) or Strict-Transport-Security (a reverse-proxy/Caddy-level
    concern in this deployment, not this FastAPI app's -- see docker-compose.prod.yml), so both
    are left out here rather than guessed at.

    - X-Content-Type-Options: nosniff -- stops a browser from ever re-interpreting a response's
      declared Content-Type (e.g. treating an uploaded "image" as executable script).
    - X-Frame-Options: DENY -- this app is never meant to be embedded in another site's <iframe>;
      blocks clickjacking-style attacks that rely on doing so.
    - Referrer-Policy: strict-origin-when-cross-origin -- a sensible modern default (send the
      full URL only same-origin, just the origin cross-origin, nothing on a downgrade to http),
      not this app's own previous behavior (browsers' own unset default is looser).
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
# Endpoints reachable with no prior session -- there is no csrf_token cookie yet to check against,
# and (login/signup aside) no authenticated session for a cross-site attacker to ride along with
# either way. Logout is included too: a CSRF-forced logout only ends the citizen's own session
# early, never touches another account or any data, which isn't worth the complexity of routing a
# CSRF header through the one call site (auth.tsx's logout()) that fires during the same teardown
# that's already clearing everything else.
_CSRF_EXEMPT_PATHS = {
    "/auth/login", "/auth/signup", "/auth/refresh", "/auth/logout",
    "/auth/signup/email/send-code", "/auth/signup/email/verify-code",
    "/auth/forgot-password", "/auth/reset-password",
}


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit-cookie CSRF protection for cookie-authenticated sessions -- the httpOnly
    access_token/refresh_token cookies (see deps.set_auth_cookies) ride along with ANY request to
    this origin a citizen's browser makes, including one a malicious page on another site tricks
    it into firing; a non-httpOnly csrf_token cookie plus a header the frontend must deliberately
    read and attach (X-CSRF-Token) closes that gap, since only same-origin JS can read the cookie
    to put it in the header in the first place.

    Only actually enforced when the request carries one of the two auth cookies -- a request
    authenticating instead via a plain Authorization: Bearer header (every existing backend test,
    or any future non-browser API client) can't be forged cross-site the way a cookie can, so this
    middleware is a complete no-op for that whole traffic shape. Also skipped for safe methods
    (never mutate anything) and the handful of pre-session endpoints in _CSRF_EXEMPT_PATHS.
    """

    async def dispatch(self, request: Request, call_next):
        has_cookie_session = bool(
            request.cookies.get(ACCESS_TOKEN_COOKIE) or request.cookies.get(REFRESH_TOKEN_COOKIE)
        )
        if (
            request.method in _CSRF_SAFE_METHODS
            or request.url.path in _CSRF_EXEMPT_PATHS
            or not has_cookie_session
        ):
            return await call_next(request)

        cookie_value = request.cookies.get(CSRF_TOKEN_COOKIE)
        header_value = request.headers.get(CSRF_HEADER_NAME)
        if not cookie_value or not header_value or cookie_value != header_value:
            return JSONResponse(status_code=403, content={"detail": "Invalid or missing CSRF token."})
        return await call_next(request)
