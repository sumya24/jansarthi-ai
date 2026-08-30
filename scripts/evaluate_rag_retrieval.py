"""Retrieval evaluation: OLD (TF-IDF + FlatVectorStore) vs NEW (real embeddings + ChromaDB).

Runs data/rag_knowledge_base/test_questions/retrieval_evaluation_dataset.json through BOTH
retrieval engines at the RagRetriever layer (category+location metadata filtering applied
identically to both -- this isolates the comparison to embedding/scoring quality, not filtering
logic, which is shared code) and reports real, measured metrics -- retrieval hit rate for
answerable cases, correct rejection rate for unanswerable cases, broken down by case type
(exact-keyword / paraphrase / multilingual / cross-location / off-topic).

Requires BOTH indices to already be built:
    python scripts/build_rag_embeddings.py                 # Chroma (NEW)
    python scripts/build_rag_embeddings.py --legacy-tfidf   # flat TF-IDF (OLD)

Writes a report to data/rag_knowledge_base/reports/tfidf_vs_embeddings_comparison.md.

Note on "unanswerable_electricity" / "unanswerable_new_water_connection": these two cases in the
dataset are handled by backend/services/intent_classifier.py's out-of-scope keyword detection,
which runs BEFORE either retriever is ever called (see ask_sarthi_service.py) -- they are
reported here as N/A for both engines (not a retrieval-layer comparison point) and are already
covered end-to-end by tests/test_ask_sarthi.py's test_no_knowledge_available_says_so_honestly
and test_new_water_connection_does_not_retrieve_a_leak_repair_chunk.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings
from backend.schemas.rag_knowledge import ServiceCategory


_CLASSIFIER_LAYER_CASES = {"unanswerable_electricity", "unanswerable_new_water_connection"}


def _load_dataset() -> list[dict]:
    path = settings.RAG_DATA_DIR / "test_questions" / "retrieval_evaluation_dataset.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _build_new_retriever():
    from backend.services.embedding_provider import SentenceTransformerEmbeddingProvider
    from backend.services.vector_store import ChromaVectorStore
    from backend.services.rag_retriever import RagRetriever

    provider = SentenceTransformerEmbeddingProvider()
    t0 = time.perf_counter()
    provider.load()
    load_s = time.perf_counter() - t0
    store = ChromaVectorStore(settings.CHROMA_PERSIST_DIR, settings.CHROMA_COLLECTION_NAME)
    store.load()
    # BUG FIX (code review): hybrid search (BM25 keyword widening) is itself a scoring-methodology
    # change, not a filtering one -- reading settings.RAG_HYBRID_SEARCH_ENABLED here (as the OTHER
    # eval scripts now correctly do, to match live production behavior) would silently reintroduce
    # exactly the confound this module's own docstring says it isolates against ("embedding/
    # scoring quality, not filtering logic"). Explicitly disabled on BOTH retrievers below,
    # deliberately NOT settings-driven, so this comparison stays embedding-vs-TF-IDF only,
    # regardless of what production has hybrid search set to.
    return RagRetriever(store, provider, top_k=5, relevance_threshold=settings.RAG_EMBEDDING_RELEVANCE_THRESHOLD, hybrid_search_enabled=False), load_s


def _build_old_retriever():
    from backend.services.embedding_provider import TfidfEmbeddingProvider
    from backend.services.vector_store import FlatVectorStore
    from backend.services.rag_retriever import RagRetriever

    if not settings.RAG_EMBEDDINGS_INDEX_PATH.exists():
        raise SystemExit(
            f"{settings.RAG_EMBEDDINGS_INDEX_PATH} not found -- run "
            f"'python scripts/build_rag_embeddings.py --legacy-tfidf' first."
        )
    store = FlatVectorStore()
    t0 = time.perf_counter()
    store.load(settings.RAG_EMBEDDINGS_INDEX_PATH)
    load_s = time.perf_counter() - t0
    provider = TfidfEmbeddingProvider(idf=store.idf)
    # See _build_new_retriever()'s own comment -- hybrid search explicitly OFF here too, for the
    # same "isolate embedding quality, not scoring methodology" reason.
    return RagRetriever(store, provider, top_k=5, relevance_threshold=settings.RAG_RELEVANCE_THRESHOLD, hybrid_search_enabled=False), load_s


def _run_case(retriever, case: dict) -> dict:
    category = ServiceCategory(case["category"]) if case["category"] else None
    t0 = time.perf_counter()
    outcome = retriever.retrieve(case["query"], category, case["city"], None)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    found = not outcome.insufficient_knowledge and len(outcome.results) > 0
    correct = (found == case["answerable"])
    top_score = outcome.results[0].score if outcome.results else None
    return {"found": found, "correct": correct, "elapsed_ms": elapsed_ms, "top_score": top_score}


def main() -> None:
    dataset = _load_dataset()
    scored_cases = [c for c in dataset if c["id"] not in _CLASSIFIER_LAYER_CASES]

    print("Loading NEW (embeddings + ChromaDB) retriever...")
    new_retriever, new_load_s = _build_new_retriever()
    print(f"  loaded in {new_load_s:.1f}s")

    print("Loading OLD (TF-IDF + FlatVectorStore) retriever...")
    old_retriever, old_load_s = _build_old_retriever()
    print(f"  loaded in {old_load_s:.3f}s")

    rows = []
    for case in scored_cases:
        new_result = _run_case(new_retriever, case)
        old_result = _run_case(old_retriever, case)
        rows.append({"case": case, "new": new_result, "old": old_result})

    def _accuracy(engine: str, predicate) -> tuple[int, int]:
        subset = [r for r in rows if predicate(r["case"])]
        correct = sum(1 for r in subset if r[engine]["correct"])
        return correct, len(subset)

    categories = {
        "exact-keyword": lambda c: "exact" in c["id"],
        "paraphrase": lambda c: "paraphrase" in c["id"],
        "multilingual (non-English)": lambda c: c["language"] != "en",
        "cross-location": lambda c: c["id"].startswith("answerable_streetlight_") and c["language"] == "en" and "exact" not in c["id"] and "paraphrase" not in c["id"],
        "off-topic/unknown-location rejection": lambda c: not c["answerable"],
    }

    lines = []
    lines.append("# TF-IDF vs Real Embeddings: retrieval comparison\n")
    lines.append(f"Generated by `scripts/evaluate_rag_retrieval.py`. {len(scored_cases)} retrieval-layer cases "
                  f"evaluated (of {len(dataset)} total in the dataset; {len(_CLASSIFIER_LAYER_CASES)} excluded "
                  f"as classifier-layer, not retrieval-layer -- see module docstring).\n")
    lines.append(f"Model/index load time: OLD (TF-IDF) {old_load_s:.3f}s, NEW (embeddings) {new_load_s:.1f}s "
                  f"(one-time cost; NEW downloads/loads a neural model, OLD just parses a JSON file).\n")
    lines.append("| Case group | OLD (TF-IDF) accuracy | NEW (embeddings) accuracy |")
    lines.append("|---|---|---|")
    for label, predicate in categories.items():
        old_correct, n = _accuracy("old", predicate)
        new_correct, _ = _accuracy("new", predicate)
        lines.append(f"| {label} | {old_correct}/{n} | {new_correct}/{n} |")

    overall_old, total = _accuracy("old", lambda c: True)
    overall_new, _ = _accuracy("new", lambda c: True)
    lines.append(f"| **Overall** | **{overall_old}/{total}** | **{overall_new}/{total}** |\n")

    avg_ms_old = sum(r["old"]["elapsed_ms"] for r in rows) / len(rows)
    avg_ms_new = sum(r["new"]["elapsed_ms"] for r in rows) / len(rows)
    lines.append(f"Average per-query retrieval time (warm, after model load): "
                  f"OLD {avg_ms_old:.2f}ms, NEW {avg_ms_new:.2f}ms.\n")

    lines.append("## Per-case detail\n")
    lines.append("| id | answerable? | OLD found | OLD correct | OLD top score | NEW found | NEW correct | NEW top score |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        c, o, n = r["case"], r["old"], r["new"]
        o_score = f"{o['top_score']:.3f}" if o["top_score"] is not None else "-"
        n_score = f"{n['top_score']:.3f}" if n["top_score"] is not None else "-"
        lines.append(f"| {c['id']} | {c['answerable']} | {o['found']} | {'OK' if o['correct'] else 'WRONG'} | {o_score} "
                      f"| {n['found']} | {'OK' if n['correct'] else 'WRONG'} | {n_score} |")

    report = "\n".join(lines) + "\n"
    out_path = settings.RAG_DATA_DIR / "reports" / "tfidf_vs_embeddings_comparison.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"\nWrote {out_path}")
    print(f"\nOverall: OLD {overall_old}/{total}, NEW {overall_new}/{total}")


if __name__ == "__main__":
    main()
