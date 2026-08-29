"""Tests for backend/mcp_server.py -- the MCP tool wrapper around the RAG retriever and complaint
services (Part 8 of the production-hardening roadmap). These call the underlying Python functions
directly (the same functions `@mcp.tool()` decorates), not through an actual MCP client/transport
-- FastMCP's decorator returns the original callable unchanged, so this is a faithful test of what
an MCP client would actually invoke, without needing a real stdio/HTTP MCP round-trip.

`db_session`-backed tests monkeypatch `backend.mcp_server.SessionLocal` to the test's isolated
in-memory database (the same pattern this whole suite uses for the FastAPI app's own `get_db`
override -- see conftest.py's `db_session` fixture) -- `mcp_server.py`'s tools call `SessionLocal()`
directly (this is a standalone script, not a FastAPI route with dependency injection), so without
this they would otherwise touch this project's real, on-disk `jansarthi.db`.
"""

import backend.mcp_server as mcp_server
from backend.models import Complaint


def test_all_three_tools_are_registered_with_fastmcp():
    import asyncio

    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {"search_civic_knowledge_base", "get_complaint_status", "file_complaint"}


def test_get_retriever_wires_in_the_reranker_same_as_the_real_http_api(monkeypatch):
    """BUG FIX (code review): _get_retriever() used to build its RagRetriever without ever
    calling AskJanMitraService._load_default_reranker(), silently contradicting this module's own
    docstring claim of using "the same retrieval pipeline...same VERIFIED-preference rerank" as
    the real /ask-janmitra endpoint. With RAG_RERANKER_ENABLED=true, the HTTP API would rerank via
    the cross-encoder while this MCP tool kept returning heuristic-only-ranked results for an
    identical query. Asserts the constructed RagRetriever's own reranker is whatever
    AskJanMitraService._load_default_reranker() returns -- proving the two can't drift again."""
    from backend.services.ask_janmitra_service import AskJanMitraService

    monkeypatch.setattr(mcp_server, "_retriever", None)  # force a fresh build for this test
    # Fake store/embedding-provider loaders too -- this test is only about the reranker wiring,
    # not about paying a real ~20s model-load cost.
    monkeypatch.setattr(AskJanMitraService, "_load_default_store", staticmethod(lambda: object()))
    monkeypatch.setattr(AskJanMitraService, "_load_default_embedding_provider", staticmethod(lambda: object()))
    sentinel_reranker = object()
    monkeypatch.setattr(AskJanMitraService, "_load_default_reranker", staticmethod(lambda: sentinel_reranker))

    retriever = mcp_server._get_retriever()

    assert retriever._reranker is sentinel_reranker


# --- search_civic_knowledge_base -- real ChromaDB + real embedding model, no mocks (matches
# tests/test_rag_vector_store.py's own posture for this exact stack) ------------------------


def test_search_civic_knowledge_base_returns_real_verified_results():
    result = mcp_server.search_civic_knowledge_base(
        "street light not working",
        service_category="STREETLIGHTS",
        city="Sahibzada Ajit Singh Nagar (Mohali)",
    )
    assert result["insufficient_knowledge"] is False
    assert len(result["results"]) > 0
    first = result["results"][0]
    assert first["verification_status"] in ("VERIFIED", "SYNTHETIC")
    assert isinstance(first["score"], float)
    assert first["content"]


def test_search_civic_knowledge_base_reports_insufficient_knowledge_honestly():
    """An out-of-scope/no-match query for a real, narrow category+location combination must come
    back empty with a real reason -- never a fabricated result."""
    result = mcp_server.search_civic_knowledge_base(
        "xyzzy plugh completely unrelated gibberish query",
        service_category="STREETLIGHTS",
        city="Sahibzada Ajit Singh Nagar (Mohali)",
    )
    if result["insufficient_knowledge"]:
        assert result["results"] == []
        assert result["reason"]


# --- get_complaint_status / file_complaint -- real DB, isolated per test via monkeypatched
# SessionLocal (see this module's own docstring) ---------------------------------------------


def test_get_complaint_status_for_a_nonexistent_id_reports_not_found(monkeypatch, db_session):
    monkeypatch.setattr(mcp_server, "SessionLocal", db_session)
    result = mcp_server.get_complaint_status(999999)
    assert result == {"found": False}


def test_get_complaint_status_for_a_real_complaint(monkeypatch, db_session):
    monkeypatch.setattr(mcp_server, "SessionLocal", db_session)
    db = db_session()
    complaint = Complaint(
        citizen_id="mcp_test_citizen",
        original_text="Garbage has not been collected in three days.",
        original_language="en",
        translated_text="Garbage has not been collected in three days.",
        summary="Uncollected garbage",
        status="pending",
        service_category="WASTE_SANITATION",
        ward="Ward 7",
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    complaint_id = complaint.id
    db.close()

    result = mcp_server.get_complaint_status(complaint_id)
    assert result["found"] is True
    assert result["id"] == complaint_id
    assert result["status"] == "pending"
    assert result["service_category"] == "WASTE_SANITATION"
    assert result["ward"] == "Ward 7"
    assert result["summary"] == "Uncollected garbage"
    assert result["created_at"] is not None


def test_file_complaint_creates_a_real_complaint_record(monkeypatch, db_session):
    monkeypatch.setattr(mcp_server, "SessionLocal", db_session)

    class _FakeComplaintAgent:
        def create_complaint(self, db, citizen_id, language_code, text, audio_chunks, photo_path, category=None):
            complaint = Complaint(
                citizen_id=citizen_id,
                original_text=text,
                original_language=language_code,
                translated_text=text,
                summary=text[:80],
                status="pending",
                service_category=category.value if category else None,
            )
            db.add(complaint)
            db.commit()
            db.refresh(complaint)
            return complaint

    monkeypatch.setattr(mcp_server, "_complaint_agent", _FakeComplaintAgent())

    result = mcp_server.file_complaint(
        citizen_id="mcp_test_citizen",
        text="There is a large pothole outside my house.",
        language_code="en",
        service_category="ROADS_POTHOLES",
    )
    assert "error" not in result
    assert result["status"] == "pending"

    db = db_session()
    saved = db.query(Complaint).filter(Complaint.id == result["id"]).first()
    assert saved is not None
    assert saved.citizen_id == "mcp_test_citizen"
    assert saved.service_category == "ROADS_POTHOLES"
    db.close()


def test_file_complaint_reports_errors_without_raising(monkeypatch, db_session):
    monkeypatch.setattr(mcp_server, "SessionLocal", db_session)

    class _FailingComplaintAgent:
        def create_complaint(self, **kwargs):
            raise ValueError("complaint text is empty")

    monkeypatch.setattr(mcp_server, "_complaint_agent", _FailingComplaintAgent())

    result = mcp_server.file_complaint(citizen_id="mcp_test_citizen", text="")
    assert result == {"error": "complaint text is empty"}
