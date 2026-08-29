"""Unit tests for RagRetriever's cross-lingual VERIFIED-rescue mechanism (backend/services/
rag_retriever.py) -- added after a live report: the exact same civic-info question about
Bengaluru's water supply, asked in Marathi script, returned a generic SYNTHETIC placeholder
instead of the real VERIFIED BWSSB record, purely because the real record's embedding score for
that non-English-script query (0.75-0.79) fell just under RAG_EMBEDDING_RELEVANCE_THRESHOLD
(0.79) while a topically-generic SYNTHETIC chunk for the same city+category cleared it easily.

Crucially, the user explicitly asked whether the fix was "just for Bangalore" and required it
work for ANY city, since a citizen can ask about any city in any supported language. These tests
use fake vector-store data for a DIFFERENT city than the one in the live report (Chennai, not
Bengaluru) specifically to prove the mechanism has no city-specific branching -- it operates
purely on each chunk's own `verification_status`/`service_category`/`city` metadata and score,
so it applies identically regardless of which city or category triggered it.

See tests/test_ask_janmitra.py's own `test_synthetic_source_suppressed_when_verified_covers_the_
same_city_and_category` for the end-to-end (real ChromaDB) citation-honesty coverage this
complements -- these tests isolate the retriever's threshold/rescue arithmetic with fully
controlled scores, since the checked-in knowledge base doesn't happen to contain a second
naturally-occurring case where every VERIFIED chunk for a city+category scores below the main
threshold while a SYNTHETIC one clears it.
"""

import pytest

from backend.schemas.rag_knowledge import ServiceCategory
from backend.services.rag_retriever import RagRetriever
from backend.services.vector_store import ScoredChunk


class _FakeEmbeddingProvider:
    def embed_query(self, text: str) -> str:
        return text


class _FakeVectorStore:
    """Returns a fixed, pre-scored candidate list regardless of the query vector or top_k --
    lets these tests dictate exact scores instead of depending on real embedding-model output.
    `get_candidates()` returns an empty pool by default (no BM25 widening -- see this module's
    hybrid-search tests further down for a store that actually exercises that path), so all the
    threshold/rescue/rerank/citation tests above are unaffected by hybrid search's addition."""

    def __init__(self, candidates: list[ScoredChunk]) -> None:
        self._candidates = candidates

    def search(self, query_vector, top_k: int, metadata_filter: dict[str, str] | None) -> list[ScoredChunk]:
        return list(self._candidates)

    def get_candidates(self, metadata_filter: dict[str, str] | None) -> list[tuple]:
        return []

    def get_candidate_texts(self, metadata_filter: dict[str, str] | None) -> list[tuple]:
        return []

    def get_embeddings(self, chunk_ids: list[str]) -> dict:
        return {}


def _chunk(chunk_id: str, score: float, city: str, category: str, status: str) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=chunk_id,
        score=score,
        metadata={
            "source_id": chunk_id,
            "city": city,
            "service_category": category,
            "verification_status": status,
        },
    )


def _retriever(candidates: list[ScoredChunk]) -> RagRetriever:
    return RagRetriever(
        _FakeVectorStore(candidates),
        _FakeEmbeddingProvider(),
        relevance_threshold=0.79,
        verified_relevance_threshold=0.74,
    )


def test_rescue_and_suppression_generalize_to_a_city_other_than_the_reported_one():
    """Chennai, not Bengaluru -- same shape as the live-reported bug (a below-main-threshold
    VERIFIED chunk competing with an above-threshold SYNTHETIC one for the same city+category),
    proving the mechanism isn't a one-city patch. The real VERIFIED chunk must win outright:
    rescued into the result set, and the competing SYNTHETIC chunk suppressed entirely."""
    candidates = [
        _chunk("SYNTHETIC_REPRESENTATIVE_DATA", 0.85, "Chennai", "ROADS_POTHOLES", "SYNTHETIC"),
        _chunk("TN_GCC_REAL_RECORD", 0.76, "Chennai", "ROADS_POTHOLES", "VERIFIED"),
    ]
    outcome = _retriever(candidates).retrieve(
        "query", ServiceCategory.ROADS_POTHOLES, "Chennai", "Tamil Nadu"
    )
    assert not outcome.insufficient_knowledge
    source_ids = {c.metadata["source_id"] for c in outcome.results}
    assert source_ids == {"TN_GCC_REAL_RECORD"}


def test_rescue_never_applies_to_synthetic_chunks():
    """The lower floor exists to rescue real content, never to admit a SYNTHETIC chunk that
    itself falls short of the main threshold -- otherwise this would be a blanket threshold
    relaxation, not a verified-only rescue."""
    candidates = [
        _chunk("SYNTHETIC_LOW_SCORE", 0.76, "Pune", "WASTE_SANITATION", "SYNTHETIC"),
    ]
    outcome = _retriever(candidates).retrieve(
        "query", ServiceCategory.WASTE_SANITATION, "Pune", "Maharashtra"
    )
    assert outcome.insufficient_knowledge
    assert outcome.results == []


def test_verified_chunk_below_the_rescue_floor_is_still_not_rescued():
    """The rescue floor is a real floor, not a disguised removal of the threshold -- a VERIFIED
    chunk scoring below even the lower floor must still be dropped."""
    candidates = [
        _chunk("TOO_LOW_EVEN_FOR_RESCUE", 0.70, "Pune", "WASTE_SANITATION", "VERIFIED"),
    ]
    outcome = _retriever(candidates).retrieve(
        "query", ServiceCategory.WASTE_SANITATION, "Pune", "Maharashtra"
    )
    assert outcome.insufficient_knowledge


def test_two_verified_records_for_the_same_city_and_category_both_survive():
    """The citation-honesty suppression is scoped to (city, service_category) vs verification
    status, not source_id -- two distinct VERIFIED records for the same city+category (a
    legitimate, pre-existing case elsewhere in this KB) must both survive rescue+suppression,
    not have one incorrectly treated as redundant."""
    candidates = [
        _chunk("KA_BBMP_SWM_BYELAWS_2020", 0.80, "Bengaluru", "WASTE_SANITATION", "VERIFIED"),
        _chunk("KA_SECOND_VERIFIED_RECORD", 0.75, "Bengaluru", "WASTE_SANITATION", "VERIFIED"),
        _chunk("SYNTHETIC_REPRESENTATIVE_DATA", 0.85, "Bengaluru", "WASTE_SANITATION", "SYNTHETIC"),
    ]
    outcome = _retriever(candidates).retrieve(
        "query", ServiceCategory.WASTE_SANITATION, "Bengaluru", "Karnataka"
    )
    source_ids = {c.metadata["source_id"] for c in outcome.results}
    assert source_ids == {"KA_BBMP_SWM_BYELAWS_2020", "KA_SECOND_VERIFIED_RECORD"}


def test_rescue_does_not_cross_contaminate_a_different_city_or_category():
    """Rescue/suppression must stay scoped to the exact (city, service_category) pair -- a
    VERIFIED rescue for one city+category must never suppress a SYNTHETIC chunk belonging to a
    different city or a different category, even within the same result set."""
    candidates = [
        _chunk("TN_GCC_REAL_RECORD", 0.76, "Chennai", "ROADS_POTHOLES", "VERIFIED"),
        _chunk("SYNTHETIC_REPRESENTATIVE_DATA", 0.85, "Chennai", "ROADS_POTHOLES", "SYNTHETIC"),
        _chunk("SYNTHETIC_OTHER_CITY", 0.85, "Coimbatore", "ROADS_POTHOLES", "SYNTHETIC"),
    ]
    outcome = _retriever(candidates).retrieve(
        "query", ServiceCategory.ROADS_POTHOLES, None, "Tamil Nadu"
    )
    source_ids = {c.metadata["source_id"] for c in outcome.results}
    assert "TN_GCC_REAL_RECORD" in source_ids
    assert "SYNTHETIC_REPRESENTATIVE_DATA" not in source_ids
    assert "SYNTHETIC_OTHER_CITY" in source_ids


# --- Hybrid search (BM25 widening the candidate pool) -----------------------------------------
#
# See rag_retriever.py's module docstring for the design: BM25 only ever WIDENS the candidate
# pool with chunks pure vector search's fixed top_k*3 window didn't return at all -- it never
# supplies the final score. Every chunk BM25 adds still gets a REAL cosine similarity against the
# query before the existing threshold/rescue/rerank/citation logic runs, completely unaware
# hybrid search happened at all.


class _FakeVectorStoreWithPool:
    """Unlike `_FakeVectorStore` above, `search()` and `get_candidates()` are independently
    controllable here -- lets these tests simulate a chunk that pure vector search's top_k*3
    window genuinely never returned (simply absent from `search_results`) while still being
    present in the full metadata-filtered pool `get_candidates()` exposes, exactly the situation
    BM25 widening exists to catch."""

    def __init__(self, search_results: list[ScoredChunk], pool: list[tuple]) -> None:
        self._search_results = search_results
        self._pool = pool

    def search(self, query_vector, top_k: int, metadata_filter: dict[str, str] | None) -> list[ScoredChunk]:
        return list(self._search_results)

    def get_candidates(self, metadata_filter: dict[str, str] | None) -> list[tuple]:
        return list(self._pool)

    def get_candidate_texts(self, metadata_filter: dict[str, str] | None) -> list[tuple]:
        return [(chunk_id, content, metadata) for chunk_id, content, metadata, _vector in self._pool]

    def get_embeddings(self, chunk_ids: list[str]) -> dict:
        wanted = set(chunk_ids)
        return {chunk_id: vector for chunk_id, _content, _metadata, vector in self._pool if chunk_id in wanted}


class _FixedVectorEmbeddingProvider:
    """Returns the same real (dense, list-of-floats) vector for every query -- lets these tests
    control the exact cosine similarity `cosine_similarity_any` computes against each pool
    chunk's own fixed vector, rather than depending on real embedding-model output."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    def embed_query(self, text: str) -> list[float]:
        return self._vector


def test_bm25_surfaces_an_exact_keyword_match_pure_vector_search_missed():
    """BM25_ONLY_HIT contains the query's exact, distinctive terms ("pole number 42") but is
    absent from the fake store's search() results entirely (simulating vector search's top_k*3
    ANN window missing it) -- it must still appear in the final results, via BM25 widening, and
    with its REAL cosine similarity (0.85, from its fixed vector) as its score, not a BM25 score."""
    vector_hit = _chunk("VECTOR_HIT", 0.90, "Nashik", "STREETLIGHTS", "VERIFIED")
    pool = [
        ("VECTOR_HIT", "general streetlight complaint information", vector_hit.metadata, [1.0, 0.0]),
        (
            "BM25_ONLY_HIT",
            "streetlight pole number 42 is broken near the market",
            {
                "source_id": "BM25_ONLY_HIT", "city": "Nashik",
                "service_category": "STREETLIGHTS", "verification_status": "VERIFIED",
            },
            [0.85, 0.5268026970889531],  # unit vector, cosine 0.85 against [1.0, 0.0]
        ),
        (
            "UNRELATED_LOW_SCORE",
            "garbage collection schedule for residential areas",
            {
                "source_id": "UNRELATED_LOW_SCORE", "city": "Nashik",
                "service_category": "STREETLIGHTS", "verification_status": "SYNTHETIC",
            },
            [0.1, 0.9949874371066199],  # cosine 0.1 against [1.0, 0.0] -- irrelevant filler
        ),
    ]
    store = _FakeVectorStoreWithPool(search_results=[vector_hit], pool=pool)
    retriever = RagRetriever(
        store, _FixedVectorEmbeddingProvider([1.0, 0.0]),
        relevance_threshold=0.79, verified_relevance_threshold=0.74,
    )

    outcome = retriever.retrieve(
        "streetlight pole number 42 is broken", ServiceCategory.STREETLIGHTS, "Nashik", "Maharashtra"
    )

    assert not outcome.insufficient_knowledge
    source_ids = {c.metadata["source_id"] for c in outcome.results}
    assert "BM25_ONLY_HIT" in source_ids
    assert "UNRELATED_LOW_SCORE" not in source_ids
    bm25_result = next(c for c in outcome.results if c.metadata["source_id"] == "BM25_ONLY_HIT")
    assert bm25_result.score == pytest.approx(0.85, abs=1e-6)


def test_bm25_widened_higher_scoring_candidate_correctly_outranks_a_lower_verified_one():
    """BUG FIX (code review): the VERIFIED-preference band used to be anchored to
    `above_threshold[0].score`, which was always the true top score back when the list came
    straight out of `store.search()` (sorted). Hybrid search appends BM25-widened candidates --
    with their own real cosine scores, which can legitimately be HIGHER than every vector-search
    score -- to the END of that list, so `above_threshold[0]` is no longer guaranteed to be the
    top score.

    Under the old bug: top_score anchors to VECTOR_TOP_BUT_LOWER's own score (0.79, position 0),
    so it trivially satisfies its OWN "near-top" floor and wins the VERIFIED bonus -- outranking
    the genuinely higher-scoring BM25_WIDENED_HIGHEST (0.95, SYNTHETIC, never eligible for the
    bonus) even though 0.79 is nowhere near the TRUE top score. Fixed: the band anchors to the
    real max (0.95), so 0.79 no longer qualifies and the higher-scoring chunk correctly wins.

    (Different cities on the two chunks are deliberate test-isolation, not part of the bug itself
    -- keeps this test's citation-honesty filtering trivially a no-op, since that mechanism is
    already covered by its own dedicated tests above and isn't what this test is about. The
    filler chunks below are load-bearing, not decoration: with only the two chunks under test,
    BM25's classic IDF formula goes NEGATIVE for a term appearing in every document in a
    too-tiny corpus -- a real BM25 math quirk, unrelated to the bug this test targets -- so
    enough unrelated filler is included to keep the corpus large enough for BM25 to behave
    the way it does on this app's real, hundreds-of-chunks-sized pools.)"""
    vector_top_but_lower = _chunk("VECTOR_TOP_BUT_LOWER", 0.79, "Nashik", "STREETLIGHTS", "VERIFIED")
    pool = [
        ("VECTOR_TOP_BUT_LOWER", "general streetlight complaint information", vector_top_but_lower.metadata, [1.0, 0.0]),
        (
            "BM25_WIDENED_HIGHEST",
            "streetlight pole number 42 is broken near the market",
            {
                "source_id": "BM25_WIDENED_HIGHEST", "city": "OtherCity",
                "service_category": "STREETLIGHTS", "verification_status": "SYNTHETIC",
            },
            [0.95, 0.3122498999199199],  # unit vector, cosine 0.95 against [1.0, 0.0]
        ),
        ("FILLER_1", "garbage collection schedule for residential areas", {"source_id": "FILLER_1", "city": "OtherCity", "service_category": "STREETLIGHTS", "verification_status": "SYNTHETIC"}, [0.01, 0.01]),
        ("FILLER_2", "water supply timings for this ward", {"source_id": "FILLER_2", "city": "OtherCity", "service_category": "STREETLIGHTS", "verification_status": "SYNTHETIC"}, [0.01, 0.01]),
        ("FILLER_3", "park maintenance report for the month", {"source_id": "FILLER_3", "city": "OtherCity", "service_category": "STREETLIGHTS", "verification_status": "SYNTHETIC"}, [0.01, 0.01]),
    ]
    store = _FakeVectorStoreWithPool(search_results=[vector_top_but_lower], pool=pool)
    retriever = RagRetriever(
        store, _FixedVectorEmbeddingProvider([1.0, 0.0]),
        relevance_threshold=0.79, verified_relevance_threshold=0.74,
    )

    outcome = retriever.retrieve(
        "streetlight pole number 42 is broken", ServiceCategory.STREETLIGHTS, "Nashik", "Maharashtra"
    )

    result_ids = [c.metadata["source_id"] for c in outcome.results]
    assert result_ids[0] == "BM25_WIDENED_HIGHEST", (
        "the real highest-scoring chunk must rank first -- if VECTOR_TOP_BUT_LOWER (0.79) ranks "
        "first instead, the VERIFIED-preference band is still anchored to the wrong (stale, "
        "position-0) top score instead of the true max"
    )


def test_bm25_widened_candidate_still_has_to_clear_the_real_relevance_threshold():
    """Widening the pool must never bypass the threshold -- a chunk BM25 finds via an exact
    keyword match but whose REAL cosine similarity to the query is low must still be dropped,
    exactly like any vector-search candidate that scores too low."""
    vector_hit = _chunk("VECTOR_HIT", 0.90, "Nashik", "STREETLIGHTS", "VERIFIED")
    pool = [
        ("VECTOR_HIT", "general streetlight complaint information", vector_hit.metadata, [1.0, 0.0]),
        (
            "BM25_MATCH_BUT_LOW_COSINE",
            "streetlight pole number 42 is broken near the market",
            {
                "source_id": "BM25_MATCH_BUT_LOW_COSINE", "city": "Nashik",
                "service_category": "STREETLIGHTS", "verification_status": "VERIFIED",
            },
            [0.3, 0.9539392014169456],  # cosine 0.3 against [1.0, 0.0] -- below threshold
        ),
    ]
    store = _FakeVectorStoreWithPool(search_results=[vector_hit], pool=pool)
    retriever = RagRetriever(
        store, _FixedVectorEmbeddingProvider([1.0, 0.0]),
        relevance_threshold=0.79, verified_relevance_threshold=0.74,
    )

    outcome = retriever.retrieve(
        "streetlight pole number 42 is broken", ServiceCategory.STREETLIGHTS, "Nashik", "Maharashtra"
    )

    source_ids = {c.metadata["source_id"] for c in outcome.results}
    assert "BM25_MATCH_BUT_LOW_COSINE" not in source_ids


def test_bm25_widening_is_a_no_op_when_the_pool_is_empty():
    """If get_candidates() returns nothing (e.g. the metadata filter matches zero chunks, or this
    store simply doesn't have a broader pool to offer), retrieve() must behave exactly as it did
    before hybrid search existed -- no crash, no change to the vector-search-only result."""
    candidates = [_chunk("ONLY_HIT", 0.90, "Nashik", "STREETLIGHTS", "VERIFIED")]
    outcome = _retriever(candidates).retrieve(
        "streetlight pole number 42 is broken", ServiceCategory.STREETLIGHTS, "Nashik", "Maharashtra"
    )
    source_ids = {c.metadata["source_id"] for c in outcome.results}
    assert source_ids == {"ONLY_HIT"}


# --- Cross-encoder reranker (optional, layered on top -- see rag_retriever.py's module
# docstring) -----------------------------------------------------------------------------------


class _FakeReranker:
    """Returns a fixed score per passage (matched by exact text), regardless of the query -- lets
    these tests dictate exact cross-encoder scores instead of depending on the real model."""

    def __init__(self, scores_by_content: dict[str, float]) -> None:
        self._scores_by_content = scores_by_content

    def score(self, query: str, passages: list[str]) -> list[float]:
        return [self._scores_by_content[p] for p in passages]


def _chunk_with_content(chunk_id: str, score: float, content: str, status: str = "VERIFIED") -> ScoredChunk:
    return ScoredChunk(
        chunk_id=chunk_id,
        score=score,
        metadata={
            "source_id": chunk_id, "city": "Nashik", "service_category": "STREETLIGHTS",
            "verification_status": status, "content": content,
        },
    )


def test_reranker_score_determines_final_order_not_the_raw_cosine_score():
    """LOWER_COSINE_BUT_BETTER_MATCH has a lower raw cosine score than HIGHER_COSINE_WORSE_MATCH
    but a HIGHER cross-encoder score -- when a reranker is configured, the cross-encoder's
    judgment must win, proving it's genuinely the primary sort key, not just a tie-breaker."""
    candidates = [
        _chunk_with_content("HIGHER_COSINE_WORSE_MATCH", 0.85, "generic content A"),
        _chunk_with_content("LOWER_COSINE_BUT_BETTER_MATCH", 0.80, "generic content B"),
    ]
    reranker = _FakeReranker({"generic content A": 1.0, "generic content B": 9.0})
    store = _FakeVectorStore(candidates)
    retriever = RagRetriever(
        store, _FakeEmbeddingProvider(), relevance_threshold=0.79, verified_relevance_threshold=0.74,
        reranker=reranker,
    )
    outcome = retriever.retrieve("query", ServiceCategory.STREETLIGHTS, "Nashik", "Maharashtra")
    result_ids = [c.metadata["source_id"] for c in outcome.results]
    assert result_ids[0] == "LOWER_COSINE_BUT_BETTER_MATCH"


def test_reranker_verified_preference_still_applies_as_a_tie_breaker():
    """Even with a reranker configured, a VERIFIED chunk within the top cross-encoder-score band
    must still be preferred over a SYNTHETIC one that scored marginally higher -- the tie-breaker
    survives the switch to a different score space, just recalibrated to that space's own spread
    (a third, much-lower-scoring filler candidate widens the spread so a small 0.1 gap between the
    top two clearly falls inside the resulting 10% band)."""
    candidates = [
        _chunk_with_content("SYNTHETIC_SLIGHTLY_HIGHER", 0.85, "synthetic content", status="SYNTHETIC"),
        _chunk_with_content("VERIFIED_NEAR_TOP", 0.80, "verified content", status="VERIFIED"),
        _chunk_with_content("LOW_SCORE_FILLER", 0.80, "filler content", status="VERIFIED"),
    ]
    reranker = _FakeReranker({"synthetic content": 10.0, "verified content": 9.9, "filler content": 0.0})
    store = _FakeVectorStore(candidates)
    retriever = RagRetriever(
        store, _FakeEmbeddingProvider(), relevance_threshold=0.79, verified_relevance_threshold=0.74,
        reranker=reranker,
    )
    outcome = retriever.retrieve("query", ServiceCategory.STREETLIGHTS, "Nashik", "Maharashtra")
    result_ids = [c.metadata["source_id"] for c in outcome.results]
    assert result_ids[0] == "VERIFIED_NEAR_TOP"


def test_reranker_is_never_called_for_a_single_candidate():
    """No point paying a real model call to rerank a list of one -- also confirms this doesn't
    crash on the single-element case (min()/max() over one score, or a zero-width band)."""
    candidates = [_chunk_with_content("ONLY_ONE", 0.90, "only content")]

    class _RerankerThatMustNotBeCalled:
        def score(self, query, passages):
            raise AssertionError("reranker.score() must not be called for a single candidate")

    store = _FakeVectorStore(candidates)
    retriever = RagRetriever(
        store, _FakeEmbeddingProvider(), relevance_threshold=0.79, verified_relevance_threshold=0.74,
        reranker=_RerankerThatMustNotBeCalled(),
    )
    outcome = retriever.retrieve("query", ServiceCategory.STREETLIGHTS, "Nashik", "Maharashtra")
    assert [c.metadata["source_id"] for c in outcome.results] == ["ONLY_ONE"]


def test_reranker_failure_falls_back_to_the_heuristic_rerank_instead_of_crashing():
    """BUG FIX (code review): CrossEncoderReranker.score() has no exception handling of its own,
    and this call site previously had none either -- a reranker failure (model load failure,
    OOM, a raised exception from predict()) used to propagate straight out of retrieve(),
    contradicting RetrievalOutcome's own "always returned, never raises" contract and this
    codebase's fail-open pattern for every other optional AI dependency. retrieve() must
    degrade to the heuristic-only rerank for this request instead of crashing it."""
    candidates = [
        _chunk_with_content("A", 0.90, "content a", status="VERIFIED"),
        _chunk_with_content("B", 0.85, "content b", status="SYNTHETIC"),
    ]

    class _RerankerThatAlwaysFails:
        def score(self, query, passages):
            raise RuntimeError("model failed to load")

    store = _FakeVectorStore(candidates)
    retriever = RagRetriever(
        store, _FakeEmbeddingProvider(), relevance_threshold=0.79, verified_relevance_threshold=0.74,
        reranker=_RerankerThatAlwaysFails(),
    )

    outcome = retriever.retrieve("query", ServiceCategory.STREETLIGHTS, "Nashik", "Maharashtra")

    # Never raised -- and produced a real result via the heuristic fallback (highest raw cosine
    # score, "A" at 0.90, wins -- "B" is separately, correctly dropped by the unrelated
    # citation-honesty filter since a VERIFIED chunk exists for the same city+category).
    assert not outcome.insufficient_knowledge
    assert outcome.results[0].metadata["source_id"] == "A"


def test_reranker_band_is_capped_so_one_outlier_cannot_stretch_it_arbitrarily():
    """BUG FIX (code review): an uncapped `0.1 * spread` band is stretched by ANY single extreme
    outlier in the candidate set -- here a very poorly-scored third candidate would otherwise
    widen the band enough for VERIFIED_MEANINGFULLY_LOWER (6.0) to wrongly qualify as "near-top"
    against a true top score of 10.0, letting it leapfrog SYNTHETIC_TRUE_TOP even though it's 4
    points below on a scale where that's a meaningful gap -- exactly the "never promotes a chunk
    that scored meaningfully lower" guarantee this mechanism exists to uphold.

    VERIFIED_MEANINGFULLY_LOWER is deliberately given a DIFFERENT city than the two SYNTHETIC
    chunks -- pure test isolation from the unrelated citation-honesty filter (which would
    otherwise drop a SYNTHETIC chunk once a VERIFIED one exists for the exact same
    city+category), not part of the bug this test targets."""
    candidates = [
        ScoredChunk(chunk_id="SYNTHETIC_TRUE_TOP", score=0.85, metadata={"source_id": "SYNTHETIC_TRUE_TOP", "city": "Nashik", "service_category": "STREETLIGHTS", "verification_status": "SYNTHETIC", "content": "top content"}),
        ScoredChunk(chunk_id="VERIFIED_MEANINGFULLY_LOWER", score=0.80, metadata={"source_id": "VERIFIED_MEANINGFULLY_LOWER", "city": "OtherCity", "service_category": "STREETLIGHTS", "verification_status": "VERIFIED", "content": "verified content"}),
        ScoredChunk(chunk_id="EXTREME_OUTLIER", score=0.80, metadata={"source_id": "EXTREME_OUTLIER", "city": "Nashik", "service_category": "STREETLIGHTS", "verification_status": "SYNTHETIC", "content": "outlier content"}),
    ]
    # Uncapped: spread = 10.0 - (-100.0) = 110, band = 11.0, so top_score - band = -1.0 --
    # VERIFIED_MEANINGFULLY_LOWER (6.0) would wrongly qualify. Capped at 2.0: top_score - band =
    # 8.0, so 6.0 correctly does NOT qualify.
    reranker = _FakeReranker({"top content": 10.0, "verified content": 6.0, "outlier content": -100.0})
    store = _FakeVectorStore(candidates)
    retriever = RagRetriever(
        store, _FakeEmbeddingProvider(), relevance_threshold=0.79, verified_relevance_threshold=0.74,
        reranker=reranker,
    )

    outcome = retriever.retrieve("query", ServiceCategory.STREETLIGHTS, "Nashik", "Maharashtra")

    result_ids = [c.metadata["source_id"] for c in outcome.results]
    assert result_ids[0] == "SYNTHETIC_TRUE_TOP", (
        "the true top-scoring chunk must still rank first -- if VERIFIED_MEANINGFULLY_LOWER "
        "ranks first instead, the band is still uncapped and being stretched by the outlier"
    )


# --- BM25 index caching + bounded embedding fetch (code review, efficiency) -----------------
#
# See rag_retriever.py's _get_or_build_bm25_index()/_widen_with_bm25() docstrings: the BM25 index
# used to be rebuilt from scratch on every retrieve() call, and every chunk in the filtered pool
# got a real embedding fetched up front regardless of whether BM25 ever surfaced it. These tests
# prove both are now fixed: the index is built once per distinct metadata_filter, and embeddings
# are only ever fetched for the small set of NEW candidates BM25 actually ranks highly.


class _CountingVectorStoreWithPool:
    """Same contract as `_FakeVectorStoreWithPool` above, plus call counters on the two calls
    that used to be the efficiency problem -- lets these tests assert ON the call pattern itself,
    not just the final (already-covered-elsewhere) correctness of the result."""

    def __init__(self, search_results: list[ScoredChunk], pool: list[tuple]) -> None:
        self._search_results = search_results
        self._pool = pool
        self.get_candidate_texts_call_count = 0
        self.get_embeddings_call_args: list[list[str]] = []

    def search(self, query_vector, top_k: int, metadata_filter: dict[str, str] | None) -> list[ScoredChunk]:
        return list(self._search_results)

    def get_candidates(self, metadata_filter: dict[str, str] | None) -> list[tuple]:
        return list(self._pool)

    def get_candidate_texts(self, metadata_filter: dict[str, str] | None) -> list[tuple]:
        self.get_candidate_texts_call_count += 1
        return [(chunk_id, content, metadata) for chunk_id, content, metadata, _vector in self._pool]

    def get_embeddings(self, chunk_ids: list[str]) -> dict:
        self.get_embeddings_call_args.append(list(chunk_ids))
        wanted = set(chunk_ids)
        return {chunk_id: vector for chunk_id, _content, _metadata, vector in self._pool if chunk_id in wanted}


def _bm25_realistic_pool(vector_hit_metadata: dict) -> list[tuple]:
    """A pool with enough unrelated filler documents to keep BM25's IDF math sane (see this
    file's own earlier comment on why a too-tiny corpus makes classic BM25 IDF go negative for a
    term shared by every document) -- reused by both tests below."""
    return [
        ("VECTOR_HIT", "general streetlight complaint information", vector_hit_metadata, [1.0, 0.0]),
        (
            "BM25_ONLY_HIT",
            "streetlight pole number 42 is broken near the market",
            {
                "source_id": "BM25_ONLY_HIT", "city": "Nashik",
                "service_category": "STREETLIGHTS", "verification_status": "VERIFIED",
            },
            [0.85, 0.5268026970889531],
        ),
        ("FILLER_1", "garbage collection schedule for residential areas", {"source_id": "FILLER_1", "city": "Nashik", "service_category": "STREETLIGHTS", "verification_status": "SYNTHETIC"}, [0.01, 0.01]),
        ("FILLER_2", "water supply timings for this ward", {"source_id": "FILLER_2", "city": "Nashik", "service_category": "STREETLIGHTS", "verification_status": "SYNTHETIC"}, [0.01, 0.01]),
        ("FILLER_3", "park maintenance report for the month", {"source_id": "FILLER_3", "city": "Nashik", "service_category": "STREETLIGHTS", "verification_status": "SYNTHETIC"}, [0.01, 0.01]),
    ]


def test_bm25_index_is_built_once_and_cached_across_repeated_calls_with_the_same_filter():
    vector_hit = _chunk("VECTOR_HIT", 0.90, "Nashik", "STREETLIGHTS", "VERIFIED")
    store = _CountingVectorStoreWithPool(
        search_results=[vector_hit], pool=_bm25_realistic_pool(vector_hit.metadata),
    )
    retriever = RagRetriever(
        store, _FixedVectorEmbeddingProvider([1.0, 0.0]),
        relevance_threshold=0.79, verified_relevance_threshold=0.74,
    )

    retriever.retrieve("streetlight pole number 42 is broken", ServiceCategory.STREETLIGHTS, "Nashik", "Maharashtra")
    retriever.retrieve("streetlight pole number 42 is broken", ServiceCategory.STREETLIGHTS, "Nashik", "Maharashtra")
    retriever.retrieve("streetlight pole number 42 is broken", ServiceCategory.STREETLIGHTS, "Nashik", "Maharashtra")

    assert store.get_candidate_texts_call_count == 1, (
        "the pool/BM25 index must be fetched+built once for this filter, then reused -- not "
        "rebuilt from scratch on every retrieve() call"
    )


def test_bm25_index_cache_is_keyed_separately_per_distinct_filter():
    """A DIFFERENT metadata_filter must still get its own fresh build -- the cache must not
    incorrectly reuse one category/city's index for a different one."""
    vector_hit = _chunk("VECTOR_HIT", 0.90, "Nashik", "STREETLIGHTS", "VERIFIED")
    store = _CountingVectorStoreWithPool(
        search_results=[vector_hit], pool=_bm25_realistic_pool(vector_hit.metadata),
    )
    retriever = RagRetriever(
        store, _FixedVectorEmbeddingProvider([1.0, 0.0]),
        relevance_threshold=0.79, verified_relevance_threshold=0.74,
    )

    retriever.retrieve("streetlight pole number 42 is broken", ServiceCategory.STREETLIGHTS, "Nashik", "Maharashtra")
    retriever.retrieve("streetlight pole number 42 is broken", ServiceCategory.WASTE_SANITATION, "Nashik", "Maharashtra")
    retriever.retrieve("streetlight pole number 42 is broken", ServiceCategory.STREETLIGHTS, "Pune", "Maharashtra")

    assert store.get_candidate_texts_call_count == 3, (
        "three genuinely distinct filters were used -- each must get its own cache entry, not "
        "share one incorrectly"
    )


def test_bm25_only_fetches_embeddings_for_new_candidates_not_the_whole_pool():
    """The pool has 5 chunks total; only BM25_ONLY_HIT is a genuine new widening candidate
    (VECTOR_HIT is already a vector-search hit, the 3 FILLER chunks score 0 against this query).
    get_embeddings() must be called with ONLY the new candidate(s) -- never the whole pool, and
    never the already-present vector hit."""
    vector_hit = _chunk("VECTOR_HIT", 0.90, "Nashik", "STREETLIGHTS", "VERIFIED")
    store = _CountingVectorStoreWithPool(
        search_results=[vector_hit], pool=_bm25_realistic_pool(vector_hit.metadata),
    )
    retriever = RagRetriever(
        store, _FixedVectorEmbeddingProvider([1.0, 0.0]),
        relevance_threshold=0.79, verified_relevance_threshold=0.74,
    )

    retriever.retrieve("streetlight pole number 42 is broken", ServiceCategory.STREETLIGHTS, "Nashik", "Maharashtra")

    assert len(store.get_embeddings_call_args) == 1
    fetched_ids = store.get_embeddings_call_args[0]
    assert fetched_ids == ["BM25_ONLY_HIT"]


def test_bm25_does_not_call_get_embeddings_at_all_when_nothing_new_to_widen_with():
    """If every BM25-positive-scoring chunk is already a vector-search hit, there's nothing new
    to fetch an embedding for -- get_embeddings() must not be called at all (not even with an
    empty list), matching _widen_with_bm25's own early-return for this case."""
    vector_hit = _chunk("VECTOR_HIT", 0.90, "Nashik", "STREETLIGHTS", "VERIFIED")
    # Pool where the ONLY chunk with real query-token overlap is already the vector hit itself.
    pool = [
        ("VECTOR_HIT", "streetlight pole number 42 is broken near the market", vector_hit.metadata, [1.0, 0.0]),
        ("FILLER_1", "garbage collection schedule for residential areas", {"source_id": "FILLER_1", "city": "Nashik", "service_category": "STREETLIGHTS", "verification_status": "SYNTHETIC"}, [0.01, 0.01]),
        ("FILLER_2", "water supply timings for this ward", {"source_id": "FILLER_2", "city": "Nashik", "service_category": "STREETLIGHTS", "verification_status": "SYNTHETIC"}, [0.01, 0.01]),
    ]
    store = _CountingVectorStoreWithPool(search_results=[vector_hit], pool=pool)
    retriever = RagRetriever(
        store, _FixedVectorEmbeddingProvider([1.0, 0.0]),
        relevance_threshold=0.79, verified_relevance_threshold=0.74,
    )

    retriever.retrieve("streetlight pole number 42 is broken", ServiceCategory.STREETLIGHTS, "Nashik", "Maharashtra")

    assert store.get_embeddings_call_args == []


def test_hybrid_search_enabled_false_skips_bm25_widening_entirely():
    """See config.py's RAG_HYBRID_SEARCH_ENABLED docstring (code review finding: no escape hatch
    existed at all before this) -- with it off, retrieve() must behave exactly as if hybrid
    search were never built: no pool fetch, no BM25 build, no embedding fetch, and a chunk that
    only BM25 could have surfaced must NOT appear in the results."""
    vector_hit = _chunk("VECTOR_HIT", 0.90, "Nashik", "STREETLIGHTS", "VERIFIED")
    store = _CountingVectorStoreWithPool(
        search_results=[vector_hit], pool=_bm25_realistic_pool(vector_hit.metadata),
    )
    retriever = RagRetriever(
        store, _FixedVectorEmbeddingProvider([1.0, 0.0]),
        relevance_threshold=0.79, verified_relevance_threshold=0.74,
        hybrid_search_enabled=False,
    )

    outcome = retriever.retrieve(
        "streetlight pole number 42 is broken", ServiceCategory.STREETLIGHTS, "Nashik", "Maharashtra"
    )

    assert store.get_candidate_texts_call_count == 0
    assert store.get_embeddings_call_args == []
    result_ids = [c.metadata["source_id"] for c in outcome.results]
    assert result_ids == ["VECTOR_HIT"]  # BM25_ONLY_HIT never surfaces with hybrid search off
