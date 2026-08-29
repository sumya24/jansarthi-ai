"""Retrieval layer: combines metadata filtering (service category + location) with semantic
vector search, then a lightweight relevance/rerank pass -- the component everything else in the
Ask Sarthi pipeline calls to actually get chunks back.

Filtering happens BEFORE ranking, always. This is the single most important design decision in
this file: a chunk that doesn't match the requested service category or location is never a
candidate at all, regardless of how textually similar its content happens to be to the query.
This is what prevents the exact failure mode observed while building this module -- an
unfiltered search for "new electricity connection" returned a WATER_DRAINAGE chunk (shared words
like "new"/"connection") as its top hit. With category filtering active, a question correctly
classified as being about an unsupported service (electricity) never reaches vector search at
all (see ask_janmitra_service.py, which checks `out_of_scope_service` first).

Provider-agnostic by design: this class works unchanged against either
`(SentenceTransformerEmbeddingProvider, ChromaVectorStore)` (the active default) or
`(TfidfEmbeddingProvider, FlatVectorStore)` (legacy/comparison) -- both provider pairs implement
the same `embed_query`/`search` shape, and `service_category` is now a real, exact-match-able
metadata field on every chunk (added during the ChromaDB migration -- see
backend/schemas/rag_knowledge.py's Chunk docstring), not inferred from a `service_id` string
prefix as the original TF-IDF-era version of this file did.

**Hybrid search (BM25 + vector)**: pure vector search can blur past an exact keyword a citizen's
question shares with a chunk -- a paraphrase-tolerant embedding model doesn't specially privilege
an exact department name, form number, or fee figure matching verbatim. To catch that without
touching this file's carefully-tuned cosine-similarity thresholds (`_relevance_threshold`,
`_verified_relevance_threshold`) or destabilizing already-correct ranking behavior, BM25 is used
ONLY to WIDEN the candidate pool, never to replace or fuse into a different score space (no RRF):
`retrieve()` builds a BM25 index over the *same* metadata-filtered candidate pool vector search
already scoped to (via each store's new `get_candidates()`), and any chunk BM25 surfaces that
vector search's `top_k * 3` window didn't already include gets a REAL cosine similarity score
computed against the query (via `cosine_similarity_any`) before joining the candidate list. Every
downstream step -- the relevance threshold, the VERIFIED rescue, the rerank, the citation-honesty
filter -- then runs completely unchanged over the widened pool, unaware any of this happened.

**Reranker (optional, layered on top)**: an injected `reranker` (see backend/services/
reranker.py's `CrossEncoderReranker`) re-scores the small, already threshold-filtered candidate
set with a real trained cross-encoder -- a strictly more accurate (if more expensive, hence only
applied here, never over the full corpus) relevance judgment than the bi-encoder cosine similarity
used for filtering. When configured, its score becomes the PRIMARY sort key and the existing
VERIFIED-preference tie-breaker's "near-top" band is computed relative to the cross-encoder score
spread instead of the raw cosine score. `reranker=None` (the default) preserves this file's
original heuristic-only behavior byte-for-byte -- every existing caller that doesn't pass one
keeps working exactly as before.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from rank_bm25 import BM25Okapi

from backend.schemas.rag_knowledge import ServiceCategory
from backend.services.embedding_provider import cosine_similarity_any, tokenize
from backend.services.vector_store import ScoredChunk

logger = logging.getLogger(__name__)


class _EmbeddingProvider(Protocol):
    def embed_query(self, text: str) -> Any: ...


class _VectorStore(Protocol):
    def search(self, query_vector: Any, top_k: int, metadata_filter: dict[str, str] | None) -> list[ScoredChunk]: ...

    def get_candidates(self, metadata_filter: dict[str, str] | None) -> list[tuple[str, str, dict[str, Any], Any]]: ...

    def get_candidate_texts(self, metadata_filter: dict[str, str] | None) -> list[tuple[str, str, dict[str, Any]]]: ...

    def get_embeddings(self, chunk_ids: list[str]) -> dict[str, Any]: ...


class _Reranker(Protocol):
    def score(self, query: str, passages: list[str]) -> list[float]: ...


@dataclass
class RetrievalOutcome:
    """What retrieve() found -- always returned, never raises. `results` is empty and
    `insufficient_knowledge` is True whenever nothing usable was found, with `reason` explaining
    why (no location, no category match, no results above the relevance threshold) so the caller
    (ask_janmitra_service.py) can compose an honest response instead of a generic failure."""

    results: list[ScoredChunk] = field(default_factory=list)
    insufficient_knowledge: bool = False
    reason: str | None = None


class RagRetriever:
    def __init__(
        self,
        vector_store: _VectorStore,
        embedding_provider: _EmbeddingProvider,
        top_k: int = 5,
        relevance_threshold: float = 0.79,
        verified_relevance_threshold: float = 0.74,
        reranker: _Reranker | None = None,
        hybrid_search_enabled: bool = True,
    ) -> None:
        self._store = vector_store
        self._embedding_provider = embedding_provider
        self._top_k = top_k
        self._relevance_threshold = relevance_threshold
        # See config.py's RAG_HYBRID_SEARCH_ENABLED docstring for why this defaults True (a
        # different default than `reranker` above, deliberately) and for the real env-var
        # escape hatch this constructor flag exists to make usable.
        self._hybrid_search_enabled = hybrid_search_enabled
        # See config.py's RAG_VERIFIED_RELEVANCE_THRESHOLD docstring for the measured cross-lingual
        # gap this exists for. Must never be ABOVE the main threshold -- it exists to admit MORE
        # verified content in edge cases, never less.
        self._verified_relevance_threshold = min(verified_relevance_threshold, relevance_threshold)
        # Optional real trained cross-encoder -- see this module's docstring and
        # backend/services/reranker.py. None (the default) means every existing caller that
        # doesn't pass one gets this file's original heuristic-only rerank, unchanged.
        self._reranker = reranker
        # BUG FIX (code review, efficiency): a BM25 index (and the text pool it's built from) is
        # identical for every query sharing the same metadata_filter until the knowledge base is
        # next re-ingested -- rebuilding it from scratch on every single retrieve() call was pure
        # waste for the extremely common case of repeat traffic to the same city+category (this
        # app's own real usage pattern -- civic complaints cluster heavily by category and city).
        # Keyed on a hashable form of metadata_filter; this instance is already a long-lived
        # per-process singleton (same lifetime as the Chroma collection/embedding model it wraps
        # -- see AskJanMitraService's own module docstring), so caching for the process lifetime
        # matches how every other expensive one-time load in this pipeline is already handled.
        # Capped (see _widen_with_bm25) so an unusual flood of distinct filters can't grow this
        # unboundedly over a very long-running process.
        self._bm25_cache: dict[Any, tuple[Any, list[tuple[str, str, dict[str, Any]]]] | None] = {}

    def retrieve(
        self,
        query: str,
        service_category: ServiceCategory | None,
        city: str | None,
        state: str | None,
    ) -> RetrievalOutcome:
        metadata_filter: dict[str, str] = {}
        if service_category is not None:
            metadata_filter["service_category"] = service_category.value
        if city is not None:
            metadata_filter["city"] = city
        elif state is not None:
            metadata_filter["state"] = state

        query_vector = self._embedding_provider.embed_query(query)
        # Ask the store for more than top_k so the VERIFIED-preference rerank below has something
        # to work with beyond the raw top_k by score alone.
        candidates = self._store.search(query_vector, top_k=self._top_k * 3, metadata_filter=metadata_filter or None)
        if self._hybrid_search_enabled:
            candidates = self._widen_with_bm25(query, query_vector, candidates, metadata_filter or None)

        if not candidates:
            reason = "No knowledge records exist for this service/location combination."
            logger.info("RAG retrieval: no candidates (category=%s, city=%s, state=%s)", service_category, city, state)
            return RetrievalOutcome(insufficient_knowledge=True, reason=reason)

        above_threshold = [c for c in candidates if c.score >= self._relevance_threshold]

        # CROSS-LINGUAL VERIFIED RESCUE (live-reproduced gap -- see config.py's own
        # RAG_VERIFIED_RELEVANCE_THRESHOLD docstring for the measured Bengaluru/Marathi case this
        # closes): a VERIFIED chunk that already passed the same city+category metadata filter as
        # every other candidate here, but scored just under the main threshold -- specifically
        # measured for non-English-script queries against this KB's English-authored content -- is
        # rescued at a separate, lower floor instead of being silently lost to a topically-generic
        # SYNTHETIC chunk (or to "insufficient_knowledge" outright, if nothing else cleared the
        # main threshold either). Deliberately never applies to SYNTHETIC chunks -- this only ever
        # widens coverage for content that's already real/never-fabricated, not a general threshold
        # relaxation.
        rescued_verified = [
            c for c in candidates
            if c.metadata["verification_status"] == "VERIFIED"
            and self._verified_relevance_threshold <= c.score < self._relevance_threshold
        ]
        if rescued_verified:
            logger.info(
                "RAG retrieval: rescued %d VERIFIED chunk(s) below main threshold %.3f but above "
                "verified floor %.3f (scores: %s)",
                len(rescued_verified), self._relevance_threshold, self._verified_relevance_threshold,
                [round(c.score, 3) for c in rescued_verified],
            )
            above_threshold = above_threshold + rescued_verified

        if not above_threshold:
            reason = "No sufficiently relevant knowledge found for this question."
            # BUG FIX (code review): `candidates[0]` is only guaranteed to be the best-scoring
            # candidate when `candidates` came straight from `store.search()` (sorted). Hybrid
            # search's BM25 widening can append higher-scoring candidates after it -- max() keeps
            # this diagnostic log accurate regardless (this is log-only, no behavior depends on it).
            best_score = max(c.score for c in candidates)
            logger.info(
                "RAG retrieval: %d candidate(s) but none above threshold %.3f (best score %.3f)",
                len(candidates), self._relevance_threshold, best_score,
            )
            return RetrievalOutcome(insufficient_knowledge=True, reason=reason)

        # Rerank: either a real cross-encoder (when self._reranker is configured -- see this
        # module's docstring and backend/services/reranker.py) re-scoring this small
        # already-filtered candidate set, or -- unchanged from before the reranker existed -- a
        # lightweight heuristic over the raw cosine scores (documented in
        # docs/ask_janmitra_rag_architecture.md's reranking section as the original, pre-Part-6
        # choice). Either way, the VERIFIED-preference tie-breaker below runs the same: among
        # results within a small band of the top score, prefer VERIFIED over SYNTHETIC -- never
        # lets a SYNTHETIC chunk outrank a near-equally-relevant VERIFIED one, without ever
        # promoting a SYNTHETIC chunk that scored meaningfully lower.
        if self._reranker is not None and len(above_threshold) > 1:
            ce_scores = self._reranker.score(query, [c.metadata.get("content", "") for c in above_threshold])
            rank_scores = dict(zip((c.chunk_id for c in above_threshold), ce_scores))
            top_score = max(ce_scores)
            # The cross-encoder's raw output isn't bounded to a fixed range the way cosine
            # similarity is, so a fixed absolute band (0.03) has no meaning here -- use a fraction
            # of THIS result set's own score spread instead, same intent (near-top, not
            # meaningfully lower) in whatever scale this model happens to produce.
            spread = top_score - min(ce_scores)
            band = 0.1 * spread if spread > 0 else 0.0
        else:
            rank_scores = {c.chunk_id: c.score for c in above_threshold}
            # BUG FIX (code review): `above_threshold[0]` was the top score back when this list
            # came straight out of `store.search()` (already sorted descending). Since hybrid
            # search (_widen_with_bm25) appends BM25-surfaced chunks to the END of the list with
            # their own real cosine scores -- which can legitimately exceed every vector-search
            # score, since catching a higher-relevance chunk vector search's ANN window missed is
            # the whole point -- `above_threshold` is no longer guaranteed sorted here. Using
            # `max()` finds the TRUE top score regardless of position, so the VERIFIED-preference
            # band below is anchored correctly instead of silently using a stale, too-low anchor.
            top_score = max(c.score for c in above_threshold)
            band = 0.03  # narrower than the old TF-IDF-era 0.05 -- real embedding scores cluster
                         # much tighter (see the threshold-selection notes in the architecture doc)

        def sort_key(c: ScoredChunk) -> tuple[int, float]:
            rank_score = rank_scores[c.chunk_id]
            verified_bonus = 1 if (c.metadata["verification_status"] == "VERIFIED" and rank_score >= top_score - band) else 0
            return (verified_bonus, rank_score)
        above_threshold.sort(key=sort_key, reverse=True)

        # CITATION HONESTY FIX (live-reported): the rerank above only ever reorders -- a SYNTHETIC
        # chunk could still land in the final top_k and be shown as a citation even when a VERIFIED
        # chunk for that EXACT same city+category is also present, e.g. Ahmedabad garbage
        # collection citing both a real AMC document and a "not a verified official source"
        # placeholder side by side. Synthetic records exist to fill a genuine COVERAGE GAP (a
        # city/category with no real source at all, see citation_examples.md) -- once a verified
        # source for that same city+category exists in this result set, the synthetic one adds no
        # information and only undermines trust, so it's dropped here rather than merely
        # deprioritized. Scoped to (city, service_category) specifically, not source_id -- two
        # different VERIFIED records already legitimately coexist for the same city+category (see
        # this file's own tests), only a SYNTHETIC one loses out, and only against a VERIFIED match
        # for its own city+category, never a different one.
        verified_city_categories = {
            (c.metadata.get("city"), c.metadata.get("service_category"))
            for c in above_threshold
            if c.metadata["verification_status"] == "VERIFIED"
        }
        above_threshold = [
            c for c in above_threshold
            if c.metadata["verification_status"] == "VERIFIED"
            or (c.metadata.get("city"), c.metadata.get("service_category")) not in verified_city_categories
        ]

        return RetrievalOutcome(results=above_threshold[: self._top_k])

    # Hard cap on distinct metadata_filter combinations cached at once -- this app's real filter
    # space (a fixed set of ServiceCategory values times a bounded set of covered cities/states)
    # never approaches this in practice; it exists only so a pathological flood of distinct
    # filters (e.g. many one-off free-text city names) can't grow this cache unboundedly over a
    # very long-running process. Clearing the whole cache on overflow (rather than an LRU) is a
    # deliberate simplification -- this is expected to never actually trigger.
    _BM25_CACHE_MAX_ENTRIES = 500

    def _get_or_build_bm25_index(self, metadata_filter: dict[str, str] | None):
        """Returns `(bm25_index_or_None, pool_texts)` for this metadata_filter, building and
        caching it on first use. `bm25_index` is `None` when the filtered pool is empty (nothing
        to widen with, ever, for this filter). See `self._bm25_cache`'s own docstring (in
        `__init__`) for why caching this is safe and worthwhile."""
        cache_key = tuple(sorted(metadata_filter.items())) if metadata_filter else None
        cached = self._bm25_cache.get(cache_key)
        if cached is not None:
            return cached

        if len(self._bm25_cache) >= self._BM25_CACHE_MAX_ENTRIES:
            logger.warning(
                "RAG retrieval: BM25 index cache hit its %d-entry cap -- clearing (see "
                "_widen_with_bm25's own comment; this is not expected in normal operation)",
                self._BM25_CACHE_MAX_ENTRIES,
            )
            self._bm25_cache.clear()

        pool_texts = self._store.get_candidate_texts(metadata_filter)
        if not pool_texts:
            result = (None, [])
        else:
            corpus_tokens = [tokenize(content) for _, content, _ in pool_texts]
            result = (BM25Okapi(corpus_tokens), pool_texts)
        self._bm25_cache[cache_key] = result
        return result

    def _widen_with_bm25(
        self,
        query: str,
        query_vector: Any,
        candidates: list[ScoredChunk],
        metadata_filter: dict[str, str] | None,
    ) -> list[ScoredChunk]:
        """Adds any chunk BM25 keyword-matches within the SAME metadata-filtered pool that vector
        search's `candidates` didn't already surface -- see this module's docstring for the full
        "widen, never replace/fuse" design. Returns `candidates` unchanged (same list, same order)
        if the filtered pool is empty, or if the query has no tokens to match on at all.

        BUG FIX (code review, efficiency): this used to (a) rebuild the BM25 index from scratch on
        every call regardless of whether an identical `metadata_filter` was just seen (see
        `_get_or_build_bm25_index()`/`self._bm25_cache`), and (b) fetch a REAL EMBEDDING for every
        chunk in the entire filtered pool up front, even though only a small handful ever turn out
        to be BM25 widening candidates. Now only the pool's TEXT is fetched/cached up front
        (`get_candidate_texts()`), and real embeddings are fetched (`get_embeddings()`) only for
        the specific chunk ids that actually rank in BM25's own top results and aren't already
        vector-search hits -- bounded by `top_k * 3`, never by corpus size."""
        bm25, pool_texts = self._get_or_build_bm25_index(metadata_filter)
        if bm25 is None:
            return candidates

        query_tokens = tokenize(query)
        if not query_tokens:
            return candidates

        scores = bm25.get_scores(query_tokens)

        already_present = {c.chunk_id for c in candidates}
        # A BM25 score of 0.0 means no query token appears in that chunk at all -- not a real
        # keyword match, just an artifact of every chunk getting a score. Only chunks with a
        # genuine positive score are candidates for widening.
        ranked = sorted(
            ((score, idx) for idx, score in enumerate(scores) if score > 0.0),
            key=lambda pair: pair[0],
            reverse=True,
        )
        # Filter down to genuinely NEW candidates BEFORE fetching embeddings -- so the embedding
        # fetch below is scoped to exactly the chunks that might get added, never anything already
        # present as a vector-search hit.
        top_new = [
            (score, idx) for score, idx in ranked[: self._top_k * 3]
            if pool_texts[idx][0] not in already_present
        ]
        if not top_new:
            return candidates

        new_embeddings = self._store.get_embeddings([pool_texts[idx][0] for _, idx in top_new])

        widened = list(candidates)
        for score, idx in top_new:
            chunk_id, content, metadata = pool_texts[idx]
            vector = new_embeddings.get(chunk_id)
            if vector is None:
                continue  # shouldn't happen (see get_embeddings()'s own docstring) -- never crash retrieval over it
            real_score = cosine_similarity_any(query_vector, vector)
            # ChromaVectorStore's metadata doesn't include "content" (Chroma stores chunk text
            # separately as a "document" -- see its get_candidates()/search() docstrings);
            # FlatVectorStore's metadata already does, so this is a no-op overwrite with the same
            # value there. Either way every ScoredChunk this method produces has "content" set,
            # matching what search() already guarantees.
            merged_metadata = dict(metadata)
            merged_metadata["content"] = content
            widened.append(ScoredChunk(chunk_id=chunk_id, score=real_score, metadata=merged_metadata))
            logger.info(
                "RAG retrieval: BM25 widened candidate pool with chunk %s (bm25=%.3f, real cosine=%.3f)",
                chunk_id, score, real_score,
            )
        return widened


def chunk_context_label(chunk: ScoredChunk) -> str:
    """RAG QUALITY-GATE FIX: `AnswerGenerationService.generate()`'s prompt was built from a
    chunk's raw `content` text ALONE -- dropping the `sub_service`/`verification_status` metadata
    already sitting right next to it on the same `ScoredChunk`. Live-reproduced root cause of the
    Bhubaneswar pothole case: category+location filtering is correct (ROADS_POTHOLES is this KB's
    general "roads" bucket, and Odisha's two state-wide road records are genuinely, correctly
    retrieved as ROADS_POTHOLES/Odisha evidence -- see this fix's own writeup) -- but within that
    category, a query asking to REPORT a pothole and a record about getting PERMISSION to cut a
    road for utility work are different sub-services, and content-only context gives the LLM no
    way to see that distinction. It answered anyway, inventing a "pothole" framing and a
    Bhubaneswar-specific claim the source never states. Labeling each excerpt with `sub_service`
    (already authored on every KnowledgeRecord in this KB, see backend/schemas/rag_knowledge.py's
    Chunk model -- no new data, no new keyword list) lets the SAME model, given the SAME prompt
    (see prompts/ask_janmitra_answer_prompt.txt's existing "using only the context above... if
    insufficient, say so plainly" instruction) recognize the topic mismatch itself and decline
    honestly -- verified directly: without this label the model fabricated a pothole-reporting
    procedure from a road-cutting-permission record; with it, the same call answered "I don't have
    official information on this." Also finally delivers on the prompt's own pre-existing (but
    previously unfulfilled) claim that "each excerpt is labeled VERIFIED or SYNTHETIC" -- that
    label was promised in the prompt template but never actually included in the context string
    until now.

    Deliberately kept SEPARATE from a chunk's plain `content` (see `AnswerGenerationService.
    generate()`'s `context_labels` param) rather than baked into one combined string: the
    no-LLM-configured/LLM-call-failed fallback path echoes a chunk's raw content verbatim to the
    citizen (see `_fallback_answer`) -- it must never leak this internal `[VERIFIED | Topic: ...]`
    bracket straight into a citizen-facing answer."""
    md = chunk.metadata
    return f"{md.get('verification_status', 'UNKNOWN')} | Topic: {md.get('sub_service', 'General')}"
