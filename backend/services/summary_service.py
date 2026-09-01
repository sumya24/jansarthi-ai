"""Short summary generation for translated complaint text using Sarvam's chat completion API."""

import logging

import httpx
from sarvamai import SarvamAI

from backend.config import get_prompt, settings
from backend.services.fake_sarvam_sdk import FakeSarvamAI
from backend.services.sarvam_client import AIServiceError
from backend.services.sarvam_key_pool import SarvamKeyRotationMixin

logger = logging.getLogger(__name__)


class SummaryService(SarvamKeyRotationMixin):
    """Generates a short 1-2 line summary of a complaint via Sarvam's chat completion API."""

    def __init__(self) -> None:
        """Initialize the underlying SarvamAI client, if an API key is configured."""
        # This is the exact call that produced the real ~30s ConnectTimeout failure this fix
        # is responding to -- a reasoning-model call (same LLM_MODEL as answer_generation_
        # service.py/normalization_service.py), so it gets the generous read timeout, just a
        # much shorter connect timeout than the SDK's 60s default. See config.py's
        # SARVAM_CONNECT_TIMEOUT_SECONDS docstring for the full reasoning.
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
            logger.warning("LLM_API_KEY is not set; summary generation will fail until configured.")

    def summarize(self, english_text: str) -> str:
        """Generate a short summary of a complaint that has already been translated to English.

        Args:
            english_text: The complaint text in English.

        Returns:
            A 1-2 line summary of the complaint.

        Raises:
            AIServiceError: If the chat completion call fails for any reason.
        """
        if self._client is None:
            raise AIServiceError("Summary service is not configured (missing LLM_API_KEY).")

        logger.info("Summary generation started")
        try:
            prompt_template = get_prompt("summary_prompt.txt")
            prompt = prompt_template.format(complaint_text=english_text)
            system_prompt = get_prompt("system_prompt.txt")

            # sarvam-105b is a reasoning model: with the default reasoning effort it can
            # burn through the entire max_tokens budget on internal reasoning_content and
            # never emit a final answer. "low" keeps enough budget free for the actual
            # 1-2 sentence summary this prompt asks for.
            response = self._call_sarvam(lambda client: client.chat.completions(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=settings.LLM_MAX_TOKENS,
                reasoning_effort="low",
            ))
            choice = response.choices[0]
            content = choice.message.content
            if not content or not content.strip():
                logger.error(
                    "Summary generation returned no content (finish_reason=%s); "
                    "the model likely ran out of tokens before producing a final answer.",
                    choice.finish_reason,
                )
                raise AIServiceError("Summary service returned an empty response. Please try again.")
            summary = content.strip()
            logger.info("Summary generation completed")
            return summary
        except AIServiceError:
            raise
        except Exception as exc:
            logger.error("Summary generation failed: %s", exc, exc_info=True)
            raise AIServiceError("Summary service failed. Please try again.") from exc
