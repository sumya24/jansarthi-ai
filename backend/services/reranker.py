"""A real, trained cross-encoder reranker for RAG retrieval -- see rag_retriever.py's module
docstring for exactly how this fits into the retrieval pipeline.

Layered ON TOP of, not replacing, the existing VERIFIED-preference heuristic tie-breaker in
`RagRetriever.retrieve()` -- a deliberate decision to preserve this project's already-measured,
already-tuned cross-lingual VERIFIED-rescue behavior (see config.py's
RAG_VERIFIED_RELEVANCE_THRESHOLD docstring) rather than risk destabilizing it by handing ranking
over to a model that has never seen this specific corpus.

Chosen model: `cross-encoder/ms-marco-MiniLM-L-6-v2` -- free, MIT-licensed, small (~80MB, a
6-layer MiniLM) enough to run acceptably on CPU with no GPU on this project's current deployment
target, and the standard, widely-used baseline reranker for exactly this task (trained on the
MS MARCO passage-ranking dataset for query-passage relevance scoring).

Why a cross-encoder is genuinely different from (not just a fancier version of) the bi-encoder
cosine similarity `SentenceTransformerEmbeddingProvider`/`ChromaVectorStore` already use: a
cross-encoder scores a (query, passage) PAIR jointly, in one transformer forward pass with full
attention between query and passage tokens, so it can weigh interactions a bi-encoder's two
SEPARATELY-encoded fixed vectors structurally cannot capture. The tradeoff is cost -- a
cross-encoder score can't be precomputed/indexed the way a document embedding can, it must run
once per (query, candidate) pair at query time. That's exactly why `RagRetriever` only ever calls
this against its small, already threshold-filtered candidate set (typically single digits), never
the full corpus.

Loaded lazily -- same pattern as `SentenceTransformerEmbeddingProvider` -- so constructing a
`CrossEncoderReranker` (or importing this module) never pays the real, non-trivial model-load cost
until `score()`/`load()` is actually called.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class CrossEncoderReranker:
    def __init__(self, model_name: str = _DEFAULT_MODEL_NAME) -> None:
        self._model_name = model_name
        self._model = None  # lazy-loaded, see _get_model

    @property
    def model_name(self) -> str:
        return self._model_name

    def load(self) -> None:
        """Forces the (slow, first-time) model load now rather than lazily on first score() call
        -- mirrors SentenceTransformerEmbeddingProvider.load(), for startup/ingestion code that
        wants to pay this cost upfront rather than on a citizen-facing request."""
        self._get_model()

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            logger.info("Loading cross-encoder reranker %s (first load may take real time)...", self._model_name)
            self._model = CrossEncoder(self._model_name)
            logger.info("Cross-encoder reranker %s loaded", self._model_name)
        return self._model

    def score(self, query: str, passages: list[str]) -> list[float]:
        """Returns one real relevance score per passage, in the same order as `passages` --
        higher means more relevant to `query`. Never raises and never loads the model for an
        empty `passages` list (returns [] immediately)."""
        if not passages:
            return []
        model = self._get_model()
        scores = model.predict([(query, passage) for passage in passages])
        return [float(s) for s in scores]
