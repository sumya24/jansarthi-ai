"""MCP (Model Context Protocol) tool wrapper around JanSarthi AI's existing RAG retrieval and
complaint services -- a thin adapter layer, no new business logic. Every tool below calls straight
into an existing, already-tested service function (`RagRetriever.retrieve()`,
`complaint_repository.get_complaint_by_id()`, `ComplaintAgent.create_complaint()`); nothing here
reimplements retrieval, complaint creation, or status lookup.

Why this exists: exposing these services via the official `mcp` Python SDK
(https://modelcontextprotocol.io) lets ANY MCP-compatible agent/client (Claude Desktop, a future
internal supervisor/multi-agent node, or any other MCP host) call into this app's real services
directly, without needing to know this is a FastAPI app at all. This is independently useful (an
ops/support engineer's own Claude Desktop could ask "what's the status of complaint #42?") and is
also the foundation a future supervisor agent could be built on -- MCP tools are a natural
substrate for tool-calling/agentic orchestration, whether or not this specific app ever adds one.

Run standalone (stdio transport, the standard local-MCP-client mode -- see
https://modelcontextprotocol.io/docs/concepts/transports):

    python -m backend.mcp_server

then point an MCP client (e.g. Claude Desktop's `mcp_config.json`) at that command.

Security note, stated honestly rather than left implicit: `get_complaint_status` here does NOT
enforce the citizen-ownership check `orchestration/nodes.py`'s `status_flow_node` applies for a
normal citizen-facing Ask Sarthi request (see that function's own docstring: "never leaks which
IDs exist to a citizen who isn't the owner"). This server has no per-caller identity/auth model of
its own -- MCP's own auth layer (`FastMCP`'s `auth`/`token_verifier` constructor params) is NOT
configured here. This is appropriate for a trusted, backend-operator-facing MCP client (an ops/
support agent, an internal automation), and NOT yet appropriate to expose to an untrusted or
citizen-facing MCP client without adding that auth layer first -- a real, documented limitation,
not something this module claims to already solve.
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from backend.config import settings
from backend.database import SessionLocal
from backend.repositories import complaint_repository
from backend.schemas.rag_knowledge import ServiceCategory
from backend.services.ask_janmitra_service import AskJanMitraService
from backend.services.complaint_agent import ComplaintAgent
from backend.services.rag_retriever import RagRetriever
from backend.services.sarvam_client import AIServiceError

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "jansarthi-ai",
    instructions=(
        "Tools for JanSarthi AI's civic-grievance knowledge base and complaint records. "
        "search_civic_knowledge_base answers civic-service questions from this deployment's real, "
        "verified/synthetic knowledge base -- never fabricates an answer when nothing relevant is "
        "found. get_complaint_status looks up an existing complaint by its numeric ID. "
        "file_complaint creates a new complaint record from citizen-reported text."
    ),
)

# Lazily constructed on first tool call, exactly like AskJanMitraService's own default-loading
# helpers this reuses -- importing this module (or starting the MCP server) never pays the real
# embedding-model/Chroma-collection load cost until a tool is actually invoked.
_retriever: RagRetriever | None = None
_complaint_agent: ComplaintAgent | None = None


def _get_retriever() -> RagRetriever:
    global _retriever
    if _retriever is None:
        # Reuses AskJanMitraService's own default-loading staticmethods (ChromaVectorStore +
        # SentenceTransformerEmbeddingProvider + the reranker -- the exact same active-default
        # retrieval stack the real HTTP API uses) instead of duplicating that wiring here.
        #
        # BUG FIX (code review): this used to omit `_load_default_reranker()`, silently
        # contradicting this module's own docstring claim that search_civic_knowledge_base uses
        # "the same retrieval pipeline...same VERIFIED-preference rerank". With
        # RAG_RERANKER_ENABLED=true, the HTTP API would rerank results via the cross-encoder while
        # this tool kept returning heuristic-only-ranked results for the identical query -- a
        # silent divergence. Reusing the same staticmethod here means the two can never drift
        # again: whatever AskJanMitraService's real wiring does, this tool now does too.
        store = AskJanMitraService._load_default_store()
        provider = AskJanMitraService._load_default_embedding_provider()
        reranker = AskJanMitraService._load_default_reranker()
        _retriever = RagRetriever(
            store,
            provider,
            top_k=settings.RAG_TOP_K,
            relevance_threshold=settings.RAG_EMBEDDING_RELEVANCE_THRESHOLD,
            verified_relevance_threshold=settings.RAG_VERIFIED_RELEVANCE_THRESHOLD,
            reranker=reranker,
            hybrid_search_enabled=settings.RAG_HYBRID_SEARCH_ENABLED,
        )
    return _retriever


def _get_complaint_agent() -> ComplaintAgent:
    global _complaint_agent
    if _complaint_agent is None:
        _complaint_agent = ComplaintAgent()
    return _complaint_agent


@mcp.tool()
def search_civic_knowledge_base(
    query: str,
    service_category: str | None = None,
    city: str | None = None,
    state: str | None = None,
) -> dict:
    """Search JanSarthi AI's real civic-service knowledge base -- the same retrieval pipeline the
    /ask-janmitra endpoint's RAG path uses (same thresholds, same VERIFIED-preference rerank,
    same citation-honesty filtering; see backend/services/rag_retriever.py).

    `service_category`, if given, must be one of the ServiceCategory enum values, e.g.
    "STREETLIGHTS", "WASTE_SANITATION", "ROADS_POTHOLES", "WATER_SUPPLY_DRAINAGE".

    Returns `{"insufficient_knowledge": bool, "reason": str | None, "results": [...]}`. Each
    result has `content`, `verification_status` ("VERIFIED" or "SYNTHETIC"), `source_title`,
    `source_organization`, `source_url`, and `score` (real cosine similarity to the query). Never
    fabricates an answer: when `insufficient_knowledge` is True, `results` is empty and `reason`
    explains why (no matching records, or nothing cleared the relevance threshold).
    """
    category_enum = ServiceCategory(service_category) if service_category else None
    outcome = _get_retriever().retrieve(query, category_enum, city, state)
    return {
        "insufficient_knowledge": outcome.insufficient_knowledge,
        "reason": outcome.reason,
        "results": [
            {
                "content": c.metadata.get("content", ""),
                "verification_status": c.metadata.get("verification_status"),
                "source_title": c.metadata.get("source_title"),
                "source_organization": c.metadata.get("source_organization"),
                "source_url": c.metadata.get("source_url"),
                "score": round(c.score, 4),
            }
            for c in outcome.results
        ],
    }


@mcp.tool()
def get_complaint_status(complaint_id: int) -> dict:
    """Look up an existing complaint's status by its numeric ID. See this module's own docstring
    for an important limitation: this tool does NOT enforce citizen-ownership -- it is meant for a
    trusted, backend-operator-facing MCP client, not a citizen-facing one.

    Returns `{"found": False}` if no complaint with that ID exists, or `{"found": True, "id",
    "status", "service_category", "ward", "summary", "created_at"}` otherwise.
    """
    db = SessionLocal()
    try:
        complaint = complaint_repository.get_complaint_by_id(db, complaint_id)
        if complaint is None:
            return {"found": False}
        return {
            "found": True,
            "id": complaint.id,
            "status": complaint.status,
            "service_category": complaint.service_category,
            "ward": complaint.ward,
            "summary": complaint.summary,
            "created_at": complaint.created_at.isoformat() if complaint.created_at else None,
        }
    finally:
        db.close()


@mcp.tool()
def file_complaint(
    citizen_id: str,
    text: str,
    language_code: str = "en",
    service_category: str | None = None,
) -> dict:
    """Files a new complaint from citizen-reported text. Text only -- no audio/photo support via
    this tool (see this module's docstring: a deliberately thin adapter, not a reimplementation of
    orchestration/nodes.py's full complaint_flow_node). Does NOT perform ward resolution or worker
    assignment -- this only creates the base complaint record via the same `ComplaintAgent.
    create_complaint()` the citizen-facing pipeline itself calls first.

    Returns `{"id", "status", "summary"}` on success, or `{"error": str}` if creation failed (e.g.
    empty text, or a translation/summarization service failure).
    """
    category_enum = ServiceCategory(service_category) if service_category else None
    db = SessionLocal()
    try:
        complaint = _get_complaint_agent().create_complaint(
            db=db,
            citizen_id=citizen_id,
            language_code=language_code,
            text=text,
            audio_chunks=None,
            photo_path=None,
            category=category_enum,
        )
        return {"id": complaint.id, "status": complaint.status, "summary": complaint.summary}
    except (ValueError, AIServiceError) as exc:
        logger.warning("MCP file_complaint failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
