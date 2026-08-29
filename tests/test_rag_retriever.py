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

from backend.schemas.rag_knowledge import ServiceCategory
from backend.services.rag_retriever import RagRetriever
from backend.services.vector_store import ScoredChunk


class _FakeEmbeddingProvider:
    def embed_query(self, text: str) -> str:
        return text


class _FakeVectorStore:
    """Returns a fixed, pre-scored candidate list regardless of the query vector or top_k --
    lets these tests dictate exact scores instead of depending on real embedding-model output."""

    def __init__(self, candidates: list[ScoredChunk]) -> None:
        self._candidates = candidates

    def search(self, query_vector, top_k: int, metadata_filter: dict[str, str] | None) -> list[ScoredChunk]:
        return list(self._candidates)


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
