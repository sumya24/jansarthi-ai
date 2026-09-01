"""Real-model complaint category classification via Sarvam's chat completion API.

First layer of the Report an Issue wizard's three-layer category classification (see
ReportIssue.tsx and frontend/lib/serviceCategories.tsx's guessServiceCategory): this real model
call is tried first, falling back to a client-side keyword match if it returns None, and finally
to the citizen picking a category themselves if neither layer is confident. Deliberately NOT the
approach backend/services/intent_classifier.py takes for Ask Sarthi's own routing (that one is
rule-based on purpose -- see its own module docstring on why routing should stay fast, free, and
deterministic) -- a complaint's category has a real operational consequence (which worker team
sees it) that Ask Sarthi's routing doesn't, so a smarter first layer earns its cost/latency here
in a way it wouldn't there.

Modeled directly on normalization_service.py's own "quality enhancement, never blocks the flow"
pattern -- every failure mode here (missing API key, network error, timeout, empty/unparseable
model response) returns None rather than raising, so a slow or down Sarvam account can only ever
degrade classification quality, never block complaint submission.
"""

import logging

import httpx
from sarvamai import SarvamAI

from backend.config import get_prompt, settings
from backend.schemas.rag_knowledge import ServiceCategory
from backend.services.fake_sarvam_sdk import FakeSarvamAI
from backend.services.sarvam_key_pool import SarvamKeyRotationMixin

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = {c.value for c in ServiceCategory}


class ComplaintCategoryService(SarvamKeyRotationMixin):
    """Classifies a complaint's English-language text into one of the 4 civic-service
    categories, or None if the model isn't configured, fails, or isn't confident."""

    def __init__(self) -> None:
        """Initialize the underlying SarvamAI client, if an API key is configured."""
        # Same reasoning model + timeout shape as summary_service.py/normalization_service.py
        # -- see config.py's SARVAM_CONNECT_TIMEOUT_SECONDS docstring for why this needs the
        # generous reasoning read-timeout, not the short one sarvam_client.py's fast STT/
        # translation/TTS calls use.
        timeout = httpx.Timeout(
            connect=settings.SARVAM_CONNECT_TIMEOUT_SECONDS,
            read=settings.SARVAM_REASONING_READ_TIMEOUT_SECONDS,
            write=settings.SARVAM_REASONING_READ_TIMEOUT_SECONDS,
            pool=settings.SARVAM_REASONING_READ_TIMEOUT_SECONDS,
        )
        self._init_sarvam_keys(
            timeout,
            settings.SARVAM_API_KEYS or settings.LLM_API_KEY,
            client_factory=FakeSarvamAI if settings.SARVAM_MOCK_MODE else SarvamAI,
        )
        if self._client is None:
            logger.warning("LLM_API_KEY is not set; complaint category classification will be skipped.")

    def classify(self, english_text: str) -> ServiceCategory | None:
        """Best-effort classification -- returns None (never raises) on any failure, missing
        configuration, or a genuinely unsure model response, so the caller can fall back to the
        keyword match / manual picker exactly as if this layer didn't exist."""
        if self._client is None or not english_text.strip():
            return None

        try:
            prompt_template = get_prompt("complaint_category_prompt.txt")
            prompt = prompt_template.format(complaint_text=english_text)

            # Same reasoning_effort="low" as summary_service.py -- a one-word classification
            # needs even less of the reasoning-token budget than a 1-2 sentence summary does, but
            # "low" is already the floor this SDK exposes.
            response = self._call_sarvam(lambda client: client.chat.completions(
                model=settings.LLM_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You classify civic complaints into fixed categories. Respond with "
                            "only the category name or UNSURE, nothing else."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=settings.LLM_MAX_TOKENS,
                reasoning_effort="low",
            ))
            choice = response.choices[0]
            content = choice.message.content
            if not content or not content.strip():
                logger.warning(
                    "Complaint category classification returned no content (finish_reason=%s)",
                    choice.finish_reason,
                )
                return None

            category_str = content.strip().upper()
            if category_str not in _VALID_CATEGORIES:
                if category_str != "UNSURE":
                    logger.warning(
                        "Complaint category classification returned an unrecognized value: %r",
                        category_str,
                    )
                return None
            return ServiceCategory(category_str)
        except Exception as exc:
            logger.warning("Complaint category classification failed, falling back: %s", exc)
            return None
