"""Tests for backend/services/email_service.py's own SMTP mechanics (_deliver, send_otp_email).

Every other test suite that touches email (test_email_otp.py, test_complaint_lifecycle_emails.py,
EMAIL_DEV_MODE's own tests) monkeypatches send_otp_email/send_complaint_status_email away entirely
-- real send_otp_email code (message construction, smtplib calls) never actually runs in any of
those. This file closes that gap: it mocks smtplib itself (not send_otp_email), so the real
function body executes end to end and its actual SMTP calls -- host/port, starttls, login,
sendmail's from/to/message content -- are verified directly, without needing real Gmail
credentials or a live network call.
"""

from __future__ import annotations

import smtplib
from email import message_from_string
from unittest.mock import MagicMock

import pytest

from backend.config import settings
from backend.services.email_service import EmailServiceError, send_otp_email


def _decoded_text_part(raw_message: str) -> str:
    """The text/plain part's Content-Transfer-Encoding varies (base64 vs. 7bit/quoted-printable,
    an email.mime.text.MIMEText implementation detail based on the body's exact content) -- parse
    and decode properly rather than substring-matching the raw wire format, which would be
    encoding-dependent and brittle."""
    parsed = message_from_string(raw_message)
    for part in parsed.walk():
        if part.get_content_type() == "text/plain":
            return part.get_payload(decode=True).decode("utf-8")
    raise AssertionError("No text/plain part found in the message.")


@pytest.fixture(autouse=True)
def _configure_smtp(monkeypatch):
    """A real-shaped (but fake) SMTP config -- send_otp_email refuses to run at all otherwise
    (see _require_smtp_configured), and other suites leave these blank/mocked so this fixture
    can't rely on any ambient value."""
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_USERNAME", "bot@example.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "app-password")
    monkeypatch.setattr(settings, "EMAIL_FROM_ADDRESS", "bot@example.com")
    monkeypatch.setattr(settings, "EMAIL_FROM_NAME", "JanSarthi AI")
    monkeypatch.setattr(settings, "OTP_EXPIRE_MINUTES", 10)


def _fake_smtp_class():
    """A stand-in for smtplib.SMTP/SMTP_SSL: supports the `with ... as server:` usage
    _deliver relies on, and records every call made on the instance so tests can assert on them."""
    instance = MagicMock()
    instance.__enter__ = MagicMock(return_value=instance)
    instance.__exit__ = MagicMock(return_value=False)
    cls = MagicMock(return_value=instance)
    return cls, instance


def test_send_otp_email_delivers_via_starttls_on_the_default_port(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    smtp_cls, instance = _fake_smtp_class()
    monkeypatch.setattr(smtplib, "SMTP", smtp_cls)

    send_otp_email("citizen@example.com", "482913", "verify_email")

    smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=10)
    instance.starttls.assert_called_once()
    instance.login.assert_called_once_with("bot@example.com", "app-password")

    instance.sendmail.assert_called_once()
    from_addr, to_addrs, raw_message = instance.sendmail.call_args[0]
    assert from_addr == "bot@example.com"
    assert to_addrs == ["citizen@example.com"]
    assert "482913" in _decoded_text_part(raw_message)
    assert "Your JanSarthi AI email verification code" in raw_message


def test_send_otp_email_delivers_via_ssl_on_port_465(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_PORT", 465)
    smtp_cls, instance = _fake_smtp_class()
    monkeypatch.setattr(smtplib, "SMTP_SSL", smtp_cls)

    send_otp_email("citizen@example.com", "117204", "reset_password")

    smtp_cls.assert_called_once_with("smtp.example.com", 465, timeout=10)
    # Implicit TLS from the first byte on 465 -- no separate starttls() call, unlike the 587 path.
    instance.starttls.assert_not_called()
    instance.login.assert_called_once_with("bot@example.com", "app-password")

    from_addr, to_addrs, raw_message = instance.sendmail.call_args[0]
    assert to_addrs == ["citizen@example.com"]
    assert "117204" in _decoded_text_part(raw_message)
    assert "Your JanSarthi AI password reset code" in raw_message


def test_send_otp_email_raises_email_service_error_when_the_real_smtp_call_fails(monkeypatch):
    """Proves the failure path callers depend on (routes/auth.py turns this into a 503) is wired
    to a REAL smtplib exception, not just to a mock -- login() is where Gmail rejects a bad App
    Password in practice."""
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    smtp_cls, instance = _fake_smtp_class()
    instance.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Authentication failed")
    monkeypatch.setattr(smtplib, "SMTP", smtp_cls)

    with pytest.raises(EmailServiceError):
        send_otp_email("citizen@example.com", "555555", "verify_email")
