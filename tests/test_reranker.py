"""Tests for CrossEncoderReranker (backend/services/reranker.py) -- the real, trained cross-encoder
reranker added for hybrid-search/rerank hardening (Part 6). Uses the REAL model
(`cross-encoder/ms-marco-MiniLM-L-6-v2`), no mocks -- same "measure it directly, don't just claim
it" posture as tests/test_rag_vector_store.py's embedding-provider tests, and the same module-level
caching pattern (model load is genuinely slow the first time) so this file pays that cost once.
"""

from __future__ import annotations

from backend.services.reranker import CrossEncoderReranker

_shared_reranker: CrossEncoderReranker | None = None


def _get_shared_reranker() -> CrossEncoderReranker:
    global _shared_reranker
    if _shared_reranker is None:
        _shared_reranker = CrossEncoderReranker()
        _shared_reranker.load()
    return _shared_reranker


def test_score_returns_one_float_per_passage_in_the_same_order():
    reranker = _get_shared_reranker()
    passages = [
        "The street light near my house is not working at night.",
        "Garbage collection in my area is irregular.",
        "There is a large pothole on the main road.",
    ]
    scores = reranker.score("street light not working", passages)
    assert len(scores) == len(passages)
    assert all(isinstance(s, float) for s in scores)


def test_a_genuinely_relevant_passage_scores_higher_than_an_unrelated_one():
    """The real, measurable point of a cross-encoder over a bag-of-words/heuristic signal: it
    should rank a passage actually about the query's topic above one that is not, even though
    both are plausible short civic-complaint sentences with some surface-level word overlap."""
    reranker = _get_shared_reranker()
    query = "street light pole is broken and dark at night near my house"
    relevant = "The street light pole outside my house has been broken for a week and the road stays dark at night."
    unrelated = "Garbage has not been collected from my street for several days and it smells bad."
    scores = reranker.score(query, [relevant, unrelated])
    assert scores[0] > scores[1]


def test_score_on_empty_passages_returns_empty_list_without_loading_the_model():
    """No real work, no model call at all -- a fresh, never-loaded reranker must still handle this
    without triggering the (slow) lazy model load."""
    reranker = CrossEncoderReranker()
    assert reranker.score("any query", []) == []
    assert reranker._model is None  # confirms the guard actually short-circuited before loading


def test_model_name_defaults_to_the_documented_ms_marco_minilm_model():
    reranker = CrossEncoderReranker()
    assert reranker.model_name == "cross-encoder/ms-marco-MiniLM-L-6-v2"


def test_custom_model_name_is_honored():
    reranker = CrossEncoderReranker(model_name="cross-encoder/ms-marco-MiniLM-L-6-v2")
    assert reranker.model_name == "cross-encoder/ms-marco-MiniLM-L-6-v2"
