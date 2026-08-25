"""Thin wrapper around the Sarvam AI SDK for speech-to-text, translation, and text-to-speech."""

import logging

import httpx
from sarvamai import SarvamAI

from backend.config import settings

logger = logging.getLogger(__name__)


class AIServiceError(Exception):
    """Raised when an external AI service call fails.

    Callers should catch this and return a clear error to the user instead
    of letting the failure crash the request.
    """


class SarvamClient:
    """Wraps the Sarvam AI SDK for speech-to-text, text translation, and text-to-speech calls."""

    def __init__(self) -> None:
        """Initialize the underlying SarvamAI SDK client, if an API key is configured."""
        self._client: SarvamAI | None = None
        if settings.SARVAM_API_KEY:
            # See config.py's SARVAM_CONNECT_TIMEOUT_SECONDS docstring for why this is a separate
            # connect/read timeout rather than a bare float -- STT/translation/TTS are all fast,
            # non-reasoning calls, so a short read timeout is correct here (unlike the two
            # reasoning-model callers, summary_service.py/answer_generation_service.py).
            timeout = httpx.Timeout(
                connect=settings.SARVAM_CONNECT_TIMEOUT_SECONDS,
                read=settings.SARVAM_REQUEST_TIMEOUT_SECONDS,
                write=settings.SARVAM_REQUEST_TIMEOUT_SECONDS,
                pool=settings.SARVAM_REQUEST_TIMEOUT_SECONDS,
            )
            self._client = SarvamAI(api_subscription_key=settings.SARVAM_API_KEY, timeout=timeout)
        else:
            logger.warning("SARVAM_API_KEY is not set; Sarvam calls will fail until configured.")

    def _require_client(self) -> SarvamAI:
        if self._client is None:
            raise AIServiceError("Sarvam AI is not configured (missing SARVAM_API_KEY).")
        return self._client

    def transcribe(self, audio_bytes: bytes, language_code: str) -> str:
        """Transcribe spoken audio to text using Sarvam's speech-to-text model.

        Args:
            audio_bytes: Raw audio file bytes (e.g. wav/mp3) recorded by the citizen.
            language_code: BCP-47 language code of the spoken audio, e.g. "mr-IN".

        Returns:
            The transcribed text.

        Raises:
            AIServiceError: If the Sarvam API call fails for any reason.
        """
        client = self._require_client()
        logger.info("STT started (language=%s)", language_code)
        try:
            import io

            audio_file = io.BytesIO(audio_bytes)
            audio_file.name = "complaint.wav"
            response = client.speech_to_text.transcribe(
                file=audio_file,
                model="saaras:v3",
                language_code=language_code,
            )
            transcript = getattr(response, "transcript", None) or ""
            logger.info("STT completed (language=%s)", language_code)
            return transcript
        except Exception as exc:
            logger.error("STT failed (language=%s): %s", language_code, exc, exc_info=True)
            raise AIServiceError("Speech-to-text service failed. Please try again.") from exc

    def identify_language(self, text: str) -> str | None:
        """Detects the actual language of typed/transcribed text via Sarvam's dedicated
        language-identification model -- used so Ask Sarthi can answer in whatever language a
        citizen actually asked in, ChatGPT/Claude-style, rather than blindly trusting a possibly
        stale UI language toggle (see orchestration/nodes.py's `language_node` docstring for the
        live-reported mismatch this closes).

        Best-effort by design, unlike this class's other methods: a bilingual citizen asking one
        question is not worth failing the whole turn over if this specific signal is unavailable,
        so this returns None (never raises) on any failure -- including no API key configured --
        and callers fall back to the caller-supplied language in that case.

        Args:
            text: The text to detect the language of.

        Returns:
            The detected BCP-47 language code (e.g. "mr-IN"), or None if detection failed or the
            service returned nothing usable.
        """
        if self._client is None:
            return None
        try:
            response = self._client.text.identify_language(input=text)
            return response.language_code
        except Exception as exc:
            logger.warning("Language identification failed, falling back to caller-supplied language: %s", exc)
            return None

    def synthesize_speech(
        self, text: str, language_code: str, speaker: str | None = None, model: str | None = None
    ) -> str:
        """Convert text to spoken audio using Sarvam's text-to-speech model.

        Args:
            text: The text to speak.
            language_code: BCP-47 language code to speak in, e.g. "mr-IN".
            speaker: One of Sarvam's named `bulbul` voices. Defaults to `settings.TTS_SPEAKER`.
            model: Sarvam TTS model version. Defaults to `settings.TTS_MODEL`.

        Returns:
            The synthesized audio as a base64-encoded WAV string.

        Raises:
            AIServiceError: If the Sarvam API call fails for any reason.
        """
        client = self._require_client()
        logger.info("TTS started (language=%s)", language_code)
        try:
            response = client.text_to_speech.convert(
                text=text,
                language_code=language_code,
                speaker=speaker or settings.TTS_SPEAKER,
                model=model or settings.TTS_MODEL,
                output_audio_codec="wav",
            )
            audios = getattr(response, "audios", None) or []
            if not audios:
                raise AIServiceError("Text-to-speech service returned no audio.")
            logger.info("TTS completed (language=%s)", language_code)
            return audios[0]
        except AIServiceError:
            raise
        except Exception as exc:
            logger.error("TTS failed (language=%s): %s", language_code, exc, exc_info=True)
            raise AIServiceError("Text-to-speech service failed. Please try again.") from exc

    def translate(self, text: str, source_language_code: str, target_language_code: str) -> str:
        """Translate text between two languages using Sarvam's translation model.

        Args:
            text: The text to translate.
            source_language_code: BCP-47 code of the source language, e.g. "mr-IN" -- or the
                literal string "auto" to have Sarvam detect the source language itself. Only
                Sarvam's mayura:v1 model supports auto-detection, so passing "auto" switches the
                model used for this call from sarvam-translate:v1 to mayura:v1 (still covers all
                of this app's SUPPORTED_LANGUAGES -- see complaint_update_translation_cache.py for
                why auto-detection is needed at all).
            target_language_code: BCP-47 code of the target language, e.g. "en-IN".

        Returns:
            The translated text.

        Raises:
            AIServiceError: If the Sarvam API call fails for any reason.
        """
        if source_language_code == target_language_code:
            return text

        client = self._require_client()
        model = "mayura:v1" if source_language_code == "auto" else "sarvam-translate:v1"
        logger.info(
            "Translation started (%s -> %s, model=%s)", source_language_code, target_language_code, model
        )
        try:
            response = client.text.translate(
                input=text,
                source_language_code=source_language_code,
                target_language_code=target_language_code,
                model=model,
            )
            translated = getattr(response, "translated_text", None) or ""
            logger.info(
                "Translation completed (%s -> %s)", source_language_code, target_language_code
            )
            return translated
        except Exception as exc:
            logger.error(
                "Translation failed (%s -> %s): %s",
                source_language_code,
                target_language_code,
                exc,
                exc_info=True,
            )
            raise AIServiceError("Translation service failed. Please try again.") from exc
