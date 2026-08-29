"""Shared pytest fixtures: an isolated in-memory database and a test client."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.config import settings
from backend.database import Base, get_db
from backend.deps import _ai_limiter, _login_limiter, _otp_limiter, _signup_limiter
from backend.main import app
from backend.middleware import _general_limiter


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """Every TestClient request reports the same fixed `request.client.host` ("testclient",
    confirmed directly against Starlette's TestClient) -- without this, the whole suite's many
    unrelated requests (every /auth/login call; every OTHER request too, now that
    GeneralRateLimitMiddleware covers every route) would all share ONE bucket per limiter and
    quickly exceed it, breaking tests that have nothing to do with rate limiting. Resetting all
    five limiters before every test keeps each test's rate-limit state isolated, same as
    db_session already isolates each test's database -- this doesn't weaken the rate limiters
    themselves (see tests/test_rate_limiting.py, which deliberately exceeds the real limits within
    a single test to verify the real 429 behavior), it only stops accumulation *across* unrelated
    tests."""
    _login_limiter.reset()
    _signup_limiter.reset()
    _ai_limiter.reset()
    _otp_limiter.reset()
    _general_limiter.reset()


@pytest.fixture(autouse=True)
def _force_email_dev_mode_off(monkeypatch):
    """EMAIL_DEV_MODE is a real .env setting (see backend/config.py), meant to be turned on for a
    developer's own local/E2E runs so signup/login don't depend on a real inbox or Gmail's daily
    quota -- but pytest loads that same .env, so without this, whatever a developer happens to
    have set locally would silently swap every test's real-send-path assertions for a no-op skip
    (discovered directly: turning EMAIL_DEV_MODE=true on for local E2E use broke 40+ otherwise-
    unrelated tests across test_signup_email_verification.py, test_complaint_lifecycle_emails.py,
    etc., all expecting send_otp_email/send_complaint_status_email to actually be called). Forcing
    it off here makes the suite's behavior deterministic regardless of the local .env; the handful
    of tests that specifically exercise EMAIL_DEV_MODE itself (test_email_otp.py) still work fine,
    since their own monkeypatch.setattr(settings, "EMAIL_DEV_MODE", True) simply overrides this
    for their own duration."""
    monkeypatch.setattr(settings, "EMAIL_DEV_MODE", False)


@pytest.fixture()
def db_session():
    """Provide a fresh in-memory SQLite database for each test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestingSessionLocal
    app.dependency_overrides.clear()


@pytest.fixture()
def client(db_session):
    """A FastAPI TestClient wired to the isolated in-memory database.

    Automatically echoes the csrf_token cookie (set by login/signup/refresh -- see
    backend/deps.py's set_auth_cookies) back as the X-CSRF-Token request header on every call,
    exactly what a real browser session's JS does (see frontend-react/src/lib/api.ts's
    getCsrfToken()). Needed because TestClient is itself an httpx.Client and keeps a real cookie
    jar across requests -- after one login, every later request in this same test carries the
    access_token/refresh_token cookies too, which is exactly what CSRFMiddleware treats as "a
    cookie-authenticated session" and starts requiring the CSRF header on. Without this hook, every
    test touching a mutating endpoint after login would fail with 403 "Invalid or missing CSRF
    token" -- not a bug in the middleware, just this client acting like the browser it's now
    standing in for.
    """
    test_client = TestClient(app)

    def _attach_csrf_header(request):
        csrf_token = test_client.cookies.get("csrf_token")
        if csrf_token:
            request.headers["X-CSRF-Token"] = csrf_token

    test_client.event_hooks["request"] = [_attach_csrf_header]
    return test_client


@pytest.fixture()
def make_citizen(client):
    """Factory fixture: sign up a citizen via the real three-call API (POST /auth/signup/email/
    send-code, POST /auth/signup/email/verify-code, then POST /auth/signup with the returned
    proof token) and return (token, user).

    Email verification is mandatory at signup, decoupled from the rest of the form behind its own
    "Verify" button on the frontend (see backend/routes/auth.py's module docstring) -- this
    fixture intercepts backend.routes.auth.send_otp_email purely to capture the plaintext code
    without a real network call, then completes the same round trip a real citizen would, so
    every one of this fixture's many existing callers keeps working unchanged against the same
    (token, user) contract as before.

    Uses unittest.mock.patch as a `with` block, NOT the shared monkeypatch fixture -- deliberately:
    monkeypatch.setattr only reverts at the end of the WHOLE test, so if a test has already
    patched this same target itself (e.g. tests/test_email_otp.py's _fake_send_otp_email, to
    capture codes from ITS OWN later calls), calling this fixture would permanently clobber that
    test's patch for the rest of the test. A `with patch(...)` block instead restores whatever was
    there before -- the test's own patch included -- the moment this fixture is done with it.
    """

    def _make(
        phone: str = "9000000001",
        password: str = "secret123!",
        full_name: str = "Test Citizen",
        preferred_language: str = "en",
        ward: str = "Test Ward",
        email: str | None = None,
    ):
        from unittest.mock import patch

        email = email or f"citizen{phone}@example.com"
        sent_codes: list[str] = []
        with patch("backend.routes.auth.send_otp_email", lambda to_email, code, purpose: sent_codes.append(code)):
            send_response = client.post("/auth/signup/email/send-code", json={"email": email})
            assert send_response.status_code == 204, send_response.text
            assert sent_codes, "signup should have emailed a verification code"

        verify_response = client.post(
            "/auth/signup/email/verify-code", json={"email": email, "code": sent_codes[-1]}
        )
        assert verify_response.status_code == 200, verify_response.text
        token = verify_response.json()["email_verification_token"]

        signup_response = client.post(
            "/auth/signup",
            json={
                "full_name": full_name,
                "phone": phone,
                "email": email,
                "email_verification_token": token,
                "password": password,
                "preferred_language": preferred_language,
                "ward": ward,
            },
        )
        assert signup_response.status_code == 200, signup_response.text
        body = signup_response.json()
        return body["access_token"], body["user"]

    return _make


@pytest.fixture()
def make_admin(db_session):
    """Factory fixture: seed an admin account directly into the DB (as a real deployment would)."""
    from backend.models import User
    from backend.services.auth_service import hash_password

    def _make(phone: str = "9999999999", password: str = "adminpass", full_name: str = "Test Admin"):
        db = db_session()
        user = User(
            full_name=full_name,
            phone=phone,
            password_hash=hash_password(password),
            role="admin",
            preferred_language="en",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.close()
        return user

    return _make


@pytest.fixture()
def make_worker(client, make_admin):
    """Factory fixture: seed an admin, then use it to create a worker via the real API."""

    def _make(phone: str = "9000000002", password: str = "secret123!", full_name: str = "Test Worker", ward: str = "Ward 14", preferred_language: str = "hi"):
        # Uses its own dedicated bootstrap-admin phone so this fixture composes safely
        # with a test that also calls make_admin() directly with the default phone.
        bootstrap_admin_phone = "9999900000"
        make_admin(phone=bootstrap_admin_phone, password="bootstrap-pass")
        admin_login = client.post(
            "/auth/login", json={"identifier": bootstrap_admin_phone, "password": "bootstrap-pass"}
        )
        admin_token = admin_login.json()["access_token"]

        response = client.post(
            "/admin/workers",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "full_name": full_name,
                "phone": phone,
                "password": password,
                "ward": ward,
                "preferred_language": preferred_language,
            },
        )
        assert response.status_code == 200, response.text

        worker_login = client.post("/auth/login", json={"identifier": phone, "password": password})
        body = worker_login.json()
        return body["access_token"], body["user"]

    return _make
