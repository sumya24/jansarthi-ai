"""Phoenix Dataset + Experiment for the Ask Sarthi RAG pipeline -- Phoenix's equivalent of
scripts/langsmith_rag_evaluation.py (see that script's own module docstring for the full
rationale; this one mirrors it exactly, just against Phoenix instead of LangSmith).

Uploads the SAME labeled dataset
(data/rag_knowledge_base/test_questions/retrieval_evaluation_dataset.json) as a real Phoenix
Dataset, runs the CURRENT production pipeline (RagRetriever + AnswerGenerationService, the real
embeddings/ChromaDB/Sarvam path) against it via `client.experiments.run_experiment()`, and scores
the same two things per case:

  1. retrieval_correctness -- deterministic: did the pipeline correctly decide the question was
     answerable/unanswerable from the knowledge base?
  2. groundedness -- an LLM-as-judge check (also via Sarvam): for answerable cases only, is every
     factual claim in the generated answer actually supported by the retrieved context?

Every run shows up in Phoenix under Datasets & Experiments.

This is a deliberately separate, manually-run script, not part of the test suite or CI -- same
convention as its LangSmith counterpart. Runs in this project's MAIN environment (NOT the isolated
.phoenix-venv/) -- unlike the full arize-phoenix server package, `arize-phoenix-client` (this
script's only Phoenix dependency) has a lightweight dependency tree that doesn't conflict with
this project's pinned versions (confirmed directly; see PHOENIX_TRACING_PLAN.md), so it's a normal
requirements.txt entry, letting this script reuse the real production pipeline objects directly
exactly like scripts/langsmith_rag_evaluation.py does.

Usage:
    python scripts/phoenix_rag_evaluation.py

Requires:
    - The Chroma index already built: python scripts/build_rag_embeddings.py
    - A running Phoenix server (see PHOENIX_TRACING_PLAN.md's "How to run this locally") --
      independent of PHOENIX_TRACING's app-level on/off gate, same "offline eval tool checks its
      own prerequisites, not the app's tracing switch" posture as the LangSmith script.
    - SARVAM_API_KEY (or LLM_API_KEY) set -- both the real answer generation and the groundedness
      judge call Sarvam.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phoenix.client import Client

from backend.config import settings
from backend.schemas.rag_knowledge import ServiceCategory
from backend.services.intent_classifier import classify
from backend.services.rag_retriever import chunk_context_label

DATASET_NAME = "jansarthi-ask-janmitra-rag-eval"
PHOENIX_BASE_URL = "http://localhost:6006"
_LANGUAGE_NAMES = {"en": "English", "hi": "Hindi", "mr": "Marathi", "or": "Odia", "gu": "Gujarati", "bn": "Bengali"}

_GROUNDEDNESS_JUDGE_PROMPT = """You are checking whether an AI-generated answer is fully supported by the context it was given.

CONTEXT (the only information the answer is allowed to use):
{context}

ANSWER (to check):
{answer}

Does the ANSWER state any fact, phone number, fee, procedure, or detail that is NOT present in the CONTEXT? \
Reply with exactly one word: "GROUNDED" if every claim in the answer is supported by the context, \
or "UNGROUNDED" if the answer adds anything not present in the context."""


def _load_dataset_cases() -> list[dict]:
    path = settings.RAG_DATA_DIR / "test_questions" / "retrieval_evaluation_dataset.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _build_pipeline():
    """Builds the real, current production RAG components -- see
    scripts/langsmith_rag_evaluation.py's own `_build_pipeline()` docstring, identical here
    (including its `reranker=`/`hybrid_search_enabled=` code-review fix)."""
    from backend.services.answer_generation_service import AnswerGenerationService
    from backend.services.ask_janmitra_service import AskJanMitraService
    from backend.services.embedding_provider import SentenceTransformerEmbeddingProvider
    from backend.services.rag_retriever import RagRetriever
    from backend.services.vector_store import ChromaVectorStore

    provider = SentenceTransformerEmbeddingProvider()
    provider.load()
    store = ChromaVectorStore(settings.CHROMA_PERSIST_DIR, settings.CHROMA_COLLECTION_NAME)
    store.load()
    retriever = RagRetriever(
        store, provider,
        top_k=settings.RAG_TOP_K,
        relevance_threshold=settings.RAG_EMBEDDING_RELEVANCE_THRESHOLD,
        reranker=AskJanMitraService._load_default_reranker(),
        hybrid_search_enabled=settings.RAG_HYBRID_SEARCH_ENABLED,
    )
    answer_service = AnswerGenerationService()
    return retriever, answer_service


def _ensure_dataset(client: Client, cases: list[dict]):
    """Creates (or reuses) the Phoenix Dataset, uploading every case as an example. Re-running
    this script is safe -- `add_examples_to_dataset`/`create_dataset` just add another version."""
    existing = next((d for d in client.datasets.list() if d["name"] == DATASET_NAME), None)
    inputs = [{"query": c["query"], "category": c["category"], "city": c["city"], "language": c["language"]} for c in cases]
    outputs = [{"answerable": c["answerable"]} for c in cases]
    metadata = [{"case_id": c["id"], "note": c.get("note")} for c in cases]
    if existing is None:
        return client.datasets.create_dataset(
            name=DATASET_NAME,
            inputs=inputs,
            outputs=outputs,
            metadata=metadata,
            dataset_description=(
                "Ask Sarthi RAG pipeline eval cases -- mirrors "
                "data/rag_knowledge_base/test_questions/retrieval_evaluation_dataset.json. "
                "See scripts/phoenix_rag_evaluation.py."
            ),
        )
    return client.datasets.add_examples_to_dataset(dataset=existing, inputs=inputs, outputs=outputs, metadata=metadata)


def make_task(retriever, answer_service):
    """Builds the task function `run_experiment()` calls once per dataset example. Same
    classify() -> retrieve() -> generate() sequence the real app uses -- see
    scripts/langsmith_rag_evaluation.py's `make_target()` docstring, identical logic here, just
    reading Phoenix's `DatasetExample["input"]` shape instead of LangSmith's plain `inputs` dict.
    """

    def task(example: dict) -> dict:
        inputs = example["input"]
        query = inputs["query"]
        category = ServiceCategory(inputs["category"]) if inputs["category"] else None
        language_name = _LANGUAGE_NAMES.get(inputs.get("language") or "en", "English")

        classification = classify(query)
        if classification.out_of_scope_service:
            return {"insufficient_knowledge": True, "answer": None, "context_chunks": []}

        outcome = retriever.retrieve(query, category, inputs.get("city"), None)
        if outcome.insufficient_knowledge or not outcome.results:
            return {"insufficient_knowledge": True, "answer": None, "context_chunks": []}

        context_chunks = [r.metadata["content"] for r in outcome.results]
        context_labels = [chunk_context_label(r) for r in outcome.results]
        answer_text, _was_llm, _token_usage = answer_service.generate(query, context_chunks, language_name, context_labels)
        return {"insufficient_knowledge": False, "answer": answer_text, "context_chunks": context_chunks}

    return task


def retrieval_correctness(output=None, expected=None, **_kwargs) -> dict:
    """Deterministic: did the pipeline's answerable/unanswerable decision match the label?"""
    expected_answerable = (expected or {}).get("answerable")
    actual_insufficient = bool((output or {}).get("insufficient_knowledge", True))
    predicted_answerable = not actual_insufficient
    return {"name": "retrieval_correctness", "score": 1.0 if predicted_answerable == expected_answerable else 0.0}


def make_groundedness_evaluator(judge_client):
    """LLM-as-judge groundedness check -- see scripts/langsmith_rag_evaluation.py's own
    `make_groundedness_evaluator()` docstring for why this specific gap matters, identical logic."""

    def groundedness(output=None, **_kwargs) -> dict:
        output = output or {}
        answer = output.get("answer")
        context_chunks = output.get("context_chunks") or []
        if not answer or not context_chunks:
            return {"name": "groundedness", "score": None, "explanation": "skipped -- no answer generated for this case"}

        prompt = _GROUNDEDNESS_JUDGE_PROMPT.format(context="\n\n---\n\n".join(context_chunks), answer=answer)
        try:
            response = judge_client.chat.completions(
                model=settings.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=20,
                reasoning_effort="low",
            )
            verdict = (response.choices[0].message.content or "").strip().upper()
            score = 1.0 if "UNGROUNDED" not in verdict and "GROUNDED" in verdict else 0.0
            return {"name": "groundedness", "score": score, "explanation": verdict}
        except Exception as exc:
            return {"name": "groundedness", "score": None, "explanation": f"judge call failed: {exc}"}

    return groundedness


def main() -> None:
    if not settings.LLM_API_KEY:
        raise SystemExit("LLM_API_KEY/SARVAM_API_KEY is not set (see .env) -- required for answer generation and the groundedness judge.")

    from sarvamai import SarvamAI

    client = Client(base_url=PHOENIX_BASE_URL)
    judge_client = SarvamAI(api_subscription_key=settings.LLM_API_KEY)

    print("Loading eval cases...")
    cases = _load_dataset_cases()
    print(f"  {len(cases)} cases")

    print("Uploading/refreshing Phoenix dataset...")
    dataset = _ensure_dataset(client, cases)
    print(f"  dataset: {DATASET_NAME}")

    print("Loading RAG pipeline (embedding model + Chroma collection)...")
    retriever, answer_service = _build_pipeline()

    print("Running evaluation (this makes real Sarvam LLM calls for every answerable case)...")
    ran = client.experiments.run_experiment(
        dataset=dataset,
        task=make_task(retriever, answer_service),
        evaluators=[retrieval_correctness, make_groundedness_evaluator(judge_client)],
        experiment_name="ask-janmitra-rag",
        experiment_description="Retrieval correctness + answer groundedness over the RAG eval dataset (see scripts/phoenix_rag_evaluation.py).",
    )

    print()
    print("Done. View results in Phoenix under Datasets & Experiments ->", DATASET_NAME)
    print(client.experiments.get_experiment_url(dataset_id=dataset.id, experiment_id=ran["experiment_id"]))


if __name__ == "__main__":
    main()
