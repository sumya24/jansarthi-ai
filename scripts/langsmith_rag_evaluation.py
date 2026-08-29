"""LangSmith Dataset + Experiment for the Ask Sarthi RAG pipeline.

Complements scripts/evaluate_rag_retrieval.py (which compares the OLD/NEW retrieval engines and
writes a local markdown report) rather than replacing it: this script uploads the SAME labeled
dataset (data/rag_knowledge_base/test_questions/retrieval_evaluation_dataset.json) as a real
LangSmith Dataset, runs the CURRENT production pipeline (RagRetriever + AnswerGenerationService,
the real embeddings/ChromaDB/Sarvam path) against it via `langsmith.evaluate()`, and scores two
things per case:

  1. retrieval_correctness -- deterministic: did the pipeline correctly decide the question was
     answerable/unanswerable from the knowledge base? (mirrors evaluate_rag_retrieval.py's own
     "hit rate" metric, but as a LangSmith-tracked score instead of a local report line)
  2. groundedness -- an LLM-as-judge check (also via Sarvam, matching this app's own LLM
     provider): for answerable cases only, is every factual claim in the generated answer
     actually supported by the retrieved context, or did the model add something not present in
     it? This is the check this project did NOT previously have -- see
     docs/ask_janmitra_langsmith_observability.md's "Evaluators" section for why it matters (the
     existing hallucination-prevention mechanisms are all upstream of generation -- retrieval
     filtering, prompt constraints, metadata-only citations -- nothing previously scored the
     generated text itself after the fact).

Every run this script performs shows up in LangSmith under Datasets & Experiments -- see that
same docs section for exactly where to look and how to read the results.

This is a deliberately separate, manually-run script, not part of the test suite or CI: it makes
real Sarvam LLM calls (cost + latency) and real LangSmith API calls, matching this project's
existing convention for its other scripts/*.py evaluation tools (none of them run in pytest
either -- see evaluate_rag_retrieval.py's own module docstring).

Usage:
    python scripts/langsmith_rag_evaluation.py

Requires:
    - The Chroma index already built: python scripts/build_rag_embeddings.py
    - LANGSMITH_TRACING=true and LANGSMITH_API_KEY set (see .env) -- this script uses the
      LangSmith client directly, independent of backend/services/observability/tracing.py's
      is_enabled() gate (that gate is for the *running app*; this is an offline eval tool, so it
      only checks that an API key is present, not the app's own tracing on/off switch).
    - SARVAM_API_KEY (or LLM_API_KEY) set -- both the real answer generation and the groundedness
      judge call Sarvam.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langsmith import Client
from langsmith.schemas import Example, Run

from backend.config import settings
from backend.schemas.rag_knowledge import ServiceCategory
from backend.services.intent_classifier import classify
from backend.services.rag_retriever import chunk_context_label

DATASET_NAME = "janmitra-ask-janmitra-rag-eval"
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
    """Builds the real, current production RAG components -- same construction as
    AskJanMitraService's own defaults (see that class's _load_default_store()/
    _load_default_embedding_provider()), not a mock. Deliberately slow/one-time (embedding model
    + Chroma collection load) -- built once, reused for every case in the dataset.

    BUG FIX (code review): this used to omit `reranker=`/`hybrid_search_enabled=`, so toggling
    RAG_RERANKER_ENABLED/RAG_HYBRID_SEARCH_ENABLED in production silently stopped being reflected
    here -- this script's own docstring claim of matching "AskJanMitraService's own defaults" is
    only actually true once both are wired the same way."""
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


def _ensure_dataset(client: Client, cases: list[dict]) -> str:
    """Creates (or reuses) the LangSmith Dataset, uploading every case as an Example. Re-running
    this script is safe -- `create_examples` is called fresh each time, so LangSmith just tracks
    another version of the dataset if the case list changed since the last run."""
    if client.has_dataset(dataset_name=DATASET_NAME):
        dataset = client.read_dataset(dataset_name=DATASET_NAME)
    else:
        dataset = client.create_dataset(
            DATASET_NAME,
            description=(
                "Ask Sarthi RAG pipeline eval cases -- mirrors "
                "data/rag_knowledge_base/test_questions/retrieval_evaluation_dataset.json. "
                "See scripts/langsmith_rag_evaluation.py."
            ),
        )
    client.create_examples(
        dataset_id=dataset.id,
        examples=[
            {
                "inputs": {"query": c["query"], "category": c["category"], "city": c["city"], "language": c["language"]},
                "outputs": {"answerable": c["answerable"]},
                "metadata": {"case_id": c["id"], "note": c.get("note")},
            }
            for c in cases
        ],
    )
    return dataset.name


def make_target(retriever, answer_service):
    """Builds the target function `evaluate()` calls once per dataset example. Runs the SAME
    classify() -> retrieve() -> generate() sequence `rag_flow_node`/`out_of_scope_flow_node` use
    in the real app (see backend/services/orchestration/nodes.py) -- not a reimplementation, just
    this eval's own thin driver over the same real service objects.
    """

    def target(inputs: dict) -> dict:
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

    return target


def retrieval_correctness(run: Run, example: Example) -> dict:
    """Deterministic: did the pipeline's answerable/unanswerable decision match the label?"""
    expected_answerable = example.outputs["answerable"]
    actual_insufficient = bool(run.outputs.get("insufficient_knowledge")) if run.outputs else True
    predicted_answerable = not actual_insufficient
    return {"key": "retrieval_correctness", "score": 1.0 if predicted_answerable == expected_answerable else 0.0}


def make_groundedness_evaluator(judge_client):
    """LLM-as-judge groundedness check -- see this module's docstring for why this is the
    specific gap it fills. Scored only for cases that actually produced an answer; unanswerable
    cases (no answer generated) are skipped, not scored 0 -- there's nothing to judge."""

    def groundedness(run: Run, example: Example) -> dict:
        outputs = run.outputs or {}
        answer = outputs.get("answer")
        context_chunks = outputs.get("context_chunks") or []
        if not answer or not context_chunks:
            return {"key": "groundedness", "score": None, "comment": "skipped -- no answer generated for this case"}

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
            return {"key": "groundedness", "score": score, "comment": verdict}
        except Exception as exc:
            return {"key": "groundedness", "score": None, "comment": f"judge call failed: {exc}"}

    return groundedness


def main() -> None:
    if not settings.LANGSMITH_API_KEY:
        raise SystemExit("LANGSMITH_API_KEY is not set (see .env) -- required to upload the dataset and record results.")
    if not settings.LLM_API_KEY:
        raise SystemExit("LLM_API_KEY/SARVAM_API_KEY is not set (see .env) -- required for answer generation and the groundedness judge.")

    from sarvamai import SarvamAI

    client = Client(api_key=settings.LANGSMITH_API_KEY, api_url=settings.LANGSMITH_ENDPOINT)
    judge_client = SarvamAI(api_subscription_key=settings.LLM_API_KEY)

    print("Loading eval cases...")
    cases = _load_dataset_cases()
    print(f"  {len(cases)} cases")

    print("Uploading/refreshing LangSmith dataset...")
    dataset_name = _ensure_dataset(client, cases)
    print(f"  dataset: {dataset_name}")

    print("Loading RAG pipeline (embedding model + Chroma collection)...")
    retriever, answer_service = _build_pipeline()

    print("Running evaluation (this makes real Sarvam LLM calls for every answerable case)...")
    results = client.evaluate(
        make_target(retriever, answer_service),
        data=dataset_name,
        evaluators=[retrieval_correctness, make_groundedness_evaluator(judge_client)],
        experiment_prefix="ask-janmitra-rag",
        description="Retrieval correctness + answer groundedness over the RAG eval dataset (see scripts/langsmith_rag_evaluation.py).",
        max_concurrency=2,
    )

    print()
    print("Done. View results in LangSmith under Datasets & Experiments ->", dataset_name)
    print(f"Experiment URL: {results.url}")


if __name__ == "__main__":
    main()
