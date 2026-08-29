"""Tests for email verification (OTP), forgot-password, and login-by-email. See
backend/models.py's EmailOtp, backend/services/auth_service.py's generate_otp_code/
create_email_otp/verify_email_otp, backend/services/email_service.py's send_otp_email, and
backend/routes/auth.py's /email/send-verification, /email/verify, /forgot-password,
/reset-password, and the identifier-based /login.

backend.routes.auth.send_otp_email is monkeypatched in every test below to record calls instead
of hitting real SMTP -- matching how this suite fakes other external calls (Sarvam, etc.) rather
than making real network calls in tests.
"""

from __future__ import annotations

import pytest

from backend.config import settings
from backend.models import User


def _fake_send_otp_email(monkeypatch):
    """Replaces the real SMTP call with one that records (to_email, code, purpose) tuples and
    returns the last code sent -- tests need the plaintext code to submit back to /email/verify
    or /reset-password, which a real email send would only ever reveal via an actual inbox."""
    sent = []

    def _fake(to_email, code, purpose):
        sent.append((to_email, code, purpose))

    monkeypatch.setattr("backend.routes.auth.send_otp_email", _fake)
    return sent


def _mark_email_verified(db_session, user_id: int, email: str) -> None:
    """Directly sets User.email/email_verified in the DB -- bypasses the OTP round-trip for tests
    that only need an already-verified account as their starting state (forgot-password,
    reset-password, login-by-email), rather than re-testing send-verification/verify every time."""
    db = db_session()
    user = db.query(User).filter(User.id == user_id).first()
    user.email = email
    user.email_verified = True
    db.commit()
    db.close()


# --- Send verification -------------------------------------------------------------------------


def test_send_verification_succeeds_and_sends_a_code(client, make_citizen, monkeypatch):
    sent = _fake_send_otp_email(monkeypatch)
    token, _user = make_citizen(phone="9300000001")

    response = client.post(
        "/auth/email/send-verification",
        json={"email": "citizen1@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204, response.text
    assert len(sent) == 1
    assert sent[0][0] == "citizen1@example.com"
    assert sent[0][2] == "verify_email"


def test_email_dev_mode_skips_the_real_send_and_still_caches_the_code(client, make_citizen, monkeypatch):
    """LIVE-REPORTED GAP: every OTP-sending route always called the real send_otp_email -- the one
    Gmail account this app sends through hit Gmail's own daily sending-limit quota TWICE during
    heavy local/E2E testing (see PLAYWRIGHT_TEST_REPORT.md), blocking every signup-dependent test
    each time with no way to route around it. `EMAIL_DEV_MODE=true` must skip the real send
    entirely (mocked here as a call-counter, same as `_fake_send_otp_email`, but this time
    asserting it's NEVER called) while still caching the code via the existing
    GET /auth/_dev/otp-code mechanism, so a local/E2E run never depends on Gmail's quota at all."""
    sent = _fake_send_otp_email(monkeypatch)
    # make_citizen's own signup flow sends its own (separately-mocked, in conftest.py) real
    # verification email -- EMAIL_DEV_MODE must be switched on only AFTER that, or the fixture's
    # own "a code was actually sent" assertion would fail for the same reason this test exists.
    token, _user = make_citizen(phone="9300000099")
    monkeypatch.setattr(settings, "EMAIL_DEV_MODE", True)

    response = client.post(
        "/auth/email/send-verification",
        json={"email": "devmode1@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204, response.text
    assert sent == []  # the real send was never even attempted

    dev_code = client.get("/auth/_dev/otp-code", params={"email": "devmode1@example.com"})
    assert dev_code.status_code == 200
    assert len(dev_code.json()["code"]) == 6


def test_email_dev_mode_is_ignored_in_production_and_still_sends_for_real(client, make_citizen, monkeypatch):
    """The belt-and-suspenders half of the same fix -- EMAIL_DEV_MODE=true must never suppress a
    real send once ENVIRONMENT is production, matching _dev_cache_otp's own existing posture."""
    sent = _fake_send_otp_email(monkeypatch)
    token, _user = make_citizen(phone="9300000098")
    monkeypatch.setattr(settings, "EMAIL_DEV_MODE", True)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    response = client.post(
        "/auth/email/send-verification",
        json={"email": "devmode2@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204, response.text
    assert len(sent) == 1  # the real send path still ran despite EMAIL_DEV_MODE being true


def test_send_verification_requires_authentication(client, monkeypatch):
    _fake_send_otp_email(monkeypatch)
    response = client.post("/auth/email/send-verification", json={"email": "nobody@example.com"})
    assert response.status_code == 401


def test_send_verification_rejects_malformed_email(client, make_citizen, monkeypatch):
    _fake_send_otp_email(monkeypatch)
    token, _user = make_citizen(phone="9300000002")
    response = client.post(
        "/auth/email/send-verification",
        json={"email": "not-an-email"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400


def test_send_verification_rejects_email_already_verified_on_another_account(client, make_citizen, db_session, monkeypatch):
    _fake_send_otp_email(monkeypatch)
    token_a, user_a = make_citizen(phone="9300000003")
    token_b, _user_b = make_citizen(phone="9300000004")
    _mark_email_verified(db_session, user_a["id"], "taken@example.com")

    response = client.post(
        "/auth/email/send-verification",
        json={"email": "taken@example.com"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 409


def test_send_verification_returns_503_when_smtp_not_configured(client, make_citizen, monkeypatch):
    """No monkeypatch of send_otp_email here -- the real SMTP path runs, and with blank
    SMTP_USERNAME/SMTP_PASSWORD (the default test environment) it must fail as a clear 503, not a
    silent no-op or a fabricated success."""
    monkeypatch.setattr(settings, "SMTP_USERNAME", "")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "")
    token, _user = make_citizen(phone="9300000005")
    response = client.post(
        "/auth/email/send-verification",
        json={"email": "citizen5@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 503


def test_send_verification_rate_limited(client, make_citizen, monkeypatch):
    _fake_send_otp_email(monkeypatch)
    token, _user = make_citizen(phone="9300000006")

    for _ in range(settings.OTP_RATE_LIMIT):
        response = client.post(
            "/auth/email/send-verification",
            json={"email": "citizen6@example.com"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 204

    blocked = client.post(
        "/auth/email/send-verification",
        json={"email": "citizen6@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert blocked.status_code == 429


# --- Verify --------------------------------------------------------------------------------------


def test_verify_with_correct_code_succeeds(client, make_citizen, monkeypatch):
    sent = _fake_send_otp_email(monkeypatch)
    token, _user = make_citizen(phone="9300000010")
    client.post(
        "/auth/email/send-verification",
        json={"email": "citizen10@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    code = sent[-1][1]

    response = client.post(
        "/auth/email/verify", json={"code": code}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["email"] == "citizen10@example.com"
    assert body["email_verified"] is True


def test_verify_with_wrong_code_increments_attempts_and_fails(client, make_citizen, monkeypatch):
    sent = _fake_send_otp_email(monkeypatch)
    token, _user = make_citizen(phone="9300000011")
    client.post(
        "/auth/email/send-verification",
        json={"email": "citizen11@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    real_code = sent[-1][1]
    wrong_code = "000000" if real_code != "000000" else "111111"

    response = client.post(
        "/auth/email/verify", json={"code": wrong_code}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 400

    # The real code must still work afterward -- one wrong guess doesn't burn the row outright,
    # only counts toward OTP_MAX_ATTEMPTS (see test below for the exhausted case).
    response = client.post(
        "/auth/email/verify", json={"code": real_code}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text


def test_verify_exhausted_after_max_attempts(client, make_citizen, monkeypatch):
    monkeypatch.setattr(settings, "OTP_MAX_ATTEMPTS", 2)
    sent = _fake_send_otp_email(monkeypatch)
    token, _user = make_citizen(phone="9300000012")
    client.post(
        "/auth/email/send-verification",
        json={"email": "citizen12@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    real_code = sent[-1][1]
    wrong_code = "000000" if real_code != "000000" else "111111"

    for _ in range(2):
        response = client.post(
            "/auth/email/verify", json={"code": wrong_code}, headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 400

    # Even the correct code is now rejected -- the row is exhausted, not just the wrong guesses.
    response = client.post(
        "/auth/email/verify", json={"code": real_code}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 400


def test_verify_with_expired_code_fails(client, make_citizen, monkeypatch):
    sent = _fake_send_otp_email(monkeypatch)
    token, _user = make_citizen(phone="9300000013")
    client.post(
        "/auth/email/send-verification",
        json={"email": "citizen13@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    code = sent[-1][1]

    monkeypatch.setattr(settings, "OTP_EXPIRE_MINUTES", -1)
    # Re-send so the new row is created under the now-negative expiry window (already expired the
    # instant it's written) -- simpler and more realistic than reaching into the DB to backdate
    # the previous row's expires_at by hand.
    client.post(
        "/auth/email/send-verification",
        json={"email": "citizen13@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    expired_code = sent[-1][1]

    response = client.post(
        "/auth/email/verify", json={"code": expired_code}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 400


def test_verify_already_consumed_code_cannot_be_replayed(client, make_citizen, monkeypatch):
    sent = _fake_send_otp_email(monkeypatch)
    token, _user = make_citizen(phone="9300000014")
    client.post(
        "/auth/email/send-verification",
        json={"email": "citizen14@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    code = sent[-1][1]

    first = client.post(
        "/auth/email/verify", json={"code": code}, headers={"Authorization": f"Bearer {token}"}
    )
    assert first.status_code == 200

    replay = client.post(
        "/auth/email/verify", json={"code": code}, headers={"Authorization": f"Bearer {token}"}
    )
    assert replay.status_code == 400


# --- Forgot password ---------------------------------------------------------------------------
# Deliberately NOT no-enumeration -- a clear 404 for an unregistered/unverified email, unlike
# POST /auth/login's identical "wrong credentials" response. See forgot_password's own docstring
# in routes/auth.py for why this app makes that tradeoff differently than login does.


def test_forgot_password_with_verified_email_sends_a_code(client, make_citizen, db_session, monkeypatch):
    sent = _fake_send_otp_email(monkeypatch)
    _token, user = make_citizen(phone="9300000020")
    _mark_email_verified(db_session, user["id"], "citizen20@example.com")

    response = client.post("/auth/forgot-password", json={"email": "citizen20@example.com"})
    assert response.status_code == 204
    assert len(sent) == 1
    assert sent[0][2] == "reset_password"


def test_forgot_password_with_unregistered_email_returns_404(client, monkeypatch):
    sent = _fake_send_otp_email(monkeypatch)
    response = client.post("/auth/forgot-password", json={"email": "nobody-here@example.com"})
    assert response.status_code == 404
    assert sent == []


def test_forgot_password_with_unverified_email_returns_404(client, make_citizen, db_session, monkeypatch):
    """An email that exists on an account but was never verified must be treated the same as an
    unregistered one -- it was never proven to belong to that account's owner."""
    sent = _fake_send_otp_email(monkeypatch)
    _token, user = make_citizen(phone="9300000021")
    db = db_session()
    db_user = db.query(User).filter(User.id == user["id"]).first()
    db_user.email = "unverified21@example.com"
    db_user.email_verified = False
    db.commit()
    db.close()

    response = client.post("/auth/forgot-password", json={"email": "unverified21@example.com"})
    assert response.status_code == 404
    assert sent == []


# --- Reset password --------------------------------------------------------------------------


def test_reset_password_with_correct_code_succeeds(client, make_citizen, db_session, monkeypatch):
    sent = _fake_send_otp_email(monkeypatch)
    _token, user = make_citizen(phone="9300000030", password="oldpass123!")
    _mark_email_verified(db_session, user["id"], "citizen30@example.com")
    client.post("/auth/forgot-password", json={"email": "citizen30@example.com"})
    code = sent[-1][1]

    response = client.post(
        "/auth/reset-password",
        json={"email": "citizen30@example.com", "code": code, "new_password": "newpass456!"},
    )
    assert response.status_code == 204

    new_login = client.post("/auth/login", json={"identifier": "9300000030", "password": "newpass456!"})
    assert new_login.status_code == 200
    old_login = client.post("/auth/login", json={"identifier": "9300000030", "password": "oldpass123!"})
    assert old_login.status_code == 401


def test_reset_password_revokes_all_refresh_tokens(client, make_citizen, db_session, monkeypatch):
    sent = _fake_send_otp_email(monkeypatch)
    signup_token, user = make_citizen(phone="9300000031", password="oldpass123!")
    _mark_email_verified(db_session, user["id"], "citizen31@example.com")
    signup_login = client.post("/auth/login", json={"identifier": "9300000031", "password": "oldpass123!"})
    refresh_token = signup_login.json()["refresh_token"]

    client.post("/auth/forgot-password", json={"email": "citizen31@example.com"})
    code = sent[-1][1]
    response = client.post(
        "/auth/reset-password",
        json={"email": "citizen31@example.com", "code": code, "new_password": "newpass456!"},
    )
    assert response.status_code == 204

    still_valid = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert still_valid.status_code == 401


def test_reset_password_with_wrong_code_fails(client, make_citizen, db_session, monkeypatch):
    sent = _fake_send_otp_email(monkeypatch)
    _token, user = make_citizen(phone="9300000032")
    _mark_email_verified(db_session, user["id"], "citizen32@example.com")
    client.post("/auth/forgot-password", json={"email": "citizen32@example.com"})
    real_code = sent[-1][1]
    wrong_code = "000000" if real_code != "000000" else "111111"

    response = client.post(
        "/auth/reset-password",
        json={"email": "citizen32@example.com", "code": wrong_code, "new_password": "newpass456!"},
    )
    assert response.status_code == 400


def test_reset_password_with_unregistered_email_fails(client):
    response = client.post(
        "/auth/reset-password",
        json={"email": "nobody-here@example.com", "code": "123456", "new_password": "newpass456!"},
    )
    assert response.status_code == 400


def test_reset_password_rejects_a_weak_new_password(client, make_citizen, db_session, monkeypatch):
    sent = _fake_send_otp_email(monkeypatch)
    _token, user = make_citizen(phone="9300000033")
    _mark_email_verified(db_session, user["id"], "citizen33@example.com")
    client.post("/auth/forgot-password", json={"email": "citizen33@example.com"})
    code = sent[-1][1]

    response = client.post(
        "/auth/reset-password",
        json={"email": "citizen33@example.com", "code": code, "new_password": "weak"},
    )
    assert response.status_code == 400


# --- Login by email -------------------------------------------------------------------------


def test_login_with_verified_email_succeeds(client, make_citizen, db_session):
    _token, user = make_citizen(phone="9300000040", password="secret123!")
    _mark_email_verified(db_session, user["id"], "citizen40@example.com")

    response = client.post("/auth/login", json={"identifier": "citizen40@example.com", "password": "secret123!"})
    assert response.status_code == 200, response.text
    assert response.json()["user"]["phone"] == "9300000040"


def test_login_with_unverified_email_fails(client, make_citizen, db_session):
    _token, user = make_citizen(phone="9300000041", password="secret123!")
    db = db_session()
    db_user = db.query(User).filter(User.id == user["id"]).first()
    db_user.email = "citizen41@example.com"
    db_user.email_verified = False
    db.commit()
    db.close()

    response = client.post("/auth/login", json={"identifier": "citizen41@example.com", "password": "secret123!"})
    assert response.status_code == 401


def test_login_still_works_with_phone_after_email_added(client, make_citizen, db_session):
    _token, user = make_citizen(phone="9300000042", password="secret123!")
    _mark_email_verified(db_session, user["id"], "citizen42@example.com")

    response = client.post("/auth/login", json={"identifier": "9300000042", "password": "secret123!"})
    assert response.status_code == 200
