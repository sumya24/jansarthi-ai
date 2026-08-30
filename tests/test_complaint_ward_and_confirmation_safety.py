"""Regression tests for the TARGETED SAFETY CLEANUP pass on top of the P0 confirmation-gate fix
(see tests/test_ask_sarthi_complaint_confirmation.py's own docstring for that earlier fix).

This file covers the four specific issues a follow-up production-safety review flagged and this
cleanup closed:

1. `ctx.user.ward` fallback correctness -- an explicit (but unmatched/invalid) location must never
   be silently swapped for the citizen's own registered ward (see
   backend/services/orchestration/nodes.py's `_resolve_own_ward_worker_text` docstring).
2. Confirmation detection robustness -- a reply must never confirm a complaint unless a
   confirmation prompt is actually pending (see intent_classifier.py's `is_explicit_confirmation`).
3. The "yes, I already submitted a complaint..." edge case -- a longer reply that merely STARTS
   with a yes-word must not be treated as confirmation of the CURRENT draft.
4. Six-language confirmation/cancellation/ambiguous-reply coverage, verifying real database state.

Reuses the same real-Chroma-retrieval / fake-LLM / fake-complaint-agent pattern already
established in tests/test_ask_sarthi.py -- no new mocking convention introduced here.
"""

from __future__ import annotations

import pytest

from backend.models import Complaint
from backend.schemas.ask_sarthi import ConversationTurn
from tests.test_ask_sarthi import _ask, _install_real_service

_LANGUAGE_PHONE_INDEX = {"en": "1", "hi": "2", "mr": "3", "or": "4", "gu": "5", "bn": "6"}


def _turn(role: str, content: str) -> dict:
    return ConversationTurn(role=role, content=content).model_dump()


# ============================================================================
# A. Ward fallback
# ============================================================================


def test_ward_fallback_A1_explicit_location_takes_precedence_over_conflicting_home_ward(
    client, monkeypatch, db_session, make_citizen, make_worker,
):
    """An explicit, real, resolvable location must win even when the citizen's OWN registered
    ward also has a real worker -- the citizen is reporting a problem in Kanpur, not at home.

    Builds the second worker (Patiala) directly in the DB rather than via a second make_worker()
    call -- that fixture always bootstraps its own admin account on a fixed phone number, so
    calling it twice in one test collides (see tests/test_admin_routes.py's
    test_worker_cannot_delete_worker for the same established pattern)."""
    from backend.models import User
    from backend.services.auth_service import hash_password

    _install_real_service(monkeypatch)
    make_worker(phone="9300099001", ward="Kanpur")
    db = db_session()
    db.add(User(
        full_name="Patiala Worker", phone="9300099002", password_hash=hash_password("secret123!"),
        role="worker", preferred_language="en", ward="Patiala",
    ))
    db.commit()
    db.close()
    token, _ = make_citizen(phone="9300000001", ward="Patiala")

    draft = _ask(client, token, "There is a broken streetlight outside my house in Kanpur.", location_text="Kanpur")
    assert draft.status_code == 200
    body1 = draft.json()
    assert body1["routed_to"] == "NONE_AWAITING_CONFIRMATION"

    history = [
        _turn("user", "There is a broken streetlight outside my house in Kanpur."),
        _turn("assistant", body1["answer"]),
    ]
    confirm = _ask(client, token, "Yes, submit it.", conversation_history=history)
    assert confirm.status_code == 200
    body2 = confirm.json()
    assert body2["routed_to"] == "COMPLAINT_CREATED"
    assert body2["complaint_id"] is not None

    db = db_session()
    complaint = db.query(Complaint).filter(Complaint.id == body2["complaint_id"]).one()
    assert complaint.ward == "Kanpur"  # not "Patiala" -- explicit location wins
    db.close()


def test_ward_fallback_A2_missing_location_falls_back_to_citizens_own_ward(
    client, monkeypatch, db_session, make_citizen, make_worker,
):
    """No location signal anywhere in the exchange (no location_text, no city named in the
    message, no GPS) -- the citizen's own registered ward is used, exactly as this fallback was
    originally added to do (closing the non-English location-recovery gap)."""
    _install_real_service(monkeypatch)
    make_worker(phone="9300099003", ward="Patiala")
    token, _ = make_citizen(phone="9300000002", ward="Patiala")

    draft = _ask(client, token, "There is a broken streetlight outside my house.")
    assert draft.status_code == 200
    body1 = draft.json()
    assert body1["routed_to"] == "NONE_AWAITING_CONFIRMATION"
    assert db_session().query(Complaint).count() == 0

    history = [
        _turn("user", "There is a broken streetlight outside my house."),
        _turn("assistant", body1["answer"]),
    ]
    confirm = _ask(client, token, "Yes, submit it.", conversation_history=history)
    assert confirm.status_code == 200
    body2 = confirm.json()
    assert body2["routed_to"] == "COMPLAINT_CREATED"
    assert body2["complaint_id"] is not None

    db = db_session()
    complaint = db.query(Complaint).filter(Complaint.id == body2["complaint_id"]).one()
    assert complaint.ward == "Patiala"
    db.close()


def test_ward_fallback_A3_missing_location_and_unmatched_own_ward_asks_instead_of_inventing(
    client, monkeypatch, db_session, make_citizen,
):
    """No location signal anywhere, AND the citizen's own registered ward has no real worker --
    must ask for a location, never invent/guess one, never create a complaint."""
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9300000003", ward="Nowhere Ward, Unregistered City")

    resp = _ask(client, token, "There is a broken streetlight outside my house.")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("complaint_id") is None
    assert body["routed_to"] != "COMPLAINT_CREATED"
    assert body["follow_up_required"] is True

    db = db_session()
    assert db.query(Complaint).count() == 0
    db.close()


def test_ward_fallback_A4_invalid_garbage_location_never_silently_becomes_home_ward(
    client, monkeypatch, db_session, make_citizen, make_worker,
):
    """TARGETED SAFETY FIX regression: an explicit but garbage/unparseable location_text must get
    an honest "not currently served" answer, and must NEVER be silently discarded in favor of the
    citizen's own registered ward -- even though that ward has a real, currently-staffed worker."""
    _install_real_service(monkeypatch)
    make_worker(phone="9300099004", ward="Patiala")
    token, _ = make_citizen(phone="9300000004", ward="Patiala")

    resp = _ask(client, token, "There is a pothole near my house.", location_text="asdkjfh1234!!!")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("complaint_id") is None
    assert body["routed_to"] != "COMPLAINT_CREATED"
    assert "has been filed" not in body["answer"].lower()
    # Never silently created in the citizen's own served ward instead.
    assert "patiala" not in body["answer"].lower()

    db = db_session()
    assert db.query(Complaint).count() == 0
    db.close()


def test_ward_fallback_A5_explicit_unserved_city_never_silently_becomes_home_ward(
    client, monkeypatch, db_session, make_citizen, make_worker,
):
    """Same regression as A4, but with a real (not garbage) city name that simply has no worker --
    the "conflicting with user ward" scenario. Must get an honest refusal naming Kolhapur, never a
    complaint silently filed against Patiala instead."""
    _install_real_service(monkeypatch)
    make_worker(phone="9300099005", ward="Patiala")
    token, _ = make_citizen(phone="9300000005", ward="Patiala")

    resp = _ask(client, token, "There is a pothole near my house.", location_text="Kolhapur")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("complaint_id") is None
    assert body["routed_to"] != "COMPLAINT_CREATED"

    db = db_session()
    assert db.query(Complaint).count() == 0
    db.close()


# ============================================================================
# B. Confirmation context
# ============================================================================


def test_confirmation_context_B1_bare_yes_with_no_pending_draft_creates_nothing(
    client, monkeypatch, db_session, make_citizen,
):
    """"yes" as a completely fresh message, with no prior complaint-flow turn at all, must not be
    treated as confirming anything -- there is nothing to confirm."""
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9300000006")

    resp = _ask(client, token, "yes")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("complaint_id") is None
    assert body["routed_to"] != "COMPLAINT_CREATED"

    db = db_session()
    assert db.query(Complaint).count() == 0
    db.close()


def test_confirmation_context_B1_bare_okay_with_no_pending_draft_creates_nothing(
    client, monkeypatch, db_session, make_citizen,
):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9300000007")

    resp = _ask(client, token, "okay")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("complaint_id") is None
    assert body["routed_to"] != "COMPLAINT_CREATED"

    db = db_session()
    assert db.query(Complaint).count() == 0
    db.close()


def test_confirmation_context_B1_bare_submit_with_no_pending_draft_creates_nothing(
    client, monkeypatch, db_session, make_citizen,
):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9300000008")

    resp = _ask(client, token, "submit")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("complaint_id") is None
    assert body["routed_to"] != "COMPLAINT_CREATED"

    db = db_session()
    assert db.query(Complaint).count() == 0
    db.close()


def _draft_streetlight_complaint(client, token, make_worker):
    make_worker(phone="9300099009", ward="Kanpur")
    draft = _ask(client, token, "There is a broken streetlight outside my house in Kanpur.", location_text="Kanpur")
    assert draft.status_code == 200
    body = draft.json()
    assert body["routed_to"] == "NONE_AWAITING_CONFIRMATION"
    history = [
        _turn("user", "There is a broken streetlight outside my house in Kanpur."),
        _turn("assistant", body["answer"]),
    ]
    return history


def test_confirmation_context_B2_yes_with_pending_draft_creates_the_complaint(
    client, monkeypatch, db_session, make_citizen, make_worker,
):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9300000009")
    history = _draft_streetlight_complaint(client, token, make_worker)
    assert db_session().query(Complaint).count() == 0

    confirm = _ask(client, token, "yes", conversation_history=history)
    assert confirm.status_code == 200
    body = confirm.json()
    assert body["routed_to"] == "COMPLAINT_CREATED"
    assert body["complaint_id"] is not None

    db = db_session()
    assert db.query(Complaint).count() == 1
    db.close()


def test_confirmation_context_B3_no_with_pending_draft_creates_nothing(
    client, monkeypatch, db_session, make_citizen, make_worker,
):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9300000010")
    history = _draft_streetlight_complaint(client, token, make_worker)

    cancel = _ask(client, token, "no", conversation_history=history)
    assert cancel.status_code == 200
    body = cancel.json()
    assert body.get("complaint_id") is None
    assert body["routed_to"] != "COMPLAINT_CREATED"

    db = db_session()
    assert db.query(Complaint).count() == 0
    db.close()


def test_confirmation_context_B4_cancel_with_pending_draft_creates_nothing(
    client, monkeypatch, db_session, make_citizen, make_worker,
):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9300000011")
    history = _draft_streetlight_complaint(client, token, make_worker)

    cancel = _ask(client, token, "cancel", conversation_history=history)
    assert cancel.status_code == 200
    body = cancel.json()
    assert body.get("complaint_id") is None
    assert body["routed_to"] != "COMPLAINT_CREATED"

    db = db_session()
    assert db.query(Complaint).count() == 0
    db.close()


def test_confirmation_context_B5_not_now_with_pending_draft_is_ambiguous_not_confirmed(
    client, monkeypatch, db_session, make_citizen, make_worker,
):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9300000012")
    history = _draft_streetlight_complaint(client, token, make_worker)

    reply = _ask(client, token, "not now", conversation_history=history)
    assert reply.status_code == 200
    body = reply.json()
    assert body.get("complaint_id") is None
    assert body["routed_to"] != "COMPLAINT_CREATED"
    assert body["follow_up_required"] is True  # re-asks, never guesses

    db = db_session()
    assert db.query(Complaint).count() == 0
    db.close()


# ============================================================================
# C. Duplicate/ambiguous confirmation -- the specific edge case this cleanup closes
# ============================================================================


def test_ambiguous_C1_yes_already_submitted_does_not_confirm_the_current_draft(
    client, monkeypatch, db_session, make_citizen, make_worker,
):
    """The exact edge case named in the production-safety review: a reply that STARTS with "yes"
    but goes on to say something else entirely must not be treated as confirmation."""
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9300000013")
    history = _draft_streetlight_complaint(client, token, make_worker)

    reply = _ask(client, token, "yes, I already submitted a complaint about this", conversation_history=history)
    assert reply.status_code == 200
    body = reply.json()
    assert body.get("complaint_id") is None
    assert body["routed_to"] != "COMPLAINT_CREATED"

    db = db_session()
    assert db.query(Complaint).count() == 0
    db.close()


def test_ambiguous_C2_i_already_submitted_this_does_not_confirm(
    client, monkeypatch, db_session, make_citizen, make_worker,
):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9300000014")
    history = _draft_streetlight_complaint(client, token, make_worker)

    reply = _ask(client, token, "I already submitted this", conversation_history=history)
    assert reply.status_code == 200
    body = reply.json()
    assert body.get("complaint_id") is None
    assert body["routed_to"] != "COMPLAINT_CREATED"

    db = db_session()
    assert db.query(Complaint).count() == 0
    db.close()


def test_ambiguous_C3_i_submitted_it_yesterday_does_not_confirm(
    client, monkeypatch, db_session, make_citizen, make_worker,
):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9300000015")
    history = _draft_streetlight_complaint(client, token, make_worker)

    reply = _ask(client, token, "I submitted it yesterday", conversation_history=history)
    assert reply.status_code == 200
    body = reply.json()
    assert body.get("complaint_id") is None
    assert body["routed_to"] != "COMPLAINT_CREATED"

    db = db_session()
    assert db.query(Complaint).count() == 0
    db.close()


def test_ambiguous_C4_yes_i_did_does_not_confirm(
    client, monkeypatch, db_session, make_citizen, make_worker,
):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9300000016")
    history = _draft_streetlight_complaint(client, token, make_worker)

    reply = _ask(client, token, "yes I did", conversation_history=history)
    assert reply.status_code == 200
    body = reply.json()
    assert body.get("complaint_id") is None
    assert body["routed_to"] != "COMPLAINT_CREATED"

    db = db_session()
    assert db.query(Complaint).count() == 0
    db.close()


def test_ambiguous_C5_plain_yes_still_confirms_after_the_C1_C4_tightening(
    client, monkeypatch, db_session, make_citizen, make_worker,
):
    """Guards against over-correcting: the tightened confirmation check must still recognize a
    genuine, simple "yes, submit it" style reply."""
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9300000017")
    history = _draft_streetlight_complaint(client, token, make_worker)

    confirm = _ask(client, token, "yes, submit it", conversation_history=history)
    assert confirm.status_code == 200
    body = confirm.json()
    assert body["routed_to"] == "COMPLAINT_CREATED"
    assert body["complaint_id"] is not None

    db = db_session()
    assert db.query(Complaint).count() == 1
    db.close()


# ============================================================================
# D. Six languages -- confirmation / cancellation / ambiguous, verifying DB state.
#
# The conversation_history assistant turn is seeded directly with the REAL, Sarvam-verified
# confirmation-prompt fragment for each language (see nodes.py's `_CONFIRMATION_PROMPT_MARKERS`,
# same fragments this fix consolidated into one place) -- the same established pattern already
# used by tests/test_ask_sarthi_tracing.py to test a specific turn directly without depending on
# a live translation call in this test suite (which mocks Sarvam, see _install_real_service).
# ============================================================================

_CONFIRMATION_PROMPT_FRAGMENT = {
    "en": "Would you like me to submit this complaint?",
    "hi": "क्या मैं यह शिकायत दर्ज करूँ?",
    "mr": "मी ही तक्रार मी दाखल करावी का?",
    "or": "ମୁଁ ଏହି ଅଭିଯୋଗ ଦାଖଲ କରେ କି?",
    "gu": "શું હું આ ફરિયાદ સબમિટ કરું?",
    "bn": "আমি কি এই অভিযোগটি জমা দিই?",
}
_CONFIRM_WORD = {"en": "yes", "hi": "haan", "mr": "हो", "or": "ହଁ", "gu": "હા", "bn": "হ্যাঁ"}
_CANCEL_WORD = {"en": "no", "hi": "nahi", "mr": "नाही", "or": "ନା", "gu": "ના", "bn": "না"}
_AMBIGUOUS_WORD = "maybe"  # deliberately not a recognized word/phrase in ANY language's tables


def _seeded_history(language: str) -> list[dict]:
    return [
        _turn("user", "Street light not working in Mohali."),
        _turn("assistant", f"Your complaint would be about Streetlights in \"Mohali\": "
                            f"\"Street light not working in Mohali.\". {_CONFIRMATION_PROMPT_FRAGMENT[language]}"),
    ]


@pytest.mark.parametrize("language", ["en", "hi", "mr", "or", "gu", "bn"])
def test_six_language_D_confirmation_creates_exactly_one_complaint(
    client, monkeypatch, db_session, make_citizen, make_worker, language,
):
    _install_real_service(monkeypatch)
    idx = _LANGUAGE_PHONE_INDEX[language]
    make_worker(phone=f"93010{idx}9001", ward="Mohali")
    token, _ = make_citizen(phone=f"93010{idx}0001")

    before = db_session().query(Complaint).count()
    history = _seeded_history(language)
    confirm = _ask(client, token, _CONFIRM_WORD[language], conversation_history=history, language=language)
    assert confirm.status_code == 200
    body = confirm.json()
    assert body["routed_to"] == "COMPLAINT_CREATED", f"{language}: {body}"
    assert body["complaint_id"] is not None

    after = db_session().query(Complaint).count()
    assert after == before + 1, f"{language}: expected exactly one new complaint"


@pytest.mark.parametrize("language", ["en", "hi", "mr", "or", "gu", "bn"])
def test_six_language_D_cancellation_creates_no_complaint(
    client, monkeypatch, db_session, make_citizen, make_worker, language,
):
    _install_real_service(monkeypatch)
    idx = _LANGUAGE_PHONE_INDEX[language]
    make_worker(phone=f"93010{idx}9002", ward="Mohali")
    token, _ = make_citizen(phone=f"93010{idx}0002")

    before = db_session().query(Complaint).count()
    history = _seeded_history(language)
    cancel = _ask(client, token, _CANCEL_WORD[language], conversation_history=history, language=language)
    assert cancel.status_code == 200
    body = cancel.json()
    assert body.get("complaint_id") is None, f"{language}: {body}"
    assert body["routed_to"] != "COMPLAINT_CREATED"

    after = db_session().query(Complaint).count()
    assert after == before, f"{language}: cancellation must not create a complaint"


@pytest.mark.parametrize("language", ["en", "hi", "mr", "or", "gu", "bn"])
def test_six_language_D_ambiguous_reply_creates_no_complaint(
    client, monkeypatch, db_session, make_citizen, make_worker, language,
):
    _install_real_service(monkeypatch)
    idx = _LANGUAGE_PHONE_INDEX[language]
    make_worker(phone=f"93010{idx}9003", ward="Mohali")
    token, _ = make_citizen(phone=f"93010{idx}0003")

    before = db_session().query(Complaint).count()
    history = _seeded_history(language)
    reply = _ask(client, token, _AMBIGUOUS_WORD, conversation_history=history, language=language)
    assert reply.status_code == 200
    body = reply.json()
    assert body.get("complaint_id") is None, f"{language}: {body}"
    assert body["routed_to"] != "COMPLAINT_CREATED"

    after = db_session().query(Complaint).count()
    assert after == before, f"{language}: an ambiguous reply must not create a complaint"
