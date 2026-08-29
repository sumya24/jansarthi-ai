"""End-to-end proof that rag_flow_node actually uses the RAG answer cache (see
backend/services/rag_answer_cache.py / models.RagAnswerCache) -- not just a unit test of the
cache module in isolation. Real Chroma retrieval, a fake (but was_llm_generated=True) answer
service standing in for Sarvam, matching this codebase's established no-network-call pattern
(see test_ask_janmitra.py's own module docstring)."""
from __future__ import annotations

from unittest.mock import Mock

from backend.models import RagAnswerCache
from backend.services.ask_janmitra_service import AskJanMitraService
from tests.test_ask_janmitra import _FakeComplaintAgent, _get_shared_chroma_deps, _ask


def _install_llm_answer_service(monkeypatch, db_session) -> Mock:
    """Like test_ask_janmitra.py's own _install_real_service, but the fake answer service reports
    was_llm_generated=True -- required to exercise the cache-write path at all (rag_flow_node
    deliberately never caches the was_llm_generated=False fallback, see that node's own comment)."""
    import backend.routes.ask_janmitra as ask_janmitra_module

    store, provider = _get_shared_chroma_deps()
    fake_answers = Mock()
    fake_answers.generate = Mock(side_effect=lambda q, chunks, lang, context_labels=None: (f"LLM ANSWER: {q}", True, None))
    service = AskJanMitraService(
        vector_store=store, embedding_provider=provider,
        answer_service=fake_answers, complaint_agent=_FakeComplaintAgent(),
    )
    monkeypatch.setattr(ask_janmitra_module, "_service", service)
    return fake_answers


def test_second_identical_question_never_calls_the_answer_service_again(client, monkeypatch, db_session, make_citizen):
    fake_answers = _install_llm_answer_service(monkeypatch, db_session)
    token, _ = make_citizen(phone="9600000301")

    first = _ask(client, token, "How long does a streetlight repair take?", location_text="Mohali")
    second = _ask(client, token, "How long does a streetlight repair take?", location_text="Mohali")

    assert first.status_code == 200 and second.status_code == 200
    body1, body2 = first.json(), second.json()
    assert body1["answer"] == body2["answer"]
    assert body1["answer_was_llm_generated"] is True
    assert body2["answer_was_llm_generated"] is True
    fake_answers.generate.assert_called_once()  # NOT called again for the second, identical ask

    db = db_session()
    assert db.query(RagAnswerCache).count() == 1
    db.close()


def test_same_question_different_city_gets_its_own_cache_entry_not_a_collision(client, monkeypatch, db_session, make_citizen):
    fake_answers = _install_llm_answer_service(monkeypatch, db_session)
    token, _ = make_citizen(phone="9600000302")

    mohali = _ask(client, token, "How long does a streetlight repair take?", location_text="Mohali")
    patiala = _ask(client, token, "How long does a streetlight repair take?", location_text="Patiala")

    assert mohali.status_code == 200 and patiala.status_code == 200
    assert fake_answers.generate.call_count == 2  # a genuinely different context -- both real calls

    db = db_session()
    assert db.query(RagAnswerCache).count() == 2
    db.close()


def test_no_llm_fallback_answer_is_never_cached(client, monkeypatch, db_session, make_citizen):
    """The standard fake (was_llm_generated=False, matching a real Sarvam-unavailable fallback)
    must never populate the cache -- otherwise a temporary outage would freeze a degraded answer
    in for everyone who asks the same thing later."""
    from tests.test_ask_janmitra import _install_real_service
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9600000303")

    _ask(client, token, "How long does a streetlight repair take?", location_text="Mohali")

    db = db_session()
    assert db.query(RagAnswerCache).count() == 0
    db.close()
