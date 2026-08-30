"""FastAPI application entry point for JanSarthi AI."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.database import SessionLocal, init_db
from backend.middleware import CSRFMiddleware, GeneralRateLimitMiddleware, SecurityHeadersMiddleware
from backend.routes import admin, ask_sarthi, auth, complaints, locations, notifications
from backend.services.upload_cleanup_service import cleanup_orphaned_uploads

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _check_production_secrets() -> None:
    """Fails startup loudly instead of the previous silent behavior: without this check,
    auth_service.py falls back to a random per-process JWT_SECRET_KEY if none is configured
    (fine for local dev -- sessions just don't survive a restart -- but a real security gap in
    a real deployment, since every session becomes invalid on every restart and, if two
    processes ever ran the fallback independently, each would sign with a different secret).
    Only enforced when ENVIRONMENT=production (see config.py's own docstring on that setting) --
    local dev and CI, which never set it, are unaffected. Extracted as its own function
    (rather than inlined in lifespan() below) specifically so it can be unit-tested directly
    without booting the full app.
    """
    if settings.ENVIRONMENT == "production" and not settings.JWT_SECRET_KEY:
        raise RuntimeError(
            "JWT_SECRET_KEY must be set when ENVIRONMENT=production -- refusing to start with a "
            "randomly-generated, per-process secret in a real deployment. Set JWT_SECRET_KEY in "
            "the production .env (see docs/DEPLOYMENT_GCP.md)."
        )


def init_error_monitoring() -> None:
    """Wire up Sentry error alerting, if configured.

    A no-op when SENTRY_DSN is unset (the default) -- same "off unless explicitly configured"
    rule LangSmith tracing follows (see backend/services/observability/tracing.py): the app must
    behave identically whether or not this is set up. Wrapped in try/except so a bad DSN or a
    transient network issue during init can never block the app from starting -- the monitoring
    tool itself must not become a new way for the app to go down.
    """
    if not settings.SENTRY_DSN:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        integrations = [StarletteIntegration(), FastApiIntegration()]
        # NOT the top-level `enable_logs=` init() kwarg -- confirmed directly against the
        # installed SDK (sentry_sdk/client.py) that it's a deprecated no-op: the log pipeline is
        # created unconditionally regardless of that flag's value, so it can neither turn logs on
        # nor off. The real, current gate is this integration's own `capture_sentry_logs` --
        # explicitly constructed and added ONLY when the setting is on, since the SDK also
        # auto-enables a default LoggingIntegration(capture_sentry_logs=False) otherwise, and
        # always adding one with capture_sentry_logs=True would make this setting do nothing too.
        if settings.SENTRY_ENABLE_LOGS:
            integrations.append(LoggingIntegration(capture_sentry_logs=True))

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.SENTRY_ENVIRONMENT,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            integrations=integrations,
            # Request/user data can contain citizen PII (phone numbers, complaint text). False is
            # already the SDK's own default -- set explicitly so a future sentry-sdk version
            # silently changing its own default can't quietly start sending PII unnoticed.
            send_default_pii=False,
            # "trace": only ever profiles while a trace is active, so this is inert unless
            # SENTRY_TRACES_SAMPLE_RATE > 0 too -- see SENTRY_PROFILE_SESSION_SAMPLE_RATE's own
            # docstring in config.py. Fixed, not a separate setting -- Sentry's own recommended
            # mode, nothing project-specific to configure about it.
            profile_lifecycle="trace",
            profile_session_sample_rate=settings.SENTRY_PROFILE_SESSION_SAMPLE_RATE,
            # NOTE: no enable_metrics= here either, for the same reason as enable_logs above --
            # confirmed deprecated no-op. SENTRY_ENABLE_METRICS is instead enforced in
            # backend/services/metrics.py, the one place every metrics call site in this codebase
            # goes through -- see that module's own docstring.
        )
        logger.info(
            "Sentry error monitoring initialized (environment=%s, logs=%s, metrics=%s, "
            "traces_sample_rate=%s, profile_session_sample_rate=%s)",
            settings.SENTRY_ENVIRONMENT,
            settings.SENTRY_ENABLE_LOGS,
            settings.SENTRY_ENABLE_METRICS,
            settings.SENTRY_TRACES_SAMPLE_RATE,
            settings.SENTRY_PROFILE_SESSION_SAMPLE_RATE,
        )
    except Exception:
        logger.exception("Failed to initialize Sentry -- continuing without error monitoring")


_UPLOAD_CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60  # every 6 hours


async def _upload_cleanup_loop() -> None:
    """Background loop: periodically removes uploaded chat photos that never became part of a
    real complaint (see upload_cleanup_service.cleanup_orphaned_uploads's own docstring for why
    this exists at all). Runs once immediately (catches anything already stale from before this
    process started), then every `_UPLOAD_CLEANUP_INTERVAL_SECONDS`. Own DB session per sweep, not
    request-scoped -- this has nothing to do with any one request. Best-effort: a failed sweep is
    logged, never crashes the app or stops future sweeps.
    """
    while True:
        db = SessionLocal()
        try:
            cleanup_orphaned_uploads(db)
        except Exception:
            logger.exception("Orphaned-upload cleanup sweep failed -- will retry next interval")
        finally:
            db.close()
        await asyncio.sleep(_UPLOAD_CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Initialize the database, wire up error monitoring, then warm the RAG embedding model, on
    application startup.

    init_error_monitoring() deliberately lives HERE, not called unconditionally at module level
    -- this module gets imported by every test file too (via `from backend.main import app`),
    and this project's test suite's own TestClient (see tests/conftest.py) is used WITHOUT the
    `with TestClient(app) as client:` context-manager form specifically so lifespan never runs
    for a plain test request (confirmed directly: init_db() below has always relied on exactly
    this to keep tests on their own isolated in-memory database, never production's real one).
    A module-level call would have broken that isolation the other way for Sentry specifically --
    every test run would have made a REAL call to sentry_sdk.init() with a real DSN the moment
    ANY test file imports this module, and any test that exercises a deliberate error path (this
    suite has many) would then report as a real, misleading error in the actual Sentry project.
    Living inside lifespan means it only actually runs when the app is genuinely served (uvicorn,
    `docker compose up`), exactly matching init_db()'s own real-vs-test split below.

    Warming the embedding model here (rather than leaving it fully lazy) matters in practice --
    measured directly while validating this: the FIRST live Ask Sarthi request after a fresh
    backend start paid the model's ~20-25s load cost inline, on top of normal retrieval+LLM
    latency, and exceeded a 30s client timeout in one such run. Warming it once at startup (a
    few extra seconds before the app reports ready, paid once, off the request path) avoids
    that. Best-effort: if this fails (e.g. no network on first run before the model is cached
    locally), the app still starts -- ask_sarthi_service.py's embedding provider is lazy by
    design and will retry the load on the first real request, matching the pre-warm-up behavior.
    """
    _check_production_secrets()
    init_error_monitoring()
    init_db()
    try:
        from backend.routes.ask_sarthi import _service

        _service._embedding_provider.load()
        logger.info("RAG embedding model warmed up at startup")
    except Exception as exc:
        logger.warning("Could not warm up the RAG embedding model at startup (will lazy-load on first request): %s", exc)
    cleanup_task = asyncio.create_task(_upload_cleanup_loop())
    logger.info("JanSarthi AI backend started")
    yield
    cleanup_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await cleanup_task


app = FastAPI(title="JanSarthi AI", version="0.1.0", lifespan=lifespan)
# Registered BEFORE CORSMiddleware below so CORS ends up OUTERMOST (Starlette wraps middleware in
# reverse registration order -- the last one added runs first on the way in) -- see
# GeneralRateLimitMiddleware's own docstring for why that order matters (preflight handling, and
# CORS headers on this middleware's own 429 responses).
app.add_middleware(GeneralRateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CSRFMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Content-Disposition isn't in the browser's CORS-safelisted response headers, so without
    # explicitly exposing it, fetch()'s response.headers.get("Content-Disposition") silently
    # returns null on a cross-origin request (frontend :5173 -> backend :8000 in dev) even though
    # the header IS actually present -- curl/Postman never show this bug since CORS is a
    # browser-only restriction. That's what made the report download filename fall back to the
    # generic "report.pdf" (see frontend-react/src/lib/api.ts's requestBlob()) instead of the
    # real "JanSarthi_Complaint_JM-00042_Report.pdf" the backend was already sending correctly.
    #
    # X-Total-Count has the exact same problem, for the exact same CORS reason -- every
    # requestPaginated() caller (My Complaints', Home's, Admin's, and Worker's own stat cards,
    # among others) reads response.headers.get("X-Total-Count") and silently falls back to
    # items.length when it's null, capping every one of those numbers at whatever page_size was
    # requested (usually 1, for a cheap count-only call) instead of the real total -- invisible in
    # every demo account so far only because no citizen had more than one complaint until now.
    expose_headers=["Content-Disposition", "X-Total-Count"],
)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(complaints.router)
app.include_router(locations.router)
app.include_router(notifications.router)
app.include_router(ask_sarthi.router)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_FOLDER), name="uploads")


@app.get("/health")
def health() -> dict[str, str]:
    """Simple health check endpoint."""
    return {"status": "ok"}
