"""Broad robustness matrix for the location-clarification fix (see
backend/services/orchestration/nodes.py's clarification_flow_node, the "location missing
entirely" branch): a citizen can type literally anything into the location field/prompt, and this
suite checks the assistant's behavior stays honest and correct across that whole range -- real
cities (exact, lowercase, alias, non-Latin script), a genuinely ambiguous state name, gibberish,
numbers, symbols, very long junk, whitespace-only, and no input at all -- through BOTH the paths
that share this one clarification node: civic-info questions (TYPE_B_SERVICE_INFO) and complaint
filing (TYPE_A_COMPLAINT), plus a multi-turn recovery check and a localization wiring check.

Reuses test_ask_sarthi.py's `_real_ask_sarthi_service`/`_install_real_service`/`_ask` helpers
-- same real ChromaDB + real gazetteer, fake LLM/complaint-agent, so these are genuine retrieval/
routing/location-matching checks, not mocks of the thing under test.
"""

import pytest

import backend.routes.ask_sarthi as ask_sarthi_module
from backend.schemas.ask_sarthi import ConversationTurn
from tests.test_ask_sarthi import _ask, _install_real_service, _real_ask_sarthi_service

_UNRECOGNIZED = "i couldn't recognize that as a location"
_DEFAULT_ASK = "what is the location? this helps me give you the correct local information."

_QUESTION = "How do I report a garbage collection issue?"


# --- civic-info flow (TYPE_B_SERVICE_INFO): real cities/aliases must resolve straight to RAG,
# never touch the clarification node at all ---


@pytest.mark.parametrize(
    "phone,location_text",
    [
        ("9100000010", "Mumbai"),  # exact, real gazetteer city
        ("9100000011", "mumbai"),  # lowercase -- case-insensitive match
        ("9100000012", "MUMBAI"),  # uppercase
        ("9100000013", "Bangalore"),  # common-name alias -> Bengaluru
        ("9100000014", "चेन्नई"),  # Devanagari alias -> Chennai (non-Latin script support)
    ],
)
def test_resolvable_location_text_never_hits_clarification(client, monkeypatch, make_citizen, phone, location_text):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone=phone)
    resp = _ask(client, token, _QUESTION, location_text=location_text)
    assert resp.status_code == 200
    body = resp.json()
    assert body["routed_to"] == "RAG", f"{location_text!r} should have resolved straight to RAG, got: {body}"
    assert body["follow_up_required"] is False


# --- a genuinely ambiguous STATE name (2+ cities in the corpus) must ask which city, not fall
# into the "couldn't recognize" branch -- the two branches must never be confused ---


def test_ambiguous_state_name_asks_which_city_not_couldnt_recognize(client, monkeypatch, make_citizen):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9100000050")
    # Maharashtra has both Mumbai and Nagpur in the gazetteer -- see RagGazetteer.cities_by_state.
    resp = _ask(client, token, _QUESTION, location_text="Maharashtra")
    assert resp.status_code == 200
    body = resp.json()
    assert body["follow_up_required"] is True
    answer_lower = body["answer"].lower()
    assert "mumbai" in answer_lower and "nagpur" in answer_lower
    assert _UNRECOGNIZED not in answer_lower


# --- genuinely unrecognizable input, across very different shapes -- all must get the honest
# "couldn't recognize" message, never the generic "what is the location?" repeat, and never a
# crash ---


@pytest.mark.parametrize(
    "phone,location_text",
    [
        ("9100000020", "Zzz Nonexistent Place"),
        ("9100000021", "12345"),
        ("9100000022", "!!!???$$$"),
        ("9100000023", "x" * 2000),  # very long junk -- must not crash or get silently truncated into a match
        ("9100000024", "asdkjhaskjdhaksjdh"),
        ("9100000025", "मॉस्को"),  # a real place name, but not one in this app's gazetteer (Moscow, in Devanagari)
    ],
)
def test_unrecognizable_location_text_gets_honest_message_not_a_crash(client, monkeypatch, make_citizen, phone, location_text):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone=phone)
    resp = _ask(client, token, _QUESTION, location_text=location_text)
    assert resp.status_code == 200, f"{location_text!r} caused a server error: {resp.text}"
    body = resp.json()
    assert body["follow_up_required"] is True
    assert _UNRECOGNIZED in body["answer"].lower(), f"{location_text!r} got: {body['answer']!r}"


def test_whitespace_only_location_text(client, monkeypatch, make_citizen):
    """Documents actual behavior for a real edge case (an accidental space/blank submission):
    whitespace is still a non-empty string the citizen explicitly sent, so it's treated the same
    as any other unrecognizable explicit signal, not as "no location given at all"."""
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9100000051")
    resp = _ask(client, token, _QUESTION, location_text="   ")
    assert resp.status_code == 200
    body = resp.json()
    assert body["follow_up_required"] is True
    assert _UNRECOGNIZED in body["answer"].lower()


def test_no_location_text_at_all_keeps_the_generic_question(client, monkeypatch, make_citizen):
    """The control case: nothing sent at all still gets the original, generic question -- the
    fix only changes behavior for an explicit-but-unresolved signal, never this case."""
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9100000052")
    resp = _ask(client, token, _QUESTION)
    assert resp.status_code == 200
    body = resp.json()
    assert body["follow_up_required"] is True
    assert body["answer"].lower() == _DEFAULT_ASK


# --- the complaint-filing flow (TYPE_A_COMPLAINT) shares clarification_flow_node's CODE with
# civic-info questions, but NOT the same real-world reachability: complaint_flow_node has its own,
# separate, earlier check for "a location was given but doesn't match any real staffed ward" (see
# its own extensive docstring/comment on `has_real_location`/`known_place`), which fires for ANY
# non-empty location signal -- real place or gibberish alike -- before this fix's
# `location_explicit_signal_unresolved` branch could ever be reached. That branch is therefore
# only reachable, in practice, for the RAG/civic-info path (see the "unrecognizable_location_text"
# tests above); for complaint filing, an unrecognizable location correctly gets complaint_flow's
# OWN pre-existing honest "doesn't currently have workers set up" message instead. This section
# confirms THAT behavior stays correct, and that recovery still works, rather than asserting the
# new message shows up somewhere it structurally cannot. ---


def test_complaint_flow_unresolvable_location_gets_its_own_honest_no_workers_message(client, monkeypatch, make_citizen, make_worker):
    _install_real_service(monkeypatch)
    make_worker(phone="9100099060", ward="Mohali")
    token, _ = make_citizen(phone="9100000060")

    turn1 = _ask(client, token, "I want to file a complaint.")
    assert turn1.status_code == 200
    body1 = turn1.json()
    history = [
        ConversationTurn(role="user", content="I want to file a complaint.").model_dump(),
        ConversationTurn(role="assistant", content=body1["answer"]).model_dump(),
    ]

    turn2 = _ask(client, token, "Streetlight.", conversation_history=history)
    assert turn2.status_code == 200
    body2 = turn2.json()
    history.append(ConversationTurn(role="user", content="Streetlight.").model_dump())
    history.append(ConversationTurn(role="assistant", content=body2["answer"]).model_dump())

    turn3 = _ask(
        client, token, "Somewhere over there.", conversation_history=history,
        location_text="Somewhere over there.",
    )
    assert turn3.status_code == 200
    body3 = turn3.json()
    # NOT the "couldn't recognize" clarification message (see this section's own docstring) --
    # complaint_flow's own honest, TERMINAL "no workers set up" message, never a repeated question.
    assert body3["routed_to"] == "NONE_OUT_OF_SCOPE"
    assert body3["follow_up_required"] is False
    assert "doesn't currently have workers set up" in body3["answer"].lower()
    assert body3.get("complaint_id") is None  # never files a complaint against an unresolved location


def test_complaint_flow_new_message_is_unreachable_with_zero_location_signal_and_no_home_ward(client, monkeypatch, make_citizen):
    """The one complaint-flow case where `location_explicit_signal_unresolved` COULD be true is
    structurally impossible: reaching clarification_reason="location" at all requires
    `ctx.location_text` to already be empty (see complaint_flow_node's own `known_place` check --
    any non-empty signal takes the "no workers set up" branch above instead), and
    `location_explicit_signal_unresolved` is `bool(ctx.location_text) and ...` -- always False
    when `ctx.location_text` is empty. So this citizen (no ward set at signup) correctly gets the
    plain, original "what is the location?" question, not the new message -- documenting that this
    fix genuinely changes nothing about complaint filing's own behavior."""
    _install_real_service(monkeypatch)
    # A ward that resolves to nothing in the RAG gazetteer either -- signup requires a non-empty
    # ward, so this stands in for "no usable location signal anywhere," same as the original live
    # bug's own throwaway test citizen.
    token, _ = make_citizen(phone="9100000063", ward="Zzz Nonexistent Home Ward")

    history = [
        ConversationTurn(role="user", content="I want to file a complaint.").model_dump(),
        ConversationTurn(role="assistant", content="What issue would you like to report?").model_dump(),
    ]
    resp = _ask(client, token, "Streetlight.", conversation_history=history)
    assert resp.status_code == 200
    body = resp.json()
    assert body["follow_up_required"] is True
    assert body["answer"].lower() == _DEFAULT_ASK
    assert _UNRECOGNIZED not in body["answer"].lower()


def test_complaint_flow_no_workers_message_is_terminal_not_a_stuck_loop(client, monkeypatch, make_citizen, make_worker):
    """complaint_flow_node marks the workflow CANCELLED the moment it gives the "no workers set
    up" message (see its own `complaint_workflow_state: "CANCELLED"`) -- deliberately terminal,
    not a clarification loop waiting for a better answer. Confirmed here: asking again in the same
    thread never repeats the identical message (the original live bug's own symptom) -- a bare
    follow-up instead correctly falls to unclear_flow_node (see intent_node's own
    `_last_turn_invites_complaint_reply` safety check: the immediately preceding turn was a
    terminal message, not an actual clarification question, so it's correctly NOT auto-continued
    into the dead complaint attempt)."""
    _install_real_service(monkeypatch)
    make_worker(phone="9100099061", ward="Mohali")
    token, _ = make_citizen(phone="9100000061")

    history = [
        ConversationTurn(role="user", content="I want to file a complaint.").model_dump(),
        ConversationTurn(
            role="assistant", content="What issue would you like to report?"
        ).model_dump(),
        ConversationTurn(role="user", content="Streetlight.").model_dump(),
        ConversationTurn(
            role="assistant", content="What is the location? This helps me give you the correct local information."
        ).model_dump(),
    ]
    bad_turn = _ask(client, token, "Blah blah nowhere", conversation_history=history, location_text="Blah blah nowhere")
    assert bad_turn.status_code == 200
    bad_body = bad_turn.json()
    assert bad_body["routed_to"] == "NONE_OUT_OF_SCOPE"
    assert bad_body["complaint_id"] is None

    history.append(ConversationTurn(role="user", content="Blah blah nowhere").model_dump())
    history.append(ConversationTurn(role="assistant", content=bad_body["answer"]).model_dump())

    follow_up = _ask(client, token, "Use my current location.", conversation_history=history, location_text="Mohali")
    assert follow_up.status_code == 200
    follow_up_body = follow_up.json()
    # Not silently resumed into the cancelled complaint, and not the same message repeated verbatim.
    assert follow_up_body["answer"] != bad_body["answer"]
    assert follow_up_body["routed_to"] != "NONE_OUT_OF_SCOPE"


def test_complaint_flow_recovers_after_a_generic_location_prompt_then_a_good_one(client, monkeypatch, make_citizen, make_worker):
    """Recovery from the OTHER, genuinely re-askable prompt: complaint_flow_node's own generic
    "what is the location?" (reached when NO location signal exists at all yet -- still `DRAFT`,
    not cancelled). This is the shape of the original live bug report, and is already covered
    end-to-end by test_orchestration_graph.py's test_multi_turn_complaint_filing_category_then_
    location; this is a second, independent run of the same shape for this fix's own branch."""
    _install_real_service(monkeypatch)
    make_worker(phone="9100099062", ward="Mohali")
    token, _ = make_citizen(phone="9100000062")

    turn1 = _ask(client, token, "I want to file a complaint.")
    body1 = turn1.json()
    history = [
        ConversationTurn(role="user", content="I want to file a complaint.").model_dump(),
        ConversationTurn(role="assistant", content=body1["answer"]).model_dump(),
    ]

    turn2 = _ask(client, token, "Streetlight.", conversation_history=history)
    body2 = turn2.json()
    assert body2["follow_up_question"] == "What is the location?"
    history.append(ConversationTurn(role="user", content="Streetlight.").model_dump())
    history.append(ConversationTurn(role="assistant", content=body2["answer"]).model_dump())

    turn3 = _ask(client, token, "Use my current location.", conversation_history=history, location_text="Mohali")
    body3 = turn3.json()
    assert body3["routed_to"] == "NONE_AWAITING_CONFIRMATION"
    assert body3["service_category"] == "STREETLIGHTS"


# --- localization: the new message must actually go through translation for a non-English
# citizen, same as the pre-existing default message already did (see nodes.py's _localize) ---


def test_unrecognized_location_message_is_localized_for_non_english_citizen(client, monkeypatch, make_citizen):
    from unittest.mock import Mock

    fake_sarvam = Mock()
    fake_sarvam.translate = lambda text, source_language_code, target_language_code: f"[{target_language_code}] {text}"
    service = _real_ask_sarthi_service(sarvam_client=fake_sarvam)
    monkeypatch.setattr(ask_sarthi_module, "_service", service)

    token, _ = make_citizen(phone="9100000062")
    resp = _ask(client, token, _QUESTION, language="hi", location_text="Zzz Nonexistent Place")
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"].startswith("[")  # went through the fake translator, not left as raw English
    assert _UNRECOGNIZED in body["answer"].lower()  # the original English text is still in there, translated-wrapped
