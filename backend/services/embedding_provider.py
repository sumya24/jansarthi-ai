"""Turns text into vectors for semantic search over the RAG knowledge base.

**Active default: `SentenceTransformerEmbeddingProvider`** (real neural embeddings, see below).

**Legacy/comparison-only: `TfidfEmbeddingProvider`.** Classical TF-IDF (term frequency - inverse
document frequency) bag-of-words vectors, pure Python, zero dependencies beyond the standard
library. This was the original implementation (chosen when this project deliberately avoided a
neural embedding stack, back when it was still a lightweight prototype — see git history for that
reasoning). Kept in this file, not deleted, specifically so the before/after retrieval comparison
in docs/ask_sarthi_rag_architecture.md is reproducible — but no longer imported by
`ask_sarthi_service.py`'s default wiring. Its honest limitation, unchanged: TF-IDF has no notion
of synonyms or semantic paraphrase — "bin collection" will not match a chunk that only says "solid
waste" unless a shared keyword links them.

---

`SentenceTransformerEmbeddingProvider` wraps a real, pretrained multilingual sentence-embedding
model via the `sentence-transformers` library. Chosen deliberately over the alternatives actually
considered:
- The Sarvam AI SDK (this project's only other AI provider) exposes no embeddings endpoint at all
  (checked directly: `dir(SarvamAI(...))` has no `embeddings` attribute).
- ChromaDB's own bundled default embedding function (`all-MiniLM-L6-v2` via onnxruntime, already
  installed as a chromadb dependency) is English-only in practice -- not acceptable given this
  project's explicit English/Hindi/Marathi/Odia requirement.
- `intfloat/multilingual-e5-small` (the model actually used) was chosen after checking: (a) it's
  MIT-licensed, (b) it downloads from HuggingFace (confirmed reachable from this environment,
  HTTP 200), (c) it's trained on mC4 (~100 languages), which empirically covers Odia noticeably
  better than the more common "50-language" LaBSE-style multilingual models (verified directly --
  see the model-selection test transcript in docs/ask_sarthi_rag_architecture.md, not assumed
  from the model card alone), (d) "small" variant (384 dimensions, ~118M params) runs acceptably
  on CPU on this dev machine with no GPU.

Swappable by design: every retrieval-layer component depends only on `embed_query`/`embed`
(document-side) methods, whichever provider implements them -- a future provider (a hosted
embeddings API, a larger local model, etc.) is a drop-in replacement.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field

_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase, alphanumeric-only tokenization. Deliberately simple and deterministic --
    no stemming/lemmatization (would need an NLP library this project doesn't have), and this
    corpus's vocabulary (service names, department names, city names) doesn't have much
    inflectional variation for stemming to help with anyway."""
    return _TOKEN_PATTERN.findall(text.lower())


SparseVector = dict[str, float]


@dataclass
class TfidfEmbeddingProvider:
    """Fits a TF-IDF vocabulary from a corpus, then embeds new text against it.

    Attributes:
        idf: token -> inverse-document-frequency weight, populated by `fit()`.
        vocabulary_size: len(idf) after fitting -- 0 before `fit()` is called.
    """

    idf: dict[str, float] = field(default_factory=dict)

    @property
    def vocabulary_size(self) -> int:
        return len(self.idf)

    def fit(self, documents: list[str]) -> None:
        """Builds the IDF table from a corpus. Must be called once before `embed`/`embed_query`
        are meaningful -- an unfit provider has an empty vocabulary and embeds everything to {}.
        """
        doc_count = len(documents)
        if doc_count == 0:
            self.idf = {}
            return
        document_frequency: Counter[str] = Counter()
        for doc in documents:
            for token in set(tokenize(doc)):
                document_frequency[token] += 1
        # Standard smoothed IDF: log((1 + N) / (1 + df)) + 1 -- always positive, never divides by
        # zero even for a token that (implausibly) appears in every document.
        self.idf = {
            token: math.log((1 + doc_count) / (1 + df)) + 1.0
            for token, df in document_frequency.items()
        }

    def embed(self, text: str) -> SparseVector:
        """Embeds one piece of text into a sparse TF-IDF vector (token -> weight), L2-normalized
        so cosine similarity reduces to a plain dot product at search time. Tokens not in the
        fitted vocabulary are silently dropped (out-of-vocabulary) -- documented, not a bug: a
        genuinely novel word can't contribute to a classical bag-of-words match regardless."""
        tokens = tokenize(text)
        if not tokens:
            return {}
        term_frequency = Counter(tokens)
        max_tf = max(term_frequency.values())
        vector: SparseVector = {}
        for token, count in term_frequency.items():
            idf = self.idf.get(token)
            if idf is None:
                continue  # out-of-vocabulary, see docstring
            # Augmented term frequency (0.5 + 0.5 * tf/max_tf) avoids over-weighting very long
            # chunks purely because a word repeats more often in more text.
            tf = 0.5 + 0.5 * (count / max_tf)
            vector[token] = tf * idf
        norm = math.sqrt(sum(w * w for w in vector.values()))
        if norm > 0:
            vector = {token: w / norm for token, w in vector.items()}
        return vector

    def embed_query(self, text: str) -> SparseVector:
        """Same computation as `embed` -- a separate method only because a future neural provider
        may legitimately need a different code path for queries vs. documents (e.g. an
        asymmetric dual-encoder); this classical provider's two methods are identical on purpose."""
        return self.embed(text)


def cosine_similarity(a: SparseVector, b: SparseVector) -> float:
    """Cosine similarity between two sparse vectors already L2-normalized by `embed()` --
    reduces to a plain dot product over shared keys. Returns 0.0 for either empty vector
    (an empty/all-out-of-vocabulary query or chunk has no meaningful similarity to anything)."""
    if not a or not b:
        return 0.0
    # Iterate the smaller dict for a cheap constant-factor speedup -- irrelevant at this corpus
    # size (504 chunks), kept only because it's free and doesn't hurt readability.
    smaller, larger = (a, b) if len(a) <= len(b) else (b, a)
    return sum(weight * larger.get(token, 0.0) for token, weight in smaller.items())


def cosine_similarity_any(a: SparseVector | DenseVector, b: SparseVector | DenseVector) -> float:
    """Same job as `cosine_similarity` above, but works for either vector representation this
    codebase actually uses -- `SparseVector` (dict, TF-IDF) or `DenseVector` (list, sentence
    embeddings) -- so callers that don't know (or care) which embedding provider is active, like
    `RagRetriever`'s hybrid-search candidate scoring, can call one function regardless. Dense
    vectors are already L2-normalized by both `embed`/`embed_query` implementations (see
    `SentenceTransformerEmbeddingProvider`'s `normalize_embeddings=True`), so cosine similarity
    reduces to a plain dot product there exactly as it does for the sparse case."""
    if isinstance(a, dict) or isinstance(b, dict):
        return cosine_similarity(a, b)  # type: ignore[arg-type]
    if not a or not b:
        return 0.0
    return sum(x * y for x, y in zip(a, b))


DenseVector = list[float]

logger = logging.getLogger(__name__)


class SentenceTransformerEmbeddingProvider:
    """Real neural sentence embeddings via `sentence-transformers` -- the active default
    embedding provider (see module docstring). Loaded lazily (model load takes real time, ~30s+
    even from local cache -- see docs/ask_sarthi_rag_architecture.md's performance section) so
    importing this module never pays that cost, only actually constructing/using a provider does.

    E5-family models (intfloat/multilingual-e5-*) require a "query: " / "passage: " text prefix
    for their intended (and empirically much better -- see the module docstring's model-selection
    test) asymmetric retrieval usage: queries get one prefix, indexed documents get the other.
    This is NOT optional decoration -- comparing two "passage:"-prefixed texts against each other
    (instead of query-vs-passage) was tried during model selection and produced compressed,
    less-informative similarity scores; see git history for that comparison.
    """

    def __init__(self, model_name: str | None = None) -> None:
        from backend.config import settings

        self._model_name = model_name or settings.EMBEDDING_MODEL_NAME
        self._model = None  # lazy-loaded, see _get_model

    @property
    def model_name(self) -> str:
        return self._model_name

    def load(self) -> None:
        """Forces the (slow, first-time) model load now rather than lazily on first embed call --
        useful for ingestion scripts that want to time model-loading separately from embedding
        (see scripts/build_rag_embeddings.py)."""
        self._get_model()

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model %s (first load may take ~30s+)...", self._model_name)
            self._model = SentenceTransformer(self._model_name)
            logger.info("Embedding model %s loaded (dimension=%d)", self._model_name, self.dimension)
        return self._model

    @property
    def dimension(self) -> int:
        return self._get_model().get_embedding_dimension()

    def embed(self, text: str) -> DenseVector:
        """Embeds one piece of DOCUMENT/passage text (indexing side) -- "passage: " prefix."""
        vector = self._get_model().encode(f"passage: {text}", normalize_embeddings=True)
        return vector.tolist()

    def embed_query(self, text: str) -> DenseVector:
        """Embeds one piece of QUERY text (search side) -- "query: " prefix. See class docstring
        for why this must differ from embed()."""
        vector = self._get_model().encode(f"query: {text}", normalize_embeddings=True)
        return vector.tolist()

    def embed_batch(self, texts: list[str], is_query: bool = False) -> list[DenseVector]:
        """Batched embedding -- used by the ingestion script (scripts/build_rag_embeddings.py)
        so 504 chunks are encoded in one batched model call instead of 504 separate ones (real
        latency difference -- see that script's timing output)."""
        prefix = "query: " if is_query else "passage: "
        vectors = self._get_model().encode([f"{prefix}{t}" for t in texts], normalize_embeddings=True)
        return vectors.tolist()
