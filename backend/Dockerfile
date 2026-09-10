# Build context: repo root (docker build -f backend/Dockerfile .)
FROM python:3.12-slim AS base

WORKDIR /app

# build-essential: some chromadb deps (hnswlib) build a C++ extension if no matching wheel
# exists for the target platform. curl: used by the container HEALTHCHECK below.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# torch's CPU-only wheel isn't on the default PyPI index -- installing it first from the CPU
# wheel index avoids pip pulling the much larger CUDA build (see requirements.txt's comment
# above the torch line for why this matters).
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.13.0 \
    && pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY prompts/ prompts/
COPY scripts/ scripts/
COPY data/ data/

# Build the RAG retrieval index (documents/chunks/embeddings -> ChromaDB) from the committed,
# hand-authored data/rag_knowledge_base/knowledge_records/ source at IMAGE BUILD time. This is
# a pure function of that source data (see build_rag_knowledge_base.py's docstring), so baking
# it into the image keeps every deployed container's index reproducible and avoids shipping a
# binary ChromaDB blob in git or rebuilding it at container startup.
RUN python scripts/build_rag_knowledge_base.py \
    && python scripts/build_rag_embeddings.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# --ws-ping-timeout raised well past uvicorn's 20s default: LIVE-REPORTED BUG, confirmed via
# production traceback (websockets.exceptions.ConnectionClosedError: "sent 1011 (internal error)
# keepalive ping timeout; no close frame received") -- Ask Sarthi's own real RAG/reasoning-model
# turnaround is documented at ~20-25s, so uvicorn's default keepalive was killing the
# `/ask-sarthi/voice/live` WebSocket mid-turn on any request at or past that default, well before
# the slower end of its own normal latency range. Only this route holds a connection open across
# a turn that slow; every other WS use in this app is unaffected by raising it.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--ws-ping-interval", "20", "--ws-ping-timeout", "90"]
