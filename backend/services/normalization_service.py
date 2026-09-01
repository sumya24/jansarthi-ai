"""Spelling cleanup for complaint text, run in the citizen's own language before translation.

Citizens can type or speak with typos in any of the supported languages (e.g. "ara" for
"area" in English, or an equivalent slip in Marathi/Hindi script). Sarvam's translate
endpoint is literal and doesn't correct spelling, so an uncorrected typo would produce a
bad English translation that then gets re-mistranslated on every future read into a
worker's chosen display language. Cleaning the text up once, in its original language
before it's ever translated, fixes it at the source regardless of which language was used.
"""

import logging

import httpx
from sarvamai import SarvamAI

from backend.config import get_prompt, settings
from backend.services.fake_sarvam_sdk import FakeSarvamAI
from backend.services.sarvam_key_pool import SarvamKeyRotationMixin

logger = logging.getLogger(__name__)


class NormalizationService(SarvamKeyRotationMixin):
    """Corrects obvious spelling/typing mistakes in complaint text via Sarvam's chat API."""

    def __init__(self) -> None:
        """Initialize the underlying SarvamAI client, if an API key is configured."""
        # Chat-completion call against the same reasoning model as summary_service.py/
        # answer_generation_service.py (LLM_MODEL) -- see config.py's SARVAM_CONNECT_TIMEOUT_
        # SECONDS docstring for why this needs the generous reasoning read-timeout, not the
        # short one sarvam_client.py's fast STT/translation/TTS calls use.
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
            logger.warning("LLM_API_KEY is not set; text normalization will be skipped.")

    def normalize(self, text: str, language_code: str) -> str:
        """Return a spelling-corrected version of complaint text, in the same language.

        This is a quality enhancement, not a critical step: any failure (missing API
        key, empty model response, network error) falls back to the original text
        unchanged rather than raising, so it never blocks complaint submission.

        Args:
            text: Complaint text as the citizen wrote or spoke it.
            language_code: Short language code the text is in, e.g. "mr", "hi", "en".

        Returns:
            The spelling-corrected text in the same language, or the original text
            if normalization could not be performed.
        """
        if self._client is None:
            return text

        try:
            language_name = settings.SUPPORTED_LANGUAGES.get(language_code, {}).get("name", language_code)
            prompt_template = get_prompt("normalize_prompt.txt")
            prompt = prompt_template.format(complaint_text=text, language_name=language_name)
            system_prompt = get_prompt("normalize_system_prompt.txt")

            logger.info("Text normalization started (language=%s)", language_code)
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
                logger.warning(
                    "Text normalization returned no content (finish_reason=%s); using original text.",
                    choice.finish_reason,
                )
                return text
            logger.info("Text normalization completed")
            return content.strip()
        except Exception as exc:
            logger.warning("Text normalization failed, using original text unchanged: %s", exc, exc_info=True)
            return text
