"""Application configuration loaded from environment variables.

All secrets, API keys, model names, and file-handling limits live here so
the rest of the codebase never hardcodes them or reads os.environ directly.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings:
    """Central place for all configuration values used across the app."""

    # Sarvam AI (speech-to-text + translation + text-to-speech)
    SARVAM_API_KEY: str = os.getenv("SARVAM_API_KEY", "")
    SARVAM_BASE_URL: str = os.getenv("SARVAM_BASE_URL", "https://api.sarvam.ai")
    # The `sarvamai` SDK defaults to a 60s httpx timeout when none is passed (see SarvamAI.__init__).
    # A real network hiccup during live testing surfaced as a citizen-facing complaint-creation
    # failure that took ~30s to honestly report itself (a lower-level OS connection-attempt
    # timeout firing before httpx's own 60s ceiling would have) -- every caller of this API
    # (sarvam_client.py, summary_service.py, answer_generation_service.py, normalization_service.py)
    # already falls back gracefully on ANY failure (see each module's own docstring), so shortening
    # this only changes how long a citizen waits before that honest fallback fires, not what
    # happens when it does.
    #
    # Two separate knobs, not one, because the failure observed was specifically a CONNECT timeout
    # (the TCP handshake itself never completed -- genuine network unreachability) -- a completely
    # different phase from "the model is still generating a slow-but-real answer" (a READ timeout,
    # which only starts once a connection already succeeded). sarvam-105b is a reasoning model
    # (see summary_service.py/answer_generation_service.py's own comments on reasoning_effort) that
    # legitimately needs real headroom to finish thinking -- capping THAT at the same short value
    # used for fast STT/translation/TTS calls would turn legitimate slow-but-working answers into
    # unnecessary fallback-to-raw-excerpt failures, trading one problem for a worse one. Verified
    # directly that the `sarvamai` SDK accepts an httpx.Timeout with separate connect/read here
    # (its own type hint only advertises a bare float, but the value is passed straight through to
    # httpx.Client(timeout=...), which httpx itself documents as accepting either).
    SARVAM_CONNECT_TIMEOUT_SECONDS: float = float(os.getenv("SARVAM_CONNECT_TIMEOUT_SECONDS", "10"))
    SARVAM_REQUEST_TIMEOUT_SECONDS: float = float(os.getenv("SARVAM_REQUEST_TIMEOUT_SECONDS", "15"))
    SARVAM_REASONING_READ_TIMEOUT_SECONDS: float = float(os.getenv("SARVAM_REASONING_READ_TIMEOUT_SECONDS", "45"))

    # Ask Sarthi voice assistant TTS (see backend/services/sarvam_client.py's
    # synthesize_speech()) -- one fixed default voice/model for v1, not user-configurable (a
    # cosmetic product decision, not an architectural one -- see the implementation plan).
    TTS_SPEAKER: str = os.getenv("TTS_SPEAKER", "anushka")
    TTS_MODEL: str = os.getenv("TTS_MODEL", "bulbul:v2")

    # LLM used for complaint summary generation (Sarvam's chat completion API by default,
    # so this can reuse SARVAM_API_KEY if LLM_API_KEY is left unset)
    LLM_API_KEY: str = os.getenv("LLM_API_KEY") or os.getenv("SARVAM_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "sarvam-105b")
    # sarvam-105b is a reasoning model: it spends tokens on internal reasoning_content
    # before emitting the final answer, so this needs much more headroom than a plain
    # 1-2 sentence summary would suggest, or the response gets cut off with empty content.
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "1024"))

    # File storage
    UPLOAD_FOLDER: str = os.getenv("UPLOAD_FOLDER", str(BASE_DIR / "uploads"))
    MAX_PHOTO_SIZE_BYTES: int = 5 * 1024 * 1024  # 5 MB
    ALLOWED_PHOTO_CONTENT_TYPES: tuple[str, ...] = ("image/jpeg", "image/png", "image/jpg")
    # How long an Ask Sarthi chat photo is kept on disk before being cleaned up if it never ended
    # up attached to a real complaint (see services/upload_cleanup_service.py's own docstring for
    # why this can't just be zero -- a citizen mid-conversation still needs their just-attached
    # photo to survive until they decide whether to file).
    ORPHANED_UPLOAD_RETENTION_HOURS: int = int(os.getenv("ORPHANED_UPLOAD_RETENTION_HOURS", "48"))

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'jansarthi.db'}")

    # Base URL the Streamlit frontends use to reach the FastAPI backend
    BACKEND_URL: str = os.getenv("BACKEND_URL", "http://localhost:8000")

    # Origins allowed to call the API from a browser (the React dev server, etc.)
    CORS_ORIGINS: list[str] = [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
        if origin.strip()
    ]

    # Prompts directory
    PROMPTS_DIR: Path = BASE_DIR / "prompts"

    # RAG civic-knowledge-base data (see backend/schemas/rag_knowledge.py) — reference content,
    # not app state, so it lives in flat files here rather than the SQLite DB (see that module's
    # docstring for why).
    RAG_DATA_DIR: Path = BASE_DIR / "data" / "rag_knowledge_base"

    # RAG retrieval config (see backend/services/rag_retriever.py, vector_store.py,
    # embedding_provider.py). RAG_TOP_K/RAG_RELEVANCE_THRESHOLD are tunable via env var without
    # a code change — see docs/ask_janmitra_rag_architecture.md for how these were chosen.
    RAG_EMBEDDINGS_INDEX_PATH: Path = BASE_DIR / "data" / "rag_knowledge_base" / "embeddings" / "index.json"
    RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "5"))
    # NOTE: this default applies to the LEGACY TF-IDF path only (scores 0.0-~0.4 typically).
    # The active default path (real embeddings) uses RAG_EMBEDDING_RELEVANCE_THRESHOLD below --
    # cosine similarity from a neural sentence embedding model has a very different, much
    # higher-and-narrower score distribution (empirically ~0.7-0.9 for both related and
    # unrelated text in this corpus -- see docs/ask_janmitra_rag_architecture.md's threshold
    # evaluation section for the actual measurements behind this default).
    RAG_RELEVANCE_THRESHOLD: float = float(os.getenv("RAG_RELEVANCE_THRESHOLD", "0.08"))

    # Real-embeddings + ChromaDB retrieval config (see backend/services/embedding_provider.py's
    # SentenceTransformerEmbeddingProvider and vector_store.py's ChromaVectorStore). This is the
    # active default retrieval path as of the RAG embeddings/ChromaDB migration -- see
    # docs/ask_janmitra_rag_architecture.md.
    EMBEDDING_MODEL_NAME: str = os.getenv("EMBEDDING_MODEL_NAME", "intfloat/multilingual-e5-small")

    # Ask Sarthi image understanding (see backend/services/vision_service.py). A small local
    # vision-language model, not a hosted vendor -- Sarvam has no vision capability, and the user
    # chose to avoid adding a new AI vendor/API key for this feature.
    VISION_MODEL_NAME: str = os.getenv("VISION_MODEL_NAME", "vikhyatk/moondream2")
    CHROMA_PERSIST_DIR: Path = Path(os.getenv("CHROMA_PERSIST_DIR", str(BASE_DIR / "data" / "rag_knowledge_base" / "chroma")))
    CHROMA_COLLECTION_NAME: str = os.getenv("CHROMA_COLLECTION_NAME", "janmitra_knowledge")
    # Chosen from two rounds of actual measurement, not a round-number guess -- see
    # docs/ask_janmitra_rag_architecture.md's threshold-calibration section for the full data.
    #
    # Round 1 (raw/unfiltered search, no category or location restriction): this model's cosine
    # similarity scores for this corpus cluster narrowly (~0.72-0.85) regardless of true
    # relevance -- "new electricity connection" (must be REJECTED) scored 0.82-0.83, HIGHER than
    # genuinely unrelated gibberish (~0.80). A flat threshold cannot separate relevant from
    # irrelevant text in this regime; a global cutoff alone is not a safe hallucination guard.
    #
    # Round 2 (realistic: category + location filter applied first, exactly as RagRetriever.
    # retrieve() always does in production), across TWO categories to avoid tuning on one:
    #   STREETLIGHTS+Mohali: genuine paraphrases ("The light outside my house has stopped
    #     working" / "There is no functioning street light near me" / "My road lamp is broken")
    #     scored >=0.803; off-topic/gibberish probes run through the SAME filter ("...chocolate
    #     cake" 0.746-0.750, "...capital of France?" 0.723-0.741, "xyzzy plugh..." 0.766-0.780)
    #     stayed <=0.780.
    #   ROADS_POTHOLES+Mohali: genuine paraphrases ("road has a large pothole" top=0.820,
    #     "there is a deep hole in the road" top=0.794) vs. the same three off-topic/gibberish
    #     probes (cake <=0.755, capital of France <=0.739, gibberish <=0.782).
    # An initial round-2 pass tried 0.80 -- it cleanly separated STREETLIGHTS, but rejected the
    # required ROADS_POTHOLES paraphrase "there is a deep hole in the road" (0.794 < 0.80), a
    # real measured false negative on one of the spec's mandatory paraphrase examples. 0.79 is
    # the threshold actually used: it still rejects every off-topic/gibberish probe measured in
    # both categories (max 0.782) while keeping every genuine paraphrase tested in both (min
    # 0.794) -- a clean, if narrow (0.782 vs 0.794), separation band.
    #
    # Conclusion, stated honestly: this threshold is a real, measured, effective filter WHEN
    # metadata filtering (category + location) has already restricted the candidate pool -- which
    # is the only way it is ever used in production. It is NOT, by itself, a reliable filter over
    # an unrestricted corpus (round 1). The primary hallucination-prevention mechanisms remain (1)
    # the intent classifier's explicit out-of-scope detection (electricity, new-connection, etc.,
    # which never reach the retriever at all) and (2) category+location metadata filtering applied
    # before similarity ranking -- this threshold is a secondary, but now-verified-effective,
    # safeguard on top of those, not a replacement for them. The separation margin (~0.01-0.03) is
    # narrow enough that a genuinely borderline real question could go either way -- documented as
    # a known limitation, not hidden (see docs/ask_janmitra_rag_architecture.md).
    RAG_EMBEDDING_RELEVANCE_THRESHOLD: float = float(os.getenv("RAG_EMBEDDING_RELEVANCE_THRESHOLD", "0.79"))

    # A separate, lower floor for VERIFIED chunks specifically (see rag_retriever.py's own
    # "cross-lingual verified rescue" comment for the full mechanism) -- live-reproduced gap: the
    # exact same question about Bengaluru's water-supply procedure, asked in English, scored the
    # real BWSSB record at 0.87+ (comfortably above RAG_EMBEDDING_RELEVANCE_THRESHOLD); asked in
    # Marathi script, the SAME real BWSSB chunks scored only 0.75-0.79 -- just under the main
    # threshold -- while topically-generic SYNTHETIC placeholder chunks for the same city+category
    # scored 0.80-0.84 and passed easily. Not a metadata-filtering gap (category+location had
    # already narrowed the candidate pool correctly, same safety precondition
    # RAG_EMBEDDING_RELEVANCE_THRESHOLD's own docstring already relies on) -- a real, measured
    # cross-lingual embedding-similarity gap specifically for non-English scripts against this
    # knowledge base's English-authored content. Deliberately only ever widens the bar for content
    # that's already VERIFIED (never fabricated, from a real government source) and already passed
    # the same city+category filter every other chunk did -- never a blanket relaxation of the main
    # threshold, which stays exactly as measured/justified above for everything else.
    RAG_VERIFIED_RELEVANCE_THRESHOLD: float = float(os.getenv("RAG_VERIFIED_RELEVANCE_THRESHOLD", "0.74"))

    # Hardcoded identities (kept only for the legacy Streamlit frontends, which
    # predate real auth and are superseded by the React app + JWT login below)
    HARDCODED_CITIZEN_ID: str = "citizen_001"
    HARDCODED_WORKER_ID: str = "worker_001"

    # General-purpose deployment flag ("development" | "production") -- distinct from
    # SENTRY_ENVIRONMENT below, which only labels Sentry events and shouldn't be repurposed as a
    # real app-behavior switch. Currently gates exactly one thing: main.py's startup refusal to
    # run with a blank JWT_SECRET_KEY (see below) -- local dev stays convenience-first, a real
    # deployment gets a hard failure instead of a silent security gap.
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

    # Auth (JWT, HS256, implemented with the stdlib only — see services/auth_service.py). Access
    # tokens are short-lived by design now that a refresh token exists to silently renew them
    # (see REFRESH_TOKEN_EXPIRE_DAYS below) -- 24h was only ever that long because it used to be
    # the ENTIRE session lifetime with nothing to renew it.
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "")
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "30"))
    # Refresh tokens are opaque random strings stored (hashed) in the refresh_tokens table, not
    # JWTs -- see models.RefreshToken's own docstring for why. Long-lived by design (a citizen
    # shouldn't have to re-login every 30 minutes); rotated on every use and revoked as a whole
    # family on detected reuse, which is what makes a month-long lifetime an acceptable tradeoff.
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))

    # Email (plain SMTP via Python's stdlib smtplib -- see services/email_service.py) for OTP-based
    # email verification and forgot-password. No third-party email-provider account needed --
    # point this at any existing mailbox (Gmail, Outlook, a custom domain). Same "off unless
    # configured" posture as SARVAM_API_KEY/LANGSMITH_API_KEY above: blank SMTP credentials make
    # the email-touching routes return a clear 503, never a silent no-op or a fabricated "sent"
    # response (see email_service.py).
    SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    # For Gmail: the full Gmail address. SMTP_PASSWORD must be a 16-character Google "App
    # Password" (Google Account -> Security -> 2-Step Verification -> App passwords) -- Gmail
    # rejects plain-password SMTP login by default, so the normal account password won't work.
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    # Defaults to SMTP_USERNAME (the common case: sending FROM the same mailbox being logged
    # into) but kept as its own setting since some providers allow sending as a different verified
    # alias than the login username.
    EMAIL_FROM_ADDRESS: str = os.getenv("EMAIL_FROM_ADDRESS") or os.getenv("SMTP_USERNAME", "")
    EMAIL_FROM_NAME: str = os.getenv("EMAIL_FROM_NAME", "JanSarthi AI")
    # LIVE-REPORTED GAP: every real OTP send goes through the one Gmail account configured above,
    # which has repeatedly hit Gmail's own daily sending-limit quota during heavy local/E2E
    # testing (confirmed live twice, 3 days apart -- see PLAYWRIGHT_TEST_REPORT.md) -- blocking
    # every signup-dependent test, not a code bug. When true, the three OTP-sending routes
    # (routes/auth.py) skip the real SMTP send entirely and only cache the code via the existing
    # `_dev_cache_otp`/`GET /auth/_dev/otp-code` mechanism (already dev-only, already how
    # Playwright specs read a code back) -- so local/E2E runs never depend on Gmail's quota at
    # all. Every call site ALSO requires `ENVIRONMENT != "production"` before honoring this flag
    # (same belt-and-suspenders pattern _dev_cache_otp itself already uses just below in
    # routes/auth.py) -- a stray true value in a production environment can never bypass a real
    # send on its own.
    EMAIL_DEV_MODE: bool = os.getenv("EMAIL_DEV_MODE", "false").strip().lower() == "true"
    # A 6-digit OTP is short-lived by design, unlike the month-long refresh token above.
    OTP_EXPIRE_MINUTES: int = int(os.getenv("OTP_EXPIRE_MINUTES", "10"))
    # A 6-digit code has only 1,000,000 possibilities -- meaningfully brute-forceable without a
    # cap, unlike the 256-bit refresh token. See models.EmailOtp.attempts.
    OTP_MAX_ATTEMPTS: int = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))
    # Its own dedicated limiter (not just GENERAL_RATE_LIMIT below), keyed per client IP, on both
    # OTP-sending routes -- stops someone spamming a victim's inbox or burning the 300/day Brevo
    # free quota. Generous enough for a real user resending once after not receiving the first
    # code; tight enough to block a spam script.
    OTP_RATE_LIMIT: int = int(os.getenv("OTP_RATE_LIMIT", "3"))
    OTP_RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("OTP_RATE_LIMIT_WINDOW_SECONDS", "600"))
    # How long a confirmed email-verification proof (see models.SignupEmailVerification) stays
    # redeemable at POST /auth/signup -- long enough for a citizen to click "Verify" on the email
    # field, then take their time filling in the rest of the signup form (name, phone, password,
    # ward) before submitting, short enough that a stale, unused verification can't be resurrected
    # much later by someone else who happens to learn the token.
    EMAIL_VERIFICATION_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("EMAIL_VERIFICATION_TOKEN_EXPIRE_MINUTES", "30"))
    # The deployed frontend's own origin (e.g. "https://jansarthi.example.com"), used only to
    # build a "View complaint" link in complaint-lifecycle emails (see email_service.py's
    # send_complaint_status_email). Optional and blank by default -- same "off unless configured,
    # degrade gracefully" posture as everything else on this page: an email still sends fine
    # without this set, it just omits the button rather than linking to a blank/wrong host.
    FRONTEND_BASE_URL: str = os.getenv("FRONTEND_BASE_URL", "")

    # Rate limiting (see backend/services/rate_limiter.py, backend/deps.py's
    # require_login_rate_limit/require_ai_rate_limit) -- a small, in-process, stdlib-only sliding
    # window, matching this codebase's existing preference for hand-rolled-over-new-dependency
    # (see auth_service.py's own docstring on why JWT is hand-rolled here). Protects POST
    # /auth/login (brute-force) and the three POST /ask-janmitra* endpoints (expensive Sarvam/LLM/
    # vision calls) -- see docs/RATE_LIMITING.md for the full design and its single-process
    # limitation.
    #
    # LOGIN: keyed per client IP, generous enough that no real login flow ever trips it (one
    # attempt, or a couple of role-switching demo logins in the same minute) while still capping a
    # brute-force script at a small, fixed number of guesses per minute.
    LOGIN_RATE_LIMIT: int = int(os.getenv("LOGIN_RATE_LIMIT", "5"))
    LOGIN_RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "60"))
    # SIGNUP: keyed per client IP, its own dedicated limiter rather than relying on the loose
    # GENERAL_RATE_LIMIT baseline below -- account creation is rarer and more consequential to
    # abuse (mass fake-account creation) than a single request, so it gets a stricter PER-HOUR
    # window rather than login's per-minute one. 50, not something as tight as login's 5 -- this
    # needs to stay well clear of legitimate shared-IP bursts (a school/office network, or a NAT'd
    # mobile carrier -- both common for this app's actual India-wide audience) while still capping
    # a true bulk-fake-account script. Measured directly against this project's own e2e suite,
    # which performs ~17 real signups from one IP in a single full run (before any Playwright
    # retry) -- confirms 5 was genuinely too tight, not just theoretically conservative.
    SIGNUP_RATE_LIMIT: int = int(os.getenv("SIGNUP_RATE_LIMIT", "50"))
    SIGNUP_RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("SIGNUP_RATE_LIMIT_WINDOW_SECONDS", "3600"))
    # AI: keyed per authenticated user id, sized against the real measured shape of a normal Ask
    # Sarthi turn (a location clarification round-trip alone is 2 calls; a complaint-confirmation
    # round-trip is another 2) -- 10/min gives a normal demo conversation (several exchanges) 3-4x
    # headroom while still catching a rapid-fire abuse script hammering the paid Sarvam API.
    AI_RATE_LIMIT: int = int(os.getenv("AI_RATE_LIMIT", "10"))
    AI_RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("AI_RATE_LIMIT_WINDOW_SECONDS", "60"))
    # General baseline, applied to every route except /health by backend/middleware.py's
    # GeneralRateLimitMiddleware -- a safety net against scripted abuse/scraping across the whole
    # API, on top of (not instead of) LOGIN_RATE_LIMIT/AI_RATE_LIMIT's own stricter limits.
    # Generous: a real user clicking through the app (loading a dashboard, filing a complaint,
    # paging through workers) never comes close, verified live -- see docs/RATE_LIMITING.md.
    GENERAL_RATE_LIMIT: int = int(os.getenv("GENERAL_RATE_LIMIT", "60"))
    GENERAL_RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("GENERAL_RATE_LIMIT_WINDOW_SECONDS", "60"))
    # Whether to trust the reverse proxy's X-Forwarded-For for the login rate limiter's per-IP
    # key, instead of the raw TCP peer address. Safe to enable ONLY when every request is
    # guaranteed to have passed through a reverse proxy that sets this header itself (this
    # project's production Caddy does, and the backend container publishes no port of its own --
    # see docker-compose.prod.yml) -- an attacker who can reach the backend directly could
    # otherwise spoof this header to bypass or misattribute the limit. Off by default (local dev
    # has no reverse proxy in front, so the raw TCP peer IS the real client); set true only in the
    # production environment that actually has Caddy in front (see docker-compose.prod.yml, which
    # sets this).
    TRUST_PROXY_HEADERS: bool = os.getenv("TRUST_PROXY_HEADERS", "false").strip().lower() == "true"

    # LangSmith observability (see backend/services/observability/tracing.py and
    # docs/ask_janmitra_langsmith_observability.md) -- a pure observability layer around the
    # existing LangGraph/RAG pipeline; OFF by default, and the app must behave identically
    # whether or not it's configured (see that doc's "failure behavior" section). Tracing is
    # only actually attempted when BOTH LANGSMITH_TRACING is true AND an API key is set --
    # matches SarvamClient's own "warn, don't require" pattern for optional external services.
    LANGSMITH_TRACING: bool = os.getenv("LANGSMITH_TRACING", "false").strip().lower() == "true"
    LANGSMITH_API_KEY: str = os.getenv("LANGSMITH_API_KEY", "")
    LANGSMITH_ENDPOINT: str = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    LANGSMITH_PROJECT: str = os.getenv("LANGSMITH_PROJECT", "jansarthi-ai")
    # Optional deep-link template for the Admin dashboard's "View Trace" button, with a
    # "{trace_id}" placeholder, e.g.
    # "https://smith.langchain.com/o/<your-org-id>/projects/p/<project>/r/{trace_id}" -- copy the
    # prefix from your own LangSmith project's URL in the LangSmith UI. Left blank, trace IDs are
    # still stored and shown but without a clickable link (deliberately not auto-resolved via the
    # LangSmith API -- that would be a network call on the admin dashboard's read path).
    LANGSMITH_TRACE_URL_TEMPLATE: str = os.getenv("LANGSMITH_TRACE_URL_TEMPLATE", "")
    # LangSmith Annotation Queue every "insufficient_knowledge"/out-of-scope Ask Sarthi trace is
    # routed into for human review (see tracing.py's enqueue_for_review()) -- a knowledge-base-gap
    # backlog, not a moderation queue. Created automatically on first use if it doesn't exist yet.
    LANGSMITH_REVIEW_QUEUE_NAME: str = os.getenv("LANGSMITH_REVIEW_QUEUE_NAME", "jansarthi-ai-knowledge-gaps")

    # Error monitoring (Sentry -- see backend/main.py's init_error_monitoring()). OFF by default,
    # same "off unless explicitly configured" pattern as LANGSMITH_TRACING above: only actually
    # initializes when SENTRY_DSN is set, and the app must behave identically whether or not it
    # is (a failed/skipped Sentry init must never block startup or affect a request).
    SENTRY_DSN: str = os.getenv("SENTRY_DSN", "")
    # Free-text label shown on every event in the Sentry UI (e.g. "production", "staging") so
    # errors from a local dev run never get mixed in with real deployment errors.
    SENTRY_ENVIRONMENT: str = os.getenv("SENTRY_ENVIRONMENT", "development")
    # Fraction of requests to also capture full performance traces for (0.0-1.0). Low by default
    # -- this is an error-alerting feature first; tracing every request would be needless volume
    # (and, on Sentry's hosted free/paid tiers, needless cost) for what's fundamentally meant to
    # answer "did something break", not "profile every request".
    SENTRY_TRACES_SAMPLE_RATE: float = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0"))
    # Forwards this app's existing `logging.getLogger(...)` calls (already used throughout
    # backend/) to Sentry's Logs product too, in addition to full unhandled-exception events --
    # no new logging calls needed anywhere for this to work, it rides the logging this codebase
    # already does. Off by default -- same "opt in" rule as everything else here.
    SENTRY_ENABLE_LOGS: bool = os.getenv("SENTRY_ENABLE_LOGS", "false").strip().lower() == "true"
    # Enables sentry_sdk.metrics (count/gauge/distribution) -- see the small set of business
    # metrics this actually powers: complaint creation (routes/complaints.py), Ask Sarthi request
    # volume (routes/ask_janmitra.py), and rate-limit trips (middleware.py, deps.py). Those calls
    # are always present in the code (calling them with this off is a harmless no-op, confirmed
    # directly against the SDK) -- this flag only controls whether they actually get sent.
    SENTRY_ENABLE_METRICS: bool = os.getenv("SENTRY_ENABLE_METRICS", "false").strip().lower() == "true"
    # Fraction of trace "sessions" to also collect a code-level profile for (0.0-1.0) -- which
    # exact lines/functions were slow, not just which route. Only ever actually samples while
    # there's an active trace to attach to (profile_lifecycle="trace", set unconditionally in
    # init_error_monitoring() below) -- so this has no effect unless SENTRY_TRACES_SAMPLE_RATE is
    # also > 0. Off by default, same reasoning as SENTRY_TRACES_SAMPLE_RATE above.
    SENTRY_PROFILE_SESSION_SAMPLE_RATE: float = float(os.getenv("SENTRY_PROFILE_SESSION_SAMPLE_RATE", "0.0"))

    # Supported languages: short code -> (display name, Sarvam BCP-47 code)
    SUPPORTED_LANGUAGES: dict[str, dict[str, str]] = {
        "mr": {"name": "Marathi", "bcp47": "mr-IN"},
        "hi": {"name": "Hindi", "bcp47": "hi-IN"},
        "en": {"name": "English", "bcp47": "en-IN"},
        "or": {"name": "Odia", "bcp47": "od-IN"},
        "gu": {"name": "Gujarati", "bcp47": "gu-IN"},
        "bn": {"name": "Bengali", "bcp47": "bn-IN"},
    }


settings = Settings()


def get_prompt(filename: str) -> str:
    """Read and return the contents of a prompt file from the prompts directory.

    Args:
        filename: Name of the prompt file, e.g. "summary_prompt.txt".

    Returns:
        The prompt file contents as a string.
    """
    prompt_path = settings.PROMPTS_DIR / filename
    return prompt_path.read_text(encoding="utf-8")


def to_bcp47(language_code: str) -> str:
    """Map a short language code (e.g. "mr") to its Sarvam BCP-47 form (e.g. "mr-IN").

    Args:
        language_code: One of the short codes in SUPPORTED_LANGUAGES, e.g. "mr", "hi", "en".

    Returns:
        The corresponding BCP-47 language code used by the Sarvam AI APIs.

    Raises:
        ValueError: If the language code is not one of the supported languages.
    """
    language = settings.SUPPORTED_LANGUAGES.get(language_code)
    if language is None:
        raise ValueError(f"Unsupported language code: {language_code}")
    return language["bcp47"]


def from_bcp47(bcp47_code: str) -> str | None:
    """Reverse of to_bcp47() -- maps a Sarvam BCP-47 code (e.g. from its language-identification
    or speech-to-text auto-detect output) back to this app's short SUPPORTED_LANGUAGES code.

    Returns None (never raises) for a BCP-47 code this app has no SUPPORTED_LANGUAGES entry for
    -- e.g. Sarvam correctly detects Tamil/Telugu/etc. in a citizen's message, but this app has
    no UI copy or TTS voice configured for those, so there is nothing sensible to switch the
    response into. Callers (see orchestration/nodes.py's language_node) fall back to the
    citizen's originally-selected UI language in that case, exactly like a failed detection call.
    """
    for code, info in settings.SUPPORTED_LANGUAGES.items():
        if info["bcp47"] == bcp47_code:
            return code
    return None
