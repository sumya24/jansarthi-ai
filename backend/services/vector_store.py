"""Vector stores for the RAG chunk corpus.

**Active default: `ChromaVectorStore`** (persistent, on-disk, real vector database — see below).

**Legacy/comparison-only: `FlatVectorStore`.** Brute-force cosine similarity over an in-memory
list, loaded from a single JSON file. This was the original implementation (504 chunks makes
exhaustive search trivially fast — no scale problem an ANN index would solve at that size). Kept
in this file, not deleted, so the before/after retrieval comparison in
docs/ask_janmitra_rag_architecture.md is reproducible, but no longer imported by
`ask_janmitra_service.py`'s default wiring.

Both stores share one filter representation: `metadata_filter: dict[str, str] | None`, an
exact-match `{field: value}` spec (e.g. `{"service_category": "STREETLIGHTS", "city": "Mohali"}`)
-- deliberately NOT a Python callable (the original `FlatVectorStore` design), because ChromaDB's
`where` clause can't execute arbitrary Python; a plain dict maps directly onto both a Python
dict-equality check and Chroma's `$and`/`$eq` filter syntax.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.services.embedding_provider import DenseVector, SparseVector, cosine_similarity

logger = logging.getLogger(__name__)


@dataclass
class ScoredChunk:
    """One vector-search result: a chunk's full metadata plus its similarity score for this query."""

    chunk_id: str
    score: float
    metadata: dict[str, Any]


def _matches(metadata: dict[str, Any], metadata_filter: dict[str, str] | None) -> bool:
    if not metadata_filter:
        return True
    return all(metadata.get(key) == value for key, value in metadata_filter.items())


class FlatVectorStore:
    """Brute-force in-memory vector store (legacy/comparison path — see module docstring). Call
    `load()` once, then `search()` as many times as needed."""

    def __init__(self) -> None:
        self._vectors: list[tuple[str, SparseVector, dict[str, Any]]] = []
        self._idf: dict[str, float] = {}
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def size(self) -> int:
        return len(self._vectors)

    @property
    def idf(self) -> dict[str, float]:
        return self._idf

    def load(self, index_path: Path) -> None:
        """Loads a previously-built index (see scripts/build_rag_embeddings.py --legacy-tfidf).
        Raises FileNotFoundError with a clear message if the index hasn't been built yet."""
        if not index_path.exists():
            raise FileNotFoundError(
                f"RAG TF-IDF index not found at {index_path}. Run "
                f"'python scripts/build_rag_embeddings.py --legacy-tfidf' to build it."
            )
        data = json.loads(index_path.read_text(encoding="utf-8"))
        self._idf = data["idf"]
        self._vectors = [
            (entry["chunk_id"], entry["vector"], entry["metadata"]) for entry in data["chunks"]
        ]
        self._loaded = True

    def search(
        self,
        query_vector: SparseVector,
        top_k: int = 5,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[ScoredChunk]:
        """Returns the top_k chunks by cosine similarity to query_vector, restricted to chunks
        matching metadata_filter exactly (if given). Filtering happens BEFORE scoring is used for
        ranking -- every chunk that fails the filter is fully excluded, never included with a
        penalty, so a location/category filter can never be "outvoted" by a high text score from
        the wrong place (see rag_retriever.py's cross-location-contamination tests)."""
        candidates = [c for c in self._vectors if _matches(c[2], metadata_filter)]
        scored = [
            ScoredChunk(chunk_id=chunk_id, score=cosine_similarity(query_vector, vector), metadata=metadata)
            for chunk_id, vector, metadata in candidates
        ]
        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:top_k]

    def get_candidates(self, metadata_filter: dict[str, str] | None = None) -> list[tuple[str, str, dict[str, Any], SparseVector]]:
        """Returns every chunk matching metadata_filter (no ranking, no top_k cap) as
        `(chunk_id, content, metadata, vector)` -- the full filtered candidate pool a caller
        builds its own index over. Added for hybrid search (see rag_retriever.py's module
        docstring): `RagRetriever` builds a BM25 keyword index from this pool, scoped to the exact
        same metadata filter vector search already applied, so BM25 can surface an exact-keyword
        match vector search's `top_k * 3` ANN window happened to miss -- without ever widening
        *which* chunks are eligible in the first place."""
        return [
            (chunk_id, metadata.get("content", ""), metadata, vector)
            for chunk_id, vector, metadata in self._vectors
            if _matches(metadata, metadata_filter)
        ]


class ChromaVectorStore:
    """Persistent, on-disk vector database via ChromaDB (`chromadb.PersistentClient`) -- the
    active default vector store (see module docstring).

    Distance metric: cosine (configured via the collection's `hnsw:space` metadata at creation
    time). Since `SentenceTransformerEmbeddingProvider` always L2-normalizes its output, cosine
    distance and Euclidean distance would rank identically here -- cosine is used because it's
    the conventional choice for sentence embeddings and makes the score's meaning ("1 = identical
    direction, 0 = orthogonal") independent of vector magnitude by construction, not just by
    this provider's normalization choice.

    ChromaDB requires every metadata value to be a str/int/float/bool -- `None` is not accepted.
    Fields that are `None` on a chunk (district, city, zone, ward, area, source_url) are simply
    OMITTED from the stored metadata dict, never coerced to an empty string or a sentinel value
    that could be misread as real data — `metadata.get(field)` returns `None` for an omitted key
    exactly as it would for an explicit `None`, so nothing downstream needs to know the
    difference.
    """

    def __init__(self, persist_dir: Path, collection_name: str) -> None:
        self._persist_dir = persist_dir
        self._collection_name = collection_name
        self._client = None
        self._collection = None

    def _get_client(self):
        if self._client is None:
            import chromadb

            self._persist_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self._persist_dir))
        return self._client

    @property
    def is_loaded(self) -> bool:
        """True once a collection has been opened (get_or_create'd) this process -- ChromaDB
        itself persists to disk regardless (see the module's persistence test), this just tracks
        whether *this* store instance has connected to it yet."""
        return self._collection is not None

    def load(self, index_path: Path | None = None) -> None:
        """Opens (creating if necessary) the persistent Chroma collection. `index_path` is
        accepted only for interface-compatibility with `FlatVectorStore.load()` and ignored --
        ChromaDB's "index" is the persistent directory itself (`CHROMA_PERSIST_DIR`), not a
        single JSON file; see scripts/build_rag_embeddings.py for how it's populated."""
        client = self._get_client()
        self._collection = client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "Chroma collection '%s' opened at %s (%d chunk(s) currently indexed)",
            self._collection_name, self._persist_dir, self._collection.count(),
        )

    @property
    def size(self) -> int:
        if self._collection is None:
            return 0
        return self._collection.count()

    def upsert(self, chunk_ids: list[str], vectors: list[DenseVector], documents: list[str], metadatas: list[dict[str, Any]]) -> None:
        """Adds or updates chunks by ID -- re-running ingestion with the same chunk_ids overwrites
        rather than duplicating (see scripts/build_rag_embeddings.py and the upsert-idempotency
        test in tests/test_ask_janmitra.py)."""
        if self._collection is None:
            self.load()
        # ChromaDB metadata values must be str/int/float/bool -- filter out None values here
        # rather than at every call site (see class docstring).
        cleaned = [{k: v for k, v in m.items() if v is not None} for m in metadatas]
        self._collection.upsert(ids=chunk_ids, embeddings=vectors, documents=documents, metadatas=cleaned)

    @staticmethod
    def _build_where(metadata_filter: dict[str, str] | None) -> dict[str, Any] | None:
        """Translates this codebase's plain `{field: value}` filter dict into Chroma's `where`
        clause syntax -- shared by `search()` and `get_candidates()` since both need the exact
        same metadata filter applied, just via different Chroma query methods (see
        `get_candidates()`'s docstring for why it can't just call `search()` internally)."""
        if not metadata_filter:
            return None
        if len(metadata_filter) == 1:
            (key, value), = metadata_filter.items()
            return {key: {"$eq": value}}
        return {"$and": [{key: {"$eq": value}} for key, value in metadata_filter.items()]}

    def search(
        self,
        query_vector: DenseVector,
        top_k: int = 5,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[ScoredChunk]:
        if self._collection is None:
            self.load()
        if self._collection.count() == 0:
            return []

        where = self._build_where(metadata_filter)

        # Chroma can return fewer than n_results if fewer chunks match `where` -- never an error,
        # just fewer candidates, exactly like FlatVectorStore's post-filter behavior.
        n_results = min(top_k, self._collection.count())
        results = self._collection.query(
            query_embeddings=[query_vector], n_results=n_results, where=where,
            include=["documents", "metadatas", "distances"],
        )
        ids = results["ids"][0]
        distances = results["distances"][0]
        metadatas = results["metadatas"][0]
        documents = results["documents"][0]

        scored = []
        for chunk_id, distance, metadata, document in zip(ids, distances, metadatas, documents):
            # Cosine SPACE in Chroma returns cosine DISTANCE (0 = identical, 2 = opposite) for
            # normalized vectors -- convert to a similarity score (1 = identical, -1 = opposite)
            # so this store's `score` has the same "higher = more relevant" meaning
            # FlatVectorStore's cosine_similarity already has, and RagRetriever's threshold logic
            # doesn't need to know which store produced the result.
            score = 1.0 - distance
            # Chroma stores chunk text separately as a "document", not inside `metadatas` (unlike
            # FlatVectorStore, whose metadata dict already includes "content" -- see
            # build_rag_embeddings.py's build_legacy_tfidf_index). Merge it in here under the same
            # "content" key so every caller (ask_janmitra_service.py's `r.metadata["content"]`)
            # can read chunk text the same way regardless of which store produced the result.
            merged_metadata = dict(metadata)
            merged_metadata["content"] = document
            scored.append(ScoredChunk(chunk_id=chunk_id, score=score, metadata=merged_metadata))
        return scored

    def get_candidates(self, metadata_filter: dict[str, str] | None = None) -> list[tuple[str, str, dict[str, Any], DenseVector]]:
        """Same contract as `FlatVectorStore.get_candidates()` -- see that docstring. Uses
        Chroma's `.get()` (a pure metadata-filtered fetch, no vector ranking at all), not
        `.query()` (used by `search()` above, which always ranks by vector similarity) -- `.get()`
        is the only way to retrieve the *entire* filtered pool rather than just a ranked top_k."""
        if self._collection is None:
            self.load()
        if self._collection.count() == 0:
            return []

        where = self._build_where(metadata_filter)
        results = self._collection.get(where=where, include=["documents", "metadatas", "embeddings"])
        ids = results["ids"]
        documents = results["documents"]
        metadatas = results["metadatas"]
        embeddings = results["embeddings"]
        return [
            (chunk_id, document, metadata, list(embedding))
            for chunk_id, document, metadata, embedding in zip(ids, documents, metadatas, embeddings)
        ]
