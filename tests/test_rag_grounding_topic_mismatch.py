"""RAG QUALITY-GATE regression tests: the Bhubaneswar pothole case (live-reproduced -- a citizen
asking "How do I report a pothole in Bhubaneswar, Odisha?" got a fabricated, Bhubaneswar-specific
pothole-reporting procedure invented from Odisha's real, VERIFIED state-wide road-cutting-
permission/restoration records, which do not actually cover ordinary pothole complaints).

Root cause: category+location filtering was CORRECT (ROADS_POTHOLES is this KB's general "roads"
bucket; the two Odisha records are genuinely, correctly retrieved as ROADS_POTHOLES/Odisha
evidence) -- but the answer-generation prompt received ONLY each chunk's raw `content` text,
dropping the `sub_service`/`verification_status` metadata already sitting on the same
`ScoredChunk`. Given a query about REPORTING a pothole and a chunk about getting PERMISSION to cut
a road, the LLM had no signal that these are different sub-services within the same broad
category, and answered anyway.

Fix: `backend/services/rag_retriever.py`'s `chunk_context_label()` builds a short
"VERIFIED | Topic: <sub_service>" label per chunk (reusing metadata already authored on every
KnowledgeRecord -- no new data, no keyword list); `AnswerGenerationService.generate()` accepts it
as an optional `context_labels` param and folds it into the LLM prompt only (never into the
citizen-facing fallback template -- see `_fallback_answer`).

These tests are deterministic (real, checked-in Chroma index; no live Sarvam call -- matches this
codebase's established pattern, see test_ask_sarthi.py's own module docstring). The actual live
LLM grounding behavior (does the real model now decline instead of hallucinating) was verified
manually against the real Sarvam API as part of this fix and is reported in the fix's own writeup,
not re-run here on every CI run.
"""
from __future__ import annotations

from unittest.mock import Mock

from backend.schemas.rag_knowledge import ServiceCategory
from backend.services.answer_generation_service import AnswerGenerationService
from backend.services.rag_retriever import RagRetriever, chunk_context_label
from backend.services.vector_store import ScoredChunk
from tests.test_ask_sarthi import _get_shared_chroma_deps


def _retriever() -> RagRetriever:
    store, provider = _get_shared_chroma_deps()
    from backend.config import settings
    return RagRetriever(store, provider, top_k=settings.RAG_TOP_K, relevance_threshold=settings.RAG_EMBEDDING_RELEVANCE_THRESHOLD)


def _mock_llm_answer_service(captured_prompts: list[str], reply: str = "OK") -> AnswerGenerationService:
    """A real AnswerGenerationService with its Sarvam client swapped for a Mock -- matches this
    codebase's established `client._client = fake_sdk` pattern (see test_sarvam_client.py)."""
    svc = AnswerGenerationService.__new__(AnswerGenerationService)
    fake_client = Mock()
    fake_response = Mock()
    fake_response.choices = [Mock(message=Mock(content=reply), finish_reason="stop")]

    def _capture(**kwargs):
        captured_prompts.append(kwargs["messages"][1]["content"])
        return fake_response

    fake_client.chat.completions = Mock(side_effect=_capture)
    svc._client = fake_client
    return svc


# ============================================================================
# 1. chunk_context_label -- pure metadata formatting, the core of the fix.
# ============================================================================


def test_chunk_context_label_surfaces_sub_service_and_verification_status():
    chunk = ScoredChunk(
        chunk_id="c1", score=0.9,
        metadata={
            "verification_status": "VERIFIED",
            "sub_service": "Permission to cut a municipal road (e.g. for utility or pipeline work)",
            "content": "irrelevant for this test",
        },
    )
    label = chunk_context_label(chunk)
    assert label == "VERIFIED | Topic: Permission to cut a municipal road (e.g. for utility or pipeline work)"


def test_chunk_context_label_distinguishes_permission_from_pothole_repair_sub_service():
    """The exact distinction the Bhubaneswar bug hinged on: two chunks in the SAME
    service_category must not produce the same label when their sub_service differs."""
    permission_chunk = ScoredChunk(
        chunk_id="c1", score=0.9,
        metadata={"verification_status": "VERIFIED", "sub_service": "Permission to cut a municipal road", "content": "x"},
    )
    pothole_chunk = ScoredChunk(
        chunk_id="c2", score=0.9,
        metadata={"verification_status": "VERIFIED", "sub_service": "Pothole / road surface damage repair", "content": "x"},
    )
    assert chunk_context_label(permission_chunk) != chunk_context_label(pothole_chunk)


# ============================================================================
# 2. AnswerGenerationService wiring -- proves the label actually reaches the LLM prompt, and
# never leaks into the no-LLM fallback template shown to citizens.
# ============================================================================


def test_generate_includes_context_labels_in_the_llm_prompt_when_given():
    captured: list[str] = []
    svc = _mock_llm_answer_service(captured)
    svc.generate(
        "How do I report a pothole in Bhubaneswar, Odisha?",
        ["Complaint channels: In person at the ULB's engineering section."],
        "English",
        context_labels=["VERIFIED | Topic: Permission to cut a municipal road"],
    )
    assert len(captured) == 1
    assert "VERIFIED | Topic: Permission to cut a municipal road" in captured[0]
    assert "Complaint channels: In person at the ULB's engineering section." in captured[0]


def test_generate_without_context_labels_still_works_unchanged():
    """Backward compatible: an older/other caller not yet passing context_labels behaves exactly
    as before this fix -- the raw chunk content reaches the prompt with no label wrapper around
    it (no stray "[...]" line the caller never asked for)."""
    captured: list[str] = []
    svc = _mock_llm_answer_service(captured)
    answer, was_llm, _token_usage = svc.generate("A question", ["Some chunk content."], "English")
    assert was_llm is True
    assert answer == "OK"
    assert "Some chunk content." in captured[0]
    assert "Topic:" not in captured[0]


def test_fallback_answer_never_leaks_the_context_label_to_the_citizen():
    """When no LLM is configured, the fallback template echoes chunk CONTENT verbatim -- the
    internal "[VERIFIED | Topic: ...]" label must never appear in that citizen-facing text."""
    svc = AnswerGenerationService.__new__(AnswerGenerationService)
    svc._client = None
    content_chunks = ["Complaint channels: In person at the ULB's engineering section."]
    answer, was_llm, _token_usage = svc.generate(
        "How do I report a pothole in Bhubaneswar, Odisha?", content_chunks, "English",
        context_labels=["VERIFIED | Topic: Permission to cut a municipal road"],
    )
    assert was_llm is False
    assert answer == content_chunks[0]
    assert "Topic:" not in answer
    assert "VERIFIED |" not in answer


def test_fallback_answer_skips_a_leading_faq_chunk_for_a_proper_looking_response():
    """LIVE PRODUCT FINDING: a knowledge record's FAQ chunk (deterministically rendered as
    "Q: <question> A: <answer>" -- see build_rag_knowledge_base.py's chunk_document()) sometimes
    scores highest and used to be echoed verbatim as the whole "answer" -- reading as an answer to
    a completely different question ("is this data verified?") than the one the citizen actually
    asked ("How long does a streetlight repair take?"), never a proper-looking response. The
    fallback must skip it and use the first substantive chunk instead, when one exists."""
    svc = AnswerGenerationService.__new__(AnswerGenerationService)
    svc._client = None
    context_chunks = [
        "Q: Is this the official SLA for street light repair in Bengaluru? A: No -- this is a "
        "synthetic, representative record.",
        "Representative service description for street light complaints in Bengaluru, Karnataka.",
        "Required information: Locality / pole number, if known; Phone number of the complainant",
    ]
    answer, was_llm, _token_usage = svc.generate("How long does a streetlight repair take?", context_chunks, "English")
    assert was_llm is False
    assert answer == context_chunks[1]
    assert not answer.lstrip().startswith("Q: ")


def test_fallback_answer_still_shows_the_faq_chunk_when_nothing_else_was_retrieved():
    """Control: an FAQ chunk is still better than no answer at all when it's the ONLY thing
    retrieved -- the skip-FAQ preference must never turn into an empty/missing answer."""
    svc = AnswerGenerationService.__new__(AnswerGenerationService)
    svc._client = None
    context_chunks = ["Q: Is this verified? A: No, this is a synthetic record."]
    answer, was_llm, _token_usage = svc.generate("Some question", context_chunks, "English")
    assert was_llm is False
    assert answer == context_chunks[0]


# ============================================================================
# 3. Retrieval-layer cross-location behavior (real, checked-in Chroma index; no LLM call) --
# CITY-SPECIFIC / STATE-LEVEL / no-contamination, per the Bhubaneswar bug's own investigation.
# ============================================================================


def test_bhubaneswar_state_level_query_returns_only_odisha_evidence_no_contamination():
    """Bhubaneswar has no city-tagged record in this KB (see the Odisha statewide source's own
    coverage notes) -- the query correctly falls back to genuine STATE-LEVEL Odisha evidence, not
    silently empty and not contaminated by another state/city's chunks."""
    outcome = _retriever().retrieve(
        "How do I report a pothole in Bhubaneswar, Odisha?", ServiceCategory.ROADS_POTHOLES, None, "Odisha",
    )
    assert not outcome.insufficient_knowledge
    assert outcome.results
    for r in outcome.results:
        assert r.metadata.get("state") == "Odisha"
        assert r.metadata.get("city") is None
        assert r.metadata.get("geographic_scope") == "STATE"


def test_bhubaneswar_query_never_returns_a_different_states_city_specific_chunk():
    outcome = _retriever().retrieve(
        "How do I report a pothole in Bhubaneswar, Odisha?", ServiceCategory.ROADS_POTHOLES, None, "Odisha",
    )
    cities = {r.metadata.get("city") for r in outcome.results}
    assert cities in ({None}, set())


def test_patna_pothole_query_returns_only_patna_chunks():
    outcome = _retriever().retrieve("How do I report a pothole in Patna?", ServiceCategory.ROADS_POTHOLES, "Patna", None)
    assert not outcome.insufficient_knowledge
    assert outcome.results
    assert {r.metadata.get("city") for r in outcome.results} == {"Patna"}


def test_vijayawada_pothole_query_returns_only_vijayawada_chunks():
    outcome = _retriever().retrieve(
        "How do I report a pothole in Vijayawada?", ServiceCategory.ROADS_POTHOLES, "Vijayawada", None,
    )
    assert not outcome.insufficient_knowledge
    assert outcome.results
    assert {r.metadata.get("city") for r in outcome.results} == {"Vijayawada"}


def test_mohali_pothole_query_with_canonical_city_name_returns_only_mohali_chunks():
    """Uses the CANONICAL gazetteer name (as location_node/LocationExtractor actually resolves
    plain "Mohali" to before calling the retriever in the real pipeline -- see
    location_extractor.py's _CITY_ALIASES) -- this is what production actually passes, not a raw
    "Mohali" string (RagRetriever does an exact metadata match; see this test module's own notes
    on the retriever's city-filter contract for the known, non-production-reachable gap in
    calling it directly with an informal name)."""
    outcome = _retriever().retrieve(
        "How do I report a pothole in Mohali?", ServiceCategory.ROADS_POTHOLES,
        "Sahibzada Ajit Singh Nagar (Mohali)", None,
    )
    assert not outcome.insufficient_knowledge
    assert outcome.results
    assert {r.metadata.get("city") for r in outcome.results} == {"Sahibzada Ajit Singh Nagar (Mohali)"}
    assert all(r.metadata.get("service_id", "").startswith("ROADS_") for r in outcome.results)


def test_patiala_waste_query_returns_only_patiala_chunks():
    outcome = _retriever().retrieve(
        "How do I report garbage collection issues in Patiala?", ServiceCategory.WASTE_SANITATION, "Patiala", None,
    )
    assert not outcome.insufficient_knowledge
    assert outcome.results
    assert {r.metadata.get("city") for r in outcome.results} == {"Patiala"}


def test_odisha_state_level_evidence_still_answers_a_question_it_genuinely_covers():
    """Control: the fix must not make the system refuse EVERYTHING from a state-level source --
    a question actually about road-cutting permission (what these Odisha records genuinely cover)
    must still retrieve them. Guards against "fixing" the Bhubaneswar case by making state-level
    evidence unusable in general."""
    outcome = _retriever().retrieve(
        "What is the procedure to get permission to cut a road in Odisha for laying a pipeline?",
        ServiceCategory.ROADS_POTHOLES, None, "Odisha",
    )
    assert not outcome.insufficient_knowledge
    assert outcome.results
    assert any(r.metadata.get("service_id") == "ROADS_ROAD_CUTTING_PERMISSION" for r in outcome.results)
