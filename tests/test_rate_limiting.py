"""Tests for the P0 rate-limiting fix: POST /auth/login and the three POST /ask-janmitra*
endpoints. See docs/RATE_LIMITING.md and backend/services/rate_limiter.py's module docstring for
the design.

Reuses this codebase's established Ask Sarthi test pattern (real ChromaDB retrieval, fake LLM/
vision calls -- no network call, see test_ask_janmitra.py's own docstring) rather than
reinventing it. Login tests need no such fakes -- /auth/login never calls any external service.

Rate-limit state is reset before every test by conftest.py's autouse `_reset_rate_limiters`
fixture -- each test below starts with a clean slate and is responsible for its own limit-
exceeding within itself, never relying on (or polluted by) any other test's requests.
"""

from __future__ import annotations

import time

import backend.services.rate_limiter as rate_limiter_module
from backend.config import settings
from backend.middleware import _general_limiter
from backend.services.rate_limiter import RateLimiter

from tests.test_ask_janmitra import _ask, _install_real_service
from tests.test_ask_janmitra_image import _ask_image
from tests.test_ask_janmitra_image import _install_real_service as _install_real_service_with_vision

# --- Login -----------------------------------------------------------------------------------


def test_login_within_limit_succeeds(client, make_citizen):
    """A single real login must never be affected by the limiter -- the normal case."""
    _, user = make_citizen(phone="9100000001")
    response = client.post("/auth/login", json={"identifier": "9100000001", "password": "secret123!"})
    assert response.status_code == 200, response.text
    assert response.json()["user"]["phone"] == "9100000001"


def test_login_requests_up_to_the_limit_all_succeed(client, make_citizen):
    """A citizen re-checking a mistyped phone/password a few times, or a demo switching between
    role logins, must never trip the limit -- only genuinely EXCEEDING it should."""
    make_citizen(phone="9100000002")
    for _ in range(settings.LOGIN_RATE_LIMIT):
        response = client.post("/auth/login", json={"identifier": "9100000002", "password": "secret123!"})
        assert response.status_code == 200, response.text


def test_login_exceeding_limit_returns_429_with_retry_after(client, make_citizen):
    make_citizen(phone="9100000003")
    for _ in range(settings.LOGIN_RATE_LIMIT):
        response = client.post("/auth/login", json={"identifier": "9100000003", "password": "secret123!"})
        assert response.status_code == 200

    response = client.post("/auth/login", json={"identifier": "9100000003", "password": "secret123!"})
    assert response.status_code == 429
    # Same plain error shape every other endpoint in this API already uses -- no custom handler.
    assert "detail" in response.json()
    assert response.json()["detail"]
    assert "Retry-After" in response.headers
    assert int(response.headers["Retry-After"]) > 0


def test_login_counts_failed_attempts_too(client, make_citizen):
    """The whole point is brute-force protection -- a script guessing wrong passwords must be
    throttled even though it never actually succeeds. Counts BOTH outcomes toward one limit."""
    make_citizen(phone="9100000004")
    for _ in range(settings.LOGIN_RATE_LIMIT):
        response = client.post("/auth/login", json={"identifier": "9100000004", "password": "wrong-password"})
        assert response.status_code == 401

    # The limit is now exhausted by failed attempts alone -- even the CORRECT password is
    # throttled, proving this isn't secretly only counting successes.
    response = client.post("/auth/login", json={"identifier": "9100000004", "password": "secret123!"})
    assert response.status_code == 429


def test_login_recovers_after_window_expires(client, make_citizen, monkeypatch):
    """Real recovery, not simulated -- a short window via config override (not a mocked clock),
    then a real wall-clock sleep just past it. Window widened to 3s (not 1s): bcrypt's
    deliberately-slow hashing makes each real login call here take ~200ms, so LOGIN_RATE_LIMIT+1
    of them need real headroom to land inside the SAME window and genuinely trip the limit, rather
    than the earliest ones already aging out before the loop even finishes."""
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_WINDOW_SECONDS", 3)
    make_citizen(phone="9100000005")

    for _ in range(settings.LOGIN_RATE_LIMIT):
        client.post("/auth/login", json={"identifier": "9100000005", "password": "secret123!"})
    blocked = client.post("/auth/login", json={"identifier": "9100000005", "password": "secret123!"})
    assert blocked.status_code == 429

    time.sleep(3.3)

    recovered = client.post("/auth/login", json={"identifier": "9100000005", "password": "secret123!"})
    assert recovered.status_code == 200, recovered.text


def test_login_different_phones_do_not_share_a_bucket_via_the_request_body(client, make_citizen):
    """The limiter keys login by client IP, not by the phone number in the request body -- two
    different phone numbers from the SAME client still share one bucket (this is intentional: an
    attacker trying many phone numbers from one IP is exactly the brute-force pattern being
    throttled). Documents this real, intentional behavior rather than leaving it implicit."""
    make_citizen(phone="9100000006")
    make_citizen(phone="9100000007")
    for _ in range(settings.LOGIN_RATE_LIMIT):
        client.post("/auth/login", json={"identifier": "9100000006", "password": "secret123!"})

    # Same TestClient == same apparent IP -- a different phone number does not get a fresh bucket.
    response = client.post("/auth/login", json={"identifier": "9100000007", "password": "secret123!"})
    assert response.status_code == 429


# --- Signup --------------------------------------------------------------------------------
# Its own dedicated limiter (SIGNUP_RATE_LIMIT, per-hour) -- see backend/deps.py's
# require_signup_rate_limit and config.py's own comment on why signup gets a stricter, differently
# -shaped window than login. Now lives on POST /auth/signup/email/send-code specifically (the
# real start of a signup attempt), not the final POST /auth/signup call -- see that route's own
# docstring in routes/auth.py. Distinct email addresses per attempt so this genuinely exercises
# the RATE limit itself, not the separate email-uniqueness check.


def test_signup_requests_up_to_the_limit_all_succeed(client, monkeypatch):
    monkeypatch.setattr("backend.routes.auth.send_otp_email", lambda to_email, code, purpose: None)
    for i in range(settings.SIGNUP_RATE_LIMIT):
        response = client.post("/auth/signup/email/send-code", json={"email": f"c{i:03d}@example.com"})
        assert response.status_code == 204, response.text


def test_signup_exceeding_limit_returns_429_with_retry_after(client, monkeypatch):
    monkeypatch.setattr("backend.routes.auth.send_otp_email", lambda to_email, code, purpose: None)
    for i in range(settings.SIGNUP_RATE_LIMIT):
        client.post("/auth/signup/email/send-code", json={"email": f"d{i:03d}@example.com"})

    response = client.post("/auth/signup/email/send-code", json={"email": "over-limit@example.com"})
    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert int(response.headers["Retry-After"]) > 0


def test_signup_limiter_has_its_own_bucket_separate_from_login(client, make_citizen, monkeypatch):
    """Exhausting the login limiter must never affect signup, and vice versa -- see
    backend/deps.py's own comment that the three limiters never share state."""
    monkeypatch.setattr("backend.routes.auth.send_otp_email", lambda to_email, code, purpose: None)
    make_citizen(phone="9110000200")
    for _ in range(settings.LOGIN_RATE_LIMIT):
        client.post("/auth/login", json={"identifier": "9110000200", "password": "secret123!"})
    exhausted_login = client.post("/auth/login", json={"identifier": "9110000200", "password": "secret123!"})
    assert exhausted_login.status_code == 429

    still_fresh_signup = client.post("/auth/signup/email/send-code", json={"email": "fresh-signup@example.com"})
    assert still_fresh_signup.status_code == 204, still_fresh_signup.text


# --- Ask Sarthi (AI) ---------------------------------------------------------------------------


def test_ai_request_within_limit_works(client, monkeypatch, make_citizen):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9100000010")
    response = _ask(client, token, "Who do I contact about street lights in Mohali?")
    assert response.status_code == 200, response.text


def test_ai_requests_up_to_the_limit_all_work(client, monkeypatch, make_citizen):
    """A real multi-turn Ask Sarthi conversation (location clarification, a follow-up, etc.) must
    never trip the limit -- only genuinely excessive requests should."""
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9100000011")
    for _ in range(settings.AI_RATE_LIMIT):
        response = _ask(client, token, "Who do I contact about street lights in Mohali?")
        assert response.status_code == 200, response.text


def test_ai_exceeding_limit_returns_429_with_retry_after(client, monkeypatch, make_citizen):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9100000012")
    for _ in range(settings.AI_RATE_LIMIT):
        response = _ask(client, token, "Who do I contact about street lights in Mohali?")
        assert response.status_code == 200

    response = _ask(client, token, "Who do I contact about street lights in Mohali?")
    assert response.status_code == 429
    assert response.json()["detail"]
    assert "Retry-After" in response.headers
    assert int(response.headers["Retry-After"]) > 0


def test_ai_different_users_are_not_blocked_together(client, monkeypatch, make_citizen):
    """Exhausting one citizen's AI quota must never affect a different citizen -- keyed by
    authenticated user id, not shared globally (see Step 11's "shared global limit accidentally
    blocking all users" concern)."""
    _install_real_service(monkeypatch)
    token_a, _ = make_citizen(phone="9100000013")
    token_b, _ = make_citizen(phone="9100000014")

    for _ in range(settings.AI_RATE_LIMIT):
        assert _ask(client, token_a, "Who do I contact about street lights in Mohali?").status_code == 200
    blocked = _ask(client, token_a, "Who do I contact about street lights in Mohali?")
    assert blocked.status_code == 429

    # A completely different citizen, still well within their own separate quota.
    still_works = _ask(client, token_b, "Who do I contact about street lights in Mohali?")
    assert still_works.status_code == 200, still_works.text


def test_ai_shared_across_text_image_and_voice_variants(client, monkeypatch, make_citizen):
    """One shared budget across all three Ask Sarthi entry points -- a citizen can't dodge the
    limit by switching from the text endpoint to the image endpoint."""
    _install_real_service_with_vision(monkeypatch)
    token, _ = make_citizen(phone="9100000015")

    for _ in range(settings.AI_RATE_LIMIT):
        assert _ask(client, token, "Who do I contact about street lights in Mohali?").status_code == 200

    # Same user, DIFFERENT ask-janmitra route -- still blocked, proving the shared bucket.
    response = _ask_image(client, token, "What is this?")
    assert response.status_code == 429


def test_ai_unauthorized_request_gets_401_not_a_bypass(client, monkeypatch):
    """No token at all must fail authentication (401) before any rate-limit bookkeeping runs --
    never a silent bypass, never a 429 that would imply the request was otherwise legitimate."""
    _install_real_service(monkeypatch)
    response = client.post("/ask-janmitra", json={"question": "hello", "language": "en"})
    assert response.status_code == 401


def test_ai_unauthorized_requests_never_consume_a_real_users_quota(client, monkeypatch, make_citizen):
    """A flood of unauthenticated requests must not exhaust anyone's real quota -- each one is
    rejected at auth, before the rate limiter (keyed by user id) ever runs."""
    _install_real_service(monkeypatch)
    for _ in range(settings.AI_RATE_LIMIT + 5):
        response = client.post("/ask-janmitra", json={"question": "hello", "language": "en"})
        assert response.status_code == 401

    token, _ = make_citizen(phone="9100000016")
    response = _ask(client, token, "Who do I contact about street lights in Mohali?")
    assert response.status_code == 200, response.text


def test_ai_recovers_after_window_expires(client, monkeypatch, make_citizen):
    """FLAKE FIX: this used to advance time via a real `time.sleep(1.2)` against a 1-second
    window -- only a ~200ms safety margin, which the full test suite's own system load (826
    other tests running concurrently with this one's own real Sarvam/embedding-model calls) could
    occasionally eat into, intermittently failing this test even though nothing was actually
    broken (confirmed: passed every time run in isolation, only ever flaked as part of the full
    suite). Replaced with a fake, monkeypatched clock -- `RateLimiter.check()`'s only time source
    is `time.monotonic()` (see rate_limiter.py), so controlling that directly makes the window's
    expiry deterministic and instant, with no real wall-clock sleep and no dependency on machine
    load at all."""
    _install_real_service(monkeypatch)
    monkeypatch.setattr(settings, "AI_RATE_LIMIT_WINDOW_SECONDS", 1)
    fake_now = [time.monotonic()]
    monkeypatch.setattr(rate_limiter_module.time, "monotonic", lambda: fake_now[0])
    token, _ = make_citizen(phone="9100000017")

    for _ in range(settings.AI_RATE_LIMIT):
        _ask(client, token, "Who do I contact about street lights in Mohali?")
    blocked = _ask(client, token, "Who do I contact about street lights in Mohali?")
    assert blocked.status_code == 429

    fake_now[0] += 1.2  # deterministically jump past the 1-second window, no real sleep needed

    recovered = _ask(client, token, "Who do I contact about street lights in Mohali?")
    assert recovered.status_code == 200, recovered.text


# --- General baseline (every OTHER route, via GeneralRateLimitMiddleware) ----------------------


def test_general_limit_covers_a_route_with_no_specific_limiter(client):
    """GET /locations/states has no rate-limit dependency of its own -- proves the general
    middleware alone is what protects it (and, being unauthenticated, that it correctly falls
    back to IP-based keying when there's no user yet)."""
    for _ in range(settings.GENERAL_RATE_LIMIT):
        response = client.get("/locations/states")
        assert response.status_code == 200

    response = client.get("/locations/states")
    assert response.status_code == 429
    assert response.json()["detail"]
    assert "Retry-After" in response.headers


def test_general_limit_keys_authenticated_requests_by_user_not_shared_ip(client, make_citizen):
    """Two different citizens hitting an ordinary authenticated route (GET /complaints, no
    specific limiter of its own) from the same TestClient/IP must not share one bucket -- the
    general middleware prefers the authenticated user id over IP whenever a valid token is
    present."""
    token_a, _ = make_citizen(phone="9100000020")
    token_b, _ = make_citizen(phone="9100000021")

    for _ in range(settings.GENERAL_RATE_LIMIT):
        response = client.get("/complaints", headers={"Authorization": f"Bearer {token_a}"})
        assert response.status_code == 200
    blocked = client.get("/complaints", headers={"Authorization": f"Bearer {token_a}"})
    assert blocked.status_code == 429

    still_works = client.get("/complaints", headers={"Authorization": f"Bearer {token_b}"})
    assert still_works.status_code == 200, still_works.text


def test_general_limit_does_not_apply_to_health_even_when_fully_exhausted(client):
    for _ in range(settings.GENERAL_RATE_LIMIT):
        client.get("/locations/states")
    exhausted = client.get("/locations/states")
    assert exhausted.status_code == 429

    # /health is exempt from the general middleware entirely -- must stay reachable regardless.
    response = client.get("/health")
    assert response.status_code == 200


# --- Memory bounding (Security Review Step 11: "memory leaks from stored identifiers") ---------


def test_stale_identifiers_are_eventually_swept_from_memory():
    """A direct unit test against RateLimiter itself (not the HTTP layer) -- proves an
    identifier's entry is actually removed once it's gone fully idle, not just that its own
    timestamps get pruned on its next visit (which would never happen for a one-time visitor).
    Forces the sweep to be due (rather than waiting the real 300s interval) by backdating
    `_last_sweep` directly -- the sweep's own timing gate, not its cleanup logic, is what's being
    skipped here.

    Uses the SAME window_seconds for both identifiers, matching how this limiter is actually used
    in production (each of the two real instances -- login, AI -- is always called with its own
    one fixed config value, never a mix). A real bug was caught writing this test with mismatched
    windows (1s vs 60s): the sweep's cutoff comes from the CALL that triggers it, not each
    identifier's own original window, so a stale entry checked with a much smaller window than the
    triggering call can look "fresh" under the triggering call's larger cutoff and survive an
    otherwise-due sweep. Documented as a known approximation in _sweep_if_due's own docstring
    rather than hidden -- harmless in practice since it never actually occurs here, but worth
    testing the realistic case rather than the mismatched one that doesn't reflect real usage."""
    limiter = RateLimiter()
    limiter.check("one-time-visitor", limit=5, window_seconds=1)
    assert "one-time-visitor" in limiter._hits

    time.sleep(1.2)  # let that identifier's single hit genuinely age out of the shared window

    limiter._last_sweep = 0.0  # force the next check() to treat a sweep as due
    limiter.check("a-different-identifier", limit=5, window_seconds=1)

    assert "one-time-visitor" not in limiter._hits, "stale identifier must be purged by the sweep, not retained forever"
    assert "a-different-identifier" in limiter._hits, "the identifier just checked must not be swept out from under itself"


# --- Scope: /health and normal authenticated workflow must be unaffected -----------------------


def test_health_endpoint_is_never_rate_limited(client):
    """Must stay reachable for monitoring/deploy healthchecks regardless of load elsewhere."""
    for _ in range(max(settings.LOGIN_RATE_LIMIT, settings.AI_RATE_LIMIT) + 10):
        response = client.get("/health")
        assert response.status_code == 200


def test_normal_authenticated_workflow_unaffected_by_ai_limiter(client, monkeypatch, make_citizen, make_worker):
    """The real citizen -> Ask Sarthi -> complaint-creation-adjacent workflow (well within limits)
    must work exactly as before this change -- complaint creation isn't rate-limited by this
    change at all, and a couple of Ask Sarthi calls shouldn't come close to the AI limit."""
    _install_real_service(monkeypatch)
    token, user = make_citizen(phone="9100000018", ward="Ward 14")
    make_worker(ward="Ward 14")

    ask_response = _ask(client, token, "Who do I contact about street lights in Mohali?")
    assert ask_response.status_code == 200

    complaint_response = client.post(
        "/complaints",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "text": "Street light not working near my house",
            "language": "en",
            "ward": "Ward 14",
        },
    )
    assert complaint_response.status_code == 200, complaint_response.text
