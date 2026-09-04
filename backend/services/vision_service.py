"""Image understanding for Ask Sarthi -- Google Gemini's free tier as the primary captioner, a
local open-source vision-language model as an automatic fallback.

Originally local-model-only: Sarvam (this project's only other AI vendor) has no vision
capability, and a new hosted vendor/API key was deliberately avoided for this feature at first
(see git history). Revisited 2026-08-27, LIVE-REPORTED DECISION: the local model was also what was
silently crashing the whole backend process under real memory pressure -- reproduced 3 times on
this dev machine, and a genuine risk on the smaller production VM too (2 vCPU / 3.8GB, less
headroom than dev). Gemini's free tier (confirmed live against a real API key and a real
image+text call the same day: no credit card, real image input support, current free-tier model is
`gemini-3.5-flash-lite` -- see backend/config.py's GEMINI_VISION_MODEL comment) is now tried
FIRST; the local model is kept as an automatic fallback -- same "primary + graceful degradation"
shape as every other AI call in this app (Sarvam LLM -> raw excerpts on failure, translation
failure -> English). `GEMINI_API_KEY` unset (the default) skips straight to the local model,
unchanged from before this revision.

**Local fallback model: `vikhyatk/moondream2`** (~1.9B params, via `transformers`,
`trust_remote_code=True`) -- purpose-built for CPU-friendly image captioning/VQA, small enough to
run without a GPU. Loaded lazily (first load downloads/initializes real model weights -- real
time, like `SentenceTransformerEmbeddingProvider` in embedding_provider.py, the precedent this
class follows), so importing this module never pays that cost, and a request that Gemini
successfully answers never touches it at all.

LIVE-REPORTED: this fallback used to have no time limit at all -- confirmed directly against
production, a real citizen photo left a request stuck on "Thinking..." for over 15 minutes (no
GPU on this VM; a large/detailed image can genuinely take that long on CPU). Unlike Gemini's own
call (a plain httpx `timeout=`), the local model's inference is a synchronous, CPU-bound Python
call with nothing to cancel it mid-flight -- `describe_image()` now runs it on a worker thread and
gives up waiting after `settings.VISION_LOCAL_MODEL_TIMEOUT_SECONDS` (see that setting's own
comment for why 45s), raising VisionServiceError the same way any other failure on this path
already does. The worker thread itself isn't killed (Python can't force-stop a running thread) --
it keeps using CPU in the background until it finishes or the process exits -- but the citizen's
own request is never blocked on it past the timeout, and every other caller of this service
already treats a VisionServiceError as best-effort (see that exception's own docstring).
"""

from __future__ import annotations

import base64
import io
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

logger = logging.getLogger(__name__)

# One shared pool for every local-model call this process ever makes -- a plain daemon thread per
# call (rather than a whole ThreadPoolExecutor) would work too, but this keeps the "give up after
# N seconds, thread keeps running in the background" contract explicit and reusable without
# hand-rolling it at each call site.
_local_model_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="vision-local-model")

_CAPTION_PROMPT = (
    "Describe this image in one or two sentences, focusing on any civic issue visible such as a "
    "pothole, garbage or waste, a water leak or drainage problem, or a broken streetlight. If "
    "there is no such issue visible, just describe what the image shows."
)

_GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class VisionServiceError(Exception):
    """Raised when image understanding fails on every available path (Gemini AND the local
    fallback, or Gemini skipped/failed and the local model itself couldn't load/run).

    Callers should catch this and treat image understanding as best-effort: an image whose
    caption fails still gets attached to a complaint as evidence, it just won't enrich the
    citizen's text with a description (see orchestration/nodes.py's input_processing_node).
    """


class VisionService:
    """Captions citizen-attached photos: Gemini's free tier first, a local model as fallback.

    Per-call result state (which provider actually served the last call, and its real token
    count if known) is kept in thread-local storage, not on `self` directly -- this instance is a
    shared singleton reused across concurrent requests (see AskSarthiService.__init__), and two
    citizens' requests interleaving on different threads must never read back each other's result.
    """

    def __init__(self, model_name: str | None = None) -> None:
        from backend.config import settings

        self._model_name = model_name or settings.VISION_MODEL_NAME
        self._model = None  # lazy-loaded, see _get_model
        self._tokenizer = None
        self._local = threading.local()

    @property
    def model_name(self) -> str:
        """The model that served the MOST RECENT call on the calling thread -- Gemini's model id
        if that call succeeded via Gemini, else this instance's configured local fallback model.
        Before any call has been made on this thread, returns the configured local model name
        (matches this property's pre-Gemini behavior, and what `test_vision_service.py`'s own
        `test_model_name_defaults_from_settings` already asserts)."""
        return getattr(self._local, "model_used", None) or self._model_name

    def load(self) -> None:
        """Forces the (slow, first-time) LOCAL model load now rather than lazily on first call --
        useful for warming the model at process startup instead of on a citizen's first request.
        Does not touch Gemini (nothing to "load" for a hosted API)."""
        self._get_model()

    def _get_model(self):
        if self._model is None:
            try:
                from transformers import AutoModelForCausalLM, AutoTokenizer
            except ImportError as exc:
                raise VisionServiceError(
                    "Vision model dependencies are not installed."
                ) from exc

            logger.info("Loading vision model %s (first load may take a while)...", self._model_name)
            try:
                self._model = AutoModelForCausalLM.from_pretrained(
                    self._model_name, trust_remote_code=True
                )
                self._tokenizer = AutoTokenizer.from_pretrained(self._model_name)
            except Exception as exc:
                self._model = None
                self._tokenizer = None
                logger.error("Failed to load vision model %s: %s", self._model_name, exc, exc_info=True)
                raise VisionServiceError("Vision model failed to load.") from exc
            logger.info("Vision model %s loaded", self._model_name)
        return self._model

    def _describe_via_gemini(self, image_bytes: bytes) -> str | None:
        """Best-effort: returns a real caption on success, or `None` (never raises) if Gemini
        isn't configured or the call fails for any reason -- `describe_image()` falls back to the
        local model in either case, so a Gemini outage/rate-limit/bad-key never costs a citizen
        their caption, just this app's preferred, more-accurate provider for it."""
        from backend.config import settings

        if not settings.GEMINI_API_KEY:
            return None
        try:
            import httpx

            image_b64 = base64.b64encode(image_bytes).decode("ascii")
            response = httpx.post(
                f"{_GEMINI_API_BASE}/{settings.GEMINI_VISION_MODEL}:generateContent",
                params={"key": settings.GEMINI_API_KEY},
                json={
                    "contents": [{
                        "parts": [
                            {"text": _CAPTION_PROMPT},
                            {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                        ],
                    }],
                },
                timeout=settings.GEMINI_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
            caption = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            if not caption:
                return None
            self._local.model_used = settings.GEMINI_VISION_MODEL
            # Real, reported usage straight from Gemini's own response -- more accurate than the
            # local model's tokenizer re-count fallback (see count_tokens() below), and free
            # (no separate call needed).
            self._local.token_count = (data.get("usageMetadata") or {}).get("candidatesTokenCount")
            logger.info(
                "Vision captioning completed via Gemini (%s, caption_length=%d)",
                settings.GEMINI_VISION_MODEL, len(caption),
            )
            return caption
        except Exception as exc:
            logger.warning(
                "Gemini image captioning failed, falling back to the local model: %s", exc, exc_info=True
            )
            return None

    def describe_image(self, image_bytes: bytes) -> str:
        """Generate a short text caption for an attached photo -- Gemini first, the local model
        as an automatic fallback.

        Args:
            image_bytes: Raw image file bytes (jpeg/png), already validated by the caller.

        Returns:
            A short text description of the image, tuned toward civic-issue photos.

        Raises:
            VisionServiceError: If the image can't be decoded, or every available path (Gemini,
                then the local model) failed to produce a caption.
        """
        try:
            from PIL import Image
        except ImportError as exc:
            raise VisionServiceError("Image decoding dependency is not installed.") from exc

        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        except Exception as exc:
            logger.error("Failed to decode image for vision captioning: %s", exc, exc_info=True)
            raise VisionServiceError("Could not read the attached image.") from exc

        gemini_caption = self._describe_via_gemini(image_bytes)
        if gemini_caption is not None:
            return gemini_caption

        from backend.config import settings

        model = self._get_model()

        def _run_local_inference() -> str:
            encoded_image = model.encode_image(image)
            caption = model.answer_question(encoded_image, _CAPTION_PROMPT, self._tokenizer)
            return (caption or "").strip()

        # Run on a worker thread with a hard wait limit -- this is a synchronous, CPU-bound call
        # with no `timeout=` of its own to pass in (unlike Gemini's httpx call above), so a
        # timeout can only be enforced by giving up on waiting for it, not by cancelling it (see
        # this module's own docstring). The worker thread keeps running in the background even
        # after this raises -- deliberately not joined/cancelled, since Python has no safe way to
        # force-stop a running thread.
        future = _local_model_executor.submit(_run_local_inference)
        try:
            caption = future.result(timeout=settings.VISION_LOCAL_MODEL_TIMEOUT_SECONDS)
        except FutureTimeoutError as exc:
            logger.error(
                "Vision captioning via the local model exceeded %ss; giving up (the model call keeps "
                "running in the background, but this request no longer waits on it).",
                settings.VISION_LOCAL_MODEL_TIMEOUT_SECONDS,
            )
            raise VisionServiceError("Image understanding timed out. Please try again.") from exc
        except Exception as exc:
            logger.error("Vision captioning failed: %s", exc, exc_info=True)
            raise VisionServiceError("Image understanding failed. Please try again.") from exc

        self._local.model_used = self._model_name
        self._local.token_count = None  # not known upfront -- see count_tokens()'s own tokenizer path
        logger.info("Vision captioning completed via local model (caption_length=%d)", len(caption))
        return caption

    def count_tokens(self, text: str) -> int | None:
        """Real token count for `text` -- Gemini's own reported usage if the most recent call on
        this thread was served by Gemini (see `_describe_via_gemini`), else this model's own
        tokenizer as a best-effort estimate. For observability only (Phoenix's token-count
        attributes, see ask_sarthi_service.py's `vision_span`), never for routing/business
        logic. A method separate from `describe_image()`'s own return value, deliberately: that
        method is mocked as a bare string return in five existing test files, and this app
        already has one real regression on record from a signature change swept incompletely (see
        PHOENIX_TRACING_PLAN.md) -- not worth repeating for what's purely a nice-to-have metric.

        Best-effort: returns `None` (never raises) if no real count is available from either
        source."""
        gemini_count = getattr(self._local, "token_count", None)
        if gemini_count is not None:
            return gemini_count
        if self._tokenizer is None:
            return None
        try:
            return len(self._tokenizer.encode(text))
        except Exception:
            logger.warning("Vision caption token count failed (caption still succeeded).", exc_info=True)
            return None
