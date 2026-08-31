"""Thin wrapper around the Sarvam AI SDK for speech-to-text, translation, and text-to-speech."""

import base64
import io
import logging
import re
import wave

import httpx
from sarvamai import SarvamAI
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from backend.config import settings
from backend.services.sarvam_key_pool import SarvamKeyRotationMixin

logger = logging.getLogger(__name__)


class AIServiceError(Exception):
    """Raised when an external AI service call fails.

    Callers should catch this and return a clear error to the user instead
    of letting the failure crash the request.
    """


# Retry policy for the three real outbound Sarvam calls below (transcribe/synthesize/translate) --
# NOT applied to identify_language(), which is already deliberately fail-open (see its own
# docstring) and gains little from a retry. 3 attempts total (1 original + 2 retries), short
# exponential backoff (0.5s, then 1s, capped at 2s) -- fast/non-reasoning calls (STT/TTS/
# translation, unlike the reasoning-model callers), so there's no reason to wait long between
# attempts. `retry_if_not_exception_type(AIServiceError)` is the important detail: this decorator
# wraps only the raw SDK call itself (see each method below), so the only exceptions it ever sees
# are the SDK's own (network/timeout/5xx) -- excluding AIServiceError here is defense-in-depth
# against ever retrying something already identified as non-retryable (e.g. a missing API key),
# not something expected to trigger in practice given where the decorator is applied.
# `reraise=True` means after the final attempt fails, the original exception propagates unchanged
# (not wrapped in tenacity's own RetryError), so each method's existing `except Exception as exc:
# raise AIServiceError(...) from exc` still fires exactly as it did before this was added.
_retry_sarvam_call = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=2),
    retry=retry_if_not_exception_type(AIServiceError),
    reraise=True,
)


# LIVE-REPORTED BUG: Sarvam's TTS (bulbul:v2) silently truncates long input text with NO error and
# NO indication in the response (`audios` is still populated, no length/finish-reason field at
# all) -- confirmed directly, live: a real 318-character answer (an image caption + a follow-up
# question) came back as ~12.5s of audio that stops mid-answer, the entire follow-up question
# missing, while a 145-character version of the same answer came back complete at ~7.6s. Not a
# documented character limit (Sarvam's own docs describe a much higher ~2500-char cap for a
# different model version) -- looks like an undocumented ~12-13s max OUTPUT DURATION on this
# specific model, silently cutting whatever doesn't fit rather than erroring.
#
# Fixed the same way this app's own STT already handles the opposite direction of this problem
# (see stt_service.py's `transcribe_chunks()`): split into sentence-safe chunks, synthesize each
# separately, stitch the resulting WAV audio back into one file. `_TTS_SAFE_CHUNK_CHARS` is chosen
# with real margin below the observed ~12.5s cutoff (145 chars -> 7.6s intact, confirmed via a
# real round-trip: synthesized, then transcribed back, and checked the transcript covered the
# whole input) -- not the exact edge, deliberately, since the true limit is time-based and this
# app doesn't know each chunk's real speaking rate in advance.
_TTS_SAFE_CHUNK_CHARS = 180


def _split_text_for_tts(text: str, max_chars: int = _TTS_SAFE_CHUNK_CHARS) -> list[str]:
    """Splits `text` into chunks that each stay under `max_chars`, breaking only at sentence
    boundaries (never mid-sentence) so each chunk is still natural to synthesize on its own. A
    single sentence longer than `max_chars` is kept whole as its own (over-budget) chunk --
    correctly risking that one chunk being cut over cutting it awkwardly mid-word, and this app's
    own real answer text (short, template-shaped sentences) essentially never produces one."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    if not sentences:
        return [text] if text.strip() else []

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks


def _concatenate_wav_audio(wav_byte_chunks: list[bytes]) -> bytes:
    """Stitches several base64-decoded WAV audio chunks (same format -- all produced by the same
    Sarvam TTS call, just different text) into one WAV file. Uses the stdlib `wave` module, not a
    new dependency -- correct here specifically because every chunk shares the same sample
    format (mono/stereo, sample width, frame rate), all coming from the same TTS call in the same
    request; concatenating arbitrary WAV files with different formats would need real resampling,
    which this does not attempt."""
    frame_chunks: list[bytes] = []
    params = None
    for wav_bytes in wav_byte_chunks:
        with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
            if params is None:
                params = reader.getparams()
            frame_chunks.append(reader.readframes(reader.getnframes()))

    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setparams(params)
        for frames in frame_chunks:
            writer.writeframes(frames)
    return output.getvalue()


class SarvamClient(SarvamKeyRotationMixin):
    """Wraps the Sarvam AI SDK for speech-to-text, text translation, and text-to-speech calls."""

    def __init__(self) -> None:
        """Initialize the underlying SarvamAI SDK client, if an API key is configured."""
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
        self._init_sarvam_keys(timeout, settings.SARVAM_API_KEYS or settings.SARVAM_API_KEY)
        if self._client is None:
            logger.warning("SARVAM_API_KEY is not set; Sarvam calls will fail until configured.")

    def _require_client(self) -> SarvamAI:
        if self._client is None:
            raise AIServiceError("Sarvam AI is not configured (missing SARVAM_API_KEY).")
        return self._client

    @_retry_sarvam_call
    def _call_transcribe(self, client: SarvamAI, audio_file: io.BytesIO, language_code: str):
        # seek(0) before every attempt, including retries: audio_file is a BytesIO the SDK reads
        # from to build the multipart upload -- a failed first attempt can leave its read cursor
        # partway through (or at the end), and retrying without rewinding would silently upload an
        # empty/truncated file on the second attempt instead of actually retrying the real request.
        audio_file.seek(0)
        return client.speech_to_text.transcribe(file=audio_file, model="saaras:v3", language_code=language_code)

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
        self._require_client()
        logger.info("STT started (language=%s)", language_code)
        try:
            import io

            audio_file = io.BytesIO(audio_bytes)
            audio_file.name = "complaint.wav"
            response = self._call_sarvam(lambda client: self._call_transcribe(client, audio_file, language_code))
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
            response = self._call_sarvam(lambda client: client.text.identify_language(input=text))
            return response.language_code
        except Exception as exc:
            logger.warning("Language identification failed, falling back to caller-supplied language: %s", exc)
            return None

    @_retry_sarvam_call
    def _call_synthesize(self, client: SarvamAI, text: str, language_code: str, speaker: str, model: str):
        return client.text_to_speech.convert(
            text=text,
            language_code=language_code,
            speaker=speaker,
            model=model,
            output_audio_codec="wav",
            # Own, longer read timeout -- see settings.SARVAM_TTS_READ_TIMEOUT_SECONDS' docstring:
            # synthesis time scales with text length, unlike the fast STT/translation calls this
            # client's shared timeout was originally tuned for.
            request_options={"timeout_in_seconds": int(settings.SARVAM_TTS_READ_TIMEOUT_SECONDS)},
        )

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
        self._require_client()
        logger.info("TTS started (language=%s)", language_code)
        try:
            speaker_value = speaker or settings.TTS_SPEAKER
            model_value = model or settings.TTS_MODEL
            response = self._call_sarvam(lambda client: self._call_synthesize(client, text, language_code, speaker_value, model_value))
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

    def synthesize_speech_long(
        self, text: str, language_code: str, speaker: str | None = None, model: str | None = None
    ) -> str:
        """Same as `synthesize_speech()`, but safe for text longer than Sarvam's own undocumented
        per-call output-duration limit -- see `_TTS_SAFE_CHUNK_CHARS`'s own comment for the live-
        reproduced bug this closes. Splits `text` into sentence-safe chunks, synthesizes each
        separately, and stitches the resulting audio into one WAV file; a `text` short enough to
        need only one chunk makes exactly the same single call `synthesize_speech()` would.

        Args:
            text: The text to speak -- any length.
            language_code: BCP-47 language code to speak in, e.g. "mr-IN".
            speaker: One of Sarvam's named `bulbul` voices. Defaults to `settings.TTS_SPEAKER`.
            model: Sarvam TTS model version. Defaults to `settings.TTS_MODEL`.

        Returns:
            The synthesized audio as a base64-encoded WAV string.

        Raises:
            AIServiceError: If any chunk's Sarvam API call fails -- same fail-open contract as
                `synthesize_speech()` (the caller already treats a total TTS failure as a
                text-only degrade, see ask_sarthi_service.py's own TTS call site).
        """
        chunks = _split_text_for_tts(text)
        if len(chunks) <= 1:
            return self.synthesize_speech(text, language_code, speaker, model)

        logger.info("TTS: splitting %d-character answer into %d chunk(s)", len(text), len(chunks))
        audio_chunks = [
            base64.b64decode(self.synthesize_speech(chunk, language_code, speaker, model))
            for chunk in chunks
        ]
        combined = _concatenate_wav_audio(audio_chunks)
        return base64.b64encode(combined).decode("ascii")

    @_retry_sarvam_call
    def _call_translate(self, client: SarvamAI, text: str, source_language_code: str, target_language_code: str, model: str):
        return client.text.translate(
            input=text,
            source_language_code=source_language_code,
            target_language_code=target_language_code,
            model=model,
        )

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

        self._require_client()
        model = "mayura:v1" if source_language_code == "auto" else "sarvam-translate:v1"
        logger.info(
            "Translation started (%s -> %s, model=%s)", source_language_code, target_language_code, model
        )
        try:
            response = self._call_sarvam(lambda client: self._call_translate(client, text, source_language_code, target_language_code, model))
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
