"""Regression tests for the P0 production-safety fix: a complaint may never be created without an
explicit, deterministic confirmation reply, and a bare service-category mention (with no
complaint-shaped language) must never be silently treated as a complaint.

Background (see the production-safety audit that found this, and the fix's own final report):
live testing against the running application found that a purely informational question
containing a service category and a real, supported city name -- e.g. "What is the procedure for
garbage collection complaints in Pune?" or "What are the rules for streetlight repair in Kanpur?"
-- was silently converted into a REAL, committed, auto-assigned complaint, with zero confirmation.

SIMPLIFIED (post-hoc review): this file originally carried 14 tests tracing to 15 named
scenarios from the fix's own spec. A direct comparison against tests/test_ask_sarthi.py,
tests/test_complaint_ward_and_confirmation_safety.py, and tests/test_ask_sarthi_agent_
architecture.py found 7 of those 14 were true duplicates -- same input shape, same safety
property, already asserted (often more thoroughly) elsewhere:
  - the basic draft-then-confirm-creates-a-complaint flow -> test_ask_sarthi.py::
    test_type_a_complaint_creates_and_assigns_complaint and test_complaint_ward_and_
    confirmation_safety.py's B2
  - a category-only complaint with no location asking for clarification -> test_ask_sarthi.py::
    test_missing_location_asks_for_clarification
  - a greeting never starting a complaint -> test_ask_sarthi_agent_architecture.py::
    test_greeting_hello_gets_a_greeting_not_the_generic_unclear_reply (same exact input, stronger
    assertions)
  - an unsupported/unserved location's honest refusal -> test_complaint_ward_and_confirmation_
    safety.py's A5
  - garbage/unparseable location text being rejected -> that same file's A4 (same exact input,
    stronger assertions)
  - explicit cancellation never creating a complaint -> that file's B3/B4 and several tests in
    test_ask_sarthi_agent_architecture.py
  - an ambiguous "okay" reply never confirming -> that file's B5/C1-C5 and test_ask_sarthi_
    agent_architecture.py's own parametrized ambiguous-reply test
The 7 tests below are the ones that survived that comparison -- each exercises a scenario (or a
state/wording variant) genuinely not covered by those other files. Every test in this file still
traces to a real named scenario from the fix's own spec; see this docstring's own history in git
blame for the fuller original numbering if needed.

Reuses the same real-Chroma-retrieval / fake-LLM / fake-complaint-agent pattern already
established in tests/test_ask_sarthi.py (see that module's own docstring) -- no new mocking
convention introduced here.
"""

from __future__ import annotations

from backend.models import Complaint
from backend.schemas.ask_sarthi import ConversationTurn
from tests.test_ask_sarthi import _ask, _install_real_service


def _turn(role: str, content: str) -> dict:
    return ConversationTurn(role=role, content=content).model_dump()


# --- an informational question naming a category + a real supported city must NOT create a
# complaint -- the exact P0 bug this fix closes. Not covered elsewhere: every other file's
# "doesn't create a complaint" tests use a genuinely complaint-shaped or ambiguous message, never
# a plain informational question that happens to name a category and a served city. ---


def test_info_question_garbage_procedure_in_supported_city_does_not_create_complaint(
    client, monkeypatch, db_session, make_citizen, make_worker,
):
    _install_real_service(monkeypatch)
    make_worker(phone="9200099001", ward="Pune")
    token, _ = make_citizen(phone="9200000001")

    resp = _ask(client, token, "What is the procedure for garbage collection complaints in Pune?")
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] != "TYPE_A_COMPLAINT"
    assert body.get("complaint_id") is None
    assert body["routed_to"] != "COMPLAINT_CREATED"

    db = db_session()
    assert db.query(Complaint).count() == 0
    db.close()


def test_info_question_streetlight_rules_in_supported_city_does_not_create_complaint(
    client, monkeypatch, db_session, make_citizen, make_worker,
):
    _install_real_service(monkeypatch)
    make_worker(phone="9200099002", ward="Kanpur")
    token, _ = make_citizen(phone="9200000002")

    resp = _ask(client, token, "What are the rules for streetlight repair in Kanpur?")
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] != "TYPE_A_COMPLAINT"
    assert body.get("complaint_id") is None
    assert body["routed_to"] != "COMPLAINT_CREATED"

    db = db_session()
    assert db.query(Complaint).count() == 0
    db.close()


# --- the "what time is" keyword-substring bug and its correct schedule-question counterpart --
# a designed contrastive pair guarding one specific historical bug (both messages contain "time";
# only one is a real civic question), not two independent checks of the same thing. ---


def test_generic_time_question_is_not_complaint_and_not_civic_schedule_info(client, monkeypatch, make_citizen):
    """Names a real city ("Mumbai") inside a non-civic time question -- a different edge case
    from the plain "What is the current time?" check elsewhere (does a real city name embedded in
    an unrelated question accidentally trigger location-based processing?)."""
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9200000006")

    resp = _ask(client, token, "What time is it in Mumbai?")
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] not in ("TYPE_A_COMPLAINT", "TYPE_A_MAYBE")
    assert body["routed_to"] != "RAG"
    assert body.get("complaint_id") is None


def test_civic_schedule_time_question_is_service_info(client, monkeypatch, make_citizen):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9200000007")

    resp = _ask(client, token, "What time does garbage collection happen?")
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "TYPE_B_SERVICE_INFO"
    assert body.get("complaint_id") is None


# --- context break / context switch mid-complaint-flow must not be swallowed as location text or
# complaint data, and must not create a complaint. Distinct from the similarly-named tests in
# test_ask_sarthi_agent_architecture.py: those exercise a citizen replying while
# AWAITING_CONFIRMATION (category+location already resolved); these exercise a citizen replying
# while still in DRAFT (category known, location not yet given) -- a different
# complaint_workflow_state. ---


def test_context_break_current_time_mid_complaint_flow_is_not_treated_as_location(client, monkeypatch, db_session, make_citizen):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9200000010")

    ask1 = _ask(client, token, "There is garbage near my house.")
    assert ask1.status_code == 200
    body1 = ask1.json()
    assert body1["follow_up_required"] is True

    history = [
        _turn("user", "There is garbage near my house."),
        _turn("assistant", body1["answer"]),
    ]
    ask2 = _ask(client, token, "What is the current time?", conversation_history=history)
    assert ask2.status_code == 200
    body2 = ask2.json()
    assert body2["routed_to"] != "COMPLAINT_CREATED"
    assert body2.get("complaint_id") is None

    db = db_session()
    assert db.query(Complaint).count() == 0
    db.close()


def test_context_switch_driving_licence_mid_complaint_flow_is_not_complaint_data(client, monkeypatch, db_session, make_citizen):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9200000011")

    ask1 = _ask(client, token, "There is garbage near my house.")
    assert ask1.status_code == 200
    body1 = ask1.json()

    history = [
        _turn("user", "There is garbage near my house."),
        _turn("assistant", body1["answer"]),
    ]
    ask2 = _ask(client, token, "Actually, how do I renew my driving licence?", conversation_history=history)
    assert ask2.status_code == 200
    body2 = ask2.json()
    assert body2["routed_to"] != "COMPLAINT_CREATED"
    assert body2.get("complaint_id") is None

    db = db_session()
    assert db.query(Complaint).count() == 0
    db.close()


# --- false-completion safety -- insufficient complaint data must never produce a success claim,
# and complaint_id must be null. A real end-to-end case, distinct from the unit-level grounding-
# check tests in test_ask_sarthi_agent_architecture.py (those call _run_grounding_checks
# directly against synthetic state; this proves the real pipeline never reaches that failure mode
# for an actual under-specified complaint in the first place). ---


def test_insufficient_complaint_data_never_claims_success(client, monkeypatch, db_session, make_citizen):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9200000015")

    resp = _ask(client, token, "There is a pothole near my house.")  # no location at all
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("complaint_id") is None
    assert "has been filed" not in body["answer"].lower()
    assert "has been registered" not in body["answer"].lower()
    assert "has been created" not in body["answer"].lower()

    db = db_session()
    assert db.query(Complaint).count() == 0
    db.close()


# --- LIVE-REPORTED GAP: a citizen who replies to "What issue would you like to report?" with just
# the bare category name ("Streetlight") got that exact text stored as the ENTIRE complaint
# description -- a worker sees the category twice over, with no real detail about what's wrong.
# Ask Sarthi should ask for a real description before showing the confirmation prompt, and the
# category established from the bare reply must survive into the eventual filed complaint even
# when the fuller description mentions an unrelated-looking keyword ("Road" in a streetlight
# description) that would otherwise mis-classify it. ---


def test_bare_category_reply_is_asked_to_describe_then_files_under_the_right_category(client, monkeypatch, db_session, make_citizen, make_worker):
    _install_real_service(monkeypatch)
    make_worker(phone="9200099080", ward="Mohali")
    token, _ = make_citizen(phone="9200000080")

    turn1 = _ask(client, token, "I want to file a complaint about something in Mohali.")
    assert turn1.status_code == 200
    body1 = turn1.json()
    history = [
        _turn("user", "I want to file a complaint about something in Mohali."),
        ConversationTurn(role="assistant", content=body1["answer"], complaint_workflow_state=body1.get("complaint_workflow_state")).model_dump(),
    ]

    turn2 = _ask(client, token, "Streetlight", conversation_history=history)
    assert turn2.status_code == 200
    body2 = turn2.json()
    assert body2["complaint_workflow_state"] == "AWAITING_DESCRIPTION", body2
    assert "describe" in body2["answer"].lower()
    history.append(_turn("user", "Streetlight"))
    history.append(ConversationTurn(role="assistant", content=body2["answer"], complaint_workflow_state=body2.get("complaint_workflow_state")).model_dump())

    description = "The pole near the bus stop on Ring Road has been dark for a week, it flickers and then goes off"
    turn3 = _ask(client, token, description, conversation_history=history)
    assert turn3.status_code == 200
    body3 = turn3.json()
    assert body3["service_category"] == "STREETLIGHTS", body3
    assert description in body3["answer"]
    assert body3["complaint_workflow_state"] == "AWAITING_CONFIRMATION"
    history.append(_turn("user", description))
    history.append(ConversationTurn(role="assistant", content=body3["answer"], complaint_workflow_state=body3.get("complaint_workflow_state")).model_dump())

    turn4 = _ask(client, token, "Yes, submit it.", conversation_history=history)
    assert turn4.status_code == 200
    body4 = turn4.json()
    assert "streetlights" in body4["answer"].lower()
    assert "roads potholes" not in body4["answer"].lower()

    db = db_session()
    complaint = db.query(Complaint).one()
    assert complaint.service_category == "STREETLIGHTS"
    assert complaint.translated_text and "ring road" in complaint.translated_text.lower()
    db.close()


def test_bare_category_reply_only_asked_to_describe_once_not_looped(client, monkeypatch, make_citizen, make_worker):
    _install_real_service(monkeypatch)
    make_worker(phone="9200099081", ward="Mohali")
    token, _ = make_citizen(phone="9200000081")

    turn1 = _ask(client, token, "I want to file a complaint about something in Mohali.")
    body1 = turn1.json()
    history = [
        _turn("user", "I want to file a complaint about something in Mohali."),
        ConversationTurn(role="assistant", content=body1["answer"], complaint_workflow_state=body1.get("complaint_workflow_state")).model_dump(),
    ]

    turn2 = _ask(client, token, "Streetlight", conversation_history=history)
    body2 = turn2.json()
    assert body2["complaint_workflow_state"] == "AWAITING_DESCRIPTION"
    history.append(_turn("user", "Streetlight"))
    history.append(ConversationTurn(role="assistant", content=body2["answer"], complaint_workflow_state=body2.get("complaint_workflow_state")).model_dump())

    # Replies with ANOTHER bare category word -- must not ask a second time (no infinite loop);
    # proceeds straight to confirmation using whatever text was actually given.
    turn3 = _ask(client, token, "Streetlight", conversation_history=history)
    assert turn3.status_code == 200
    body3 = turn3.json()
    assert body3["complaint_workflow_state"] == "AWAITING_CONFIRMATION"


def test_rich_description_given_directly_skips_the_describe_more_prompt(client, monkeypatch, make_citizen, make_worker):
    _install_real_service(monkeypatch)
    make_worker(phone="9200099082", ward="Mohali")
    token, _ = make_citizen(phone="9200000082")

    resp = _ask(client, token, "Street light not working in Mohali, it has been flickering for days and is now completely dark.")
    assert resp.status_code == 200
    body = resp.json()
    assert body["complaint_workflow_state"] == "AWAITING_CONFIRMATION"
    assert body["service_category"] == "STREETLIGHTS"
