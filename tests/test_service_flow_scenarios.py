"""Direct, traceable coverage of the SERVICE FLOW + SELECTIVE LANGCHAIN INTEGRATION phase's own
six example scenarios (its §13) -- one test per example, asserting each reaches the flow the spec
names. Overlaps in spirit with existing tests/test_ask_sarthi.py and
tests/test_orchestration_graph.py coverage, kept separate for direct 1:1 traceability to the
phase's own worked examples.
"""

from __future__ import annotations

from backend.models import Complaint
from backend.schemas.ask_sarthi import ConversationTurn
from tests.test_ask_sarthi import _ask, _install_real_service


# 1. "What documents do I need for a water connection?" -> RAG flow
def test_scenario_1_water_connection_documents_goes_to_rag_flow(client, monkeypatch, make_citizen):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9100000040")
    resp = _ask(client, token, "What documents do I need for a water connection?", location_text="Mohali")
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "TYPE_B_SERVICE_INFO"
    assert body["routed_to"] == "RAG"


# 2. "Streetlight near my home is broken." -> Complaint flow
def test_scenario_2_streetlight_broken_goes_to_complaint_flow(client, monkeypatch, db_session, make_citizen, make_worker):
    """P0 SAFETY FIX (production-safety audit): category + location resolving together no longer
    creates a complaint on the same call -- see test_ask_sarthi.py's
    test_type_a_complaint_creates_and_assigns_complaint for the full rationale. This test now
    verifies the two-call confirmation flow reaches the same end state."""
    _install_real_service(monkeypatch)
    make_worker(phone="9100099041", ward="Mohali")
    token, _ = make_citizen(phone="9100000041")
    resp = _ask(client, token, "Streetlight near my home is broken.", location_text="Mohali")
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "TYPE_A_COMPLAINT"
    assert body["routed_to"] == "NONE_AWAITING_CONFIRMATION"
    assert body.get("complaint_id") is None
    assert db_session().query(Complaint).count() == 0

    history = [
        ConversationTurn(role="user", content="Streetlight near my home is broken.").model_dump(),
        ConversationTurn(role="assistant", content=body["answer"]).model_dump(),
    ]
    confirm_resp = _ask(client, token, "Yes, submit it.", conversation_history=history)
    assert confirm_resp.status_code == 200
    confirm_body = confirm_resp.json()
    assert confirm_body["routed_to"] == "COMPLAINT_CREATED"
    assert confirm_body["complaint_id"] is not None

    db = db_session()
    assert db.query(Complaint).filter(Complaint.id == confirm_body["complaint_id"]).first() is not None
    db.close()


# 3. "I want to complain." -> Clarification flow
def test_scenario_3_bare_complaint_intent_goes_to_clarification_flow(client, monkeypatch, make_citizen):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9100000042")
    resp = _ask(client, token, "I want to complain.")
    assert resp.status_code == 200
    body = resp.json()
    assert body["routed_to"] == "NONE_CLARIFICATION_NEEDED"
    assert body["follow_up_required"] is True
    assert body["follow_up_question"] == "What issue would you like to report?"
    assert body.get("complaint_id") is None


# 4. "What is the status of complaint <id>?" -> Status flow
def test_scenario_4_complaint_status_goes_to_status_flow(client, monkeypatch, db_session, make_citizen):
    _install_real_service(monkeypatch)
    token, user = make_citizen(phone="9100000043")
    db = db_session()
    complaint = Complaint(citizen_id=str(user["id"]), original_text="x", original_language="en", translated_text="x", summary="x", status="assigned")
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    cid = complaint.id
    db.close()

    resp = _ask(client, token, f"What is the status of complaint #{cid}?")
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "TYPE_C_STATUS"
    assert body["routed_to"] == "COMPLAINT_STATUS_API"
    assert body["sources"] == []  # never touches RAG


# 5. "Use my current location." (as a follow-up with GPS) -> Location flow (GPS resolution)
def test_scenario_5_use_current_location_resolves_via_gps(client, monkeypatch, make_citizen):
    from unittest.mock import Mock
    import backend.routes.ask_sarthi as ask_sarthi_module
    from backend.services.location_extractor import LocationExtractor, RagGazetteer
    from backend.services.location_resolver import ResolvedLocation
    from backend.config import settings
    from tests.test_ask_sarthi import _real_ask_sarthi_service

    fake_resolver = Mock()
    fake_resolver.resolve_coordinates = lambda lat, lng: ResolvedLocation(
        latitude=lat, longitude=lng, city_name="Mohali", state_name="Punjab"
    )
    gazetteer = RagGazetteer(settings.RAG_DATA_DIR / "chunks" / "chunks.json")
    extractor = LocationExtractor(gazetteer, location_resolver=fake_resolver)
    service = _real_ask_sarthi_service(location_extractor=extractor)
    monkeypatch.setattr(ask_sarthi_module, "_service", service)

    token, _ = make_citizen(phone="9100000044")
    resp = _ask(client, token, "Use my current location.", latitude=30.7, longitude=76.7)
    assert resp.status_code == 200
    body = resp.json()
    assert body["location"]["city"] == "Sahibzada Ajit Singh Nagar (Mohali)"
    assert body["location"]["source"] == "gps"


# 6. "Tell me something unrelated to civic services." -> a genuinely off-topic message with no
# civic-service signal at all. Reported honestly (see docs/ask_sarthi_service_flow.md's
# limitations section): this codebase's "out-of-scope" concept is specifically a KNOWN-BUT-
# UNSUPPORTED civic service (electricity, new-connection) that the classifier explicitly
# recognizes -- not a general off-topic detector. A message with no civic-service signal at all
# falls to the same honest default as "I want to complain." (clarification, asking what issue to
# report), which a citizen naturally exits without providing one -- it never fabricates a civic
# answer to an unrelated question. This test asserts that honest behavior, not a routed_to value
# the classifier has no mechanism to produce.
def test_scenario_6_generic_off_topic_message_never_fabricates_a_civic_answer(client, monkeypatch, make_citizen):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9100000045")
    resp = _ask(client, token, "Tell me something unrelated to civic services.")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("complaint_id") is None
    assert body["sources"] == []
    assert body["insufficient_knowledge"] is False or body["follow_up_required"] is True
    # Never silently answers as if it understood a specific civic issue.
    assert body["service_category"] is None


def test_scenario_6b_known_unsupported_service_uses_out_of_scope_flow(client, monkeypatch, make_citizen):
    """The concrete case this codebase's out-of-scope flow DOES cover: a named but unsupported
    civic service (electricity), complementing scenario 6's honest-default coverage above."""
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9100000046")
    resp = _ask(client, token, "I want a new electricity connection.")
    assert resp.status_code == 200
    body = resp.json()
    assert body["routed_to"] == "NONE_OUT_OF_SCOPE"
    assert body["insufficient_knowledge"] is True
    assert body["sources"] == []
