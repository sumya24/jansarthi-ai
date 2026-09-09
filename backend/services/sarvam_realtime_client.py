"""Async wrapper around Sarvam's realtime (WebSocket) speech-to-text and streaming text-to-speech
APIs -- the low-latency, always-listening leg of "Live" voice mode (see routes/ask_sarthi.py's
`/voice/live` WebSocket route). Deliberately a SEPARATE class from `SarvamClient`
(sarvam_client.py), not an extension of it: `SarvamClient`/`SarvamKeyRotationMixin` are built
around the SYNC `SarvamAI` SDK client and its request/response call shape, while realtime STT and
streaming TTS live on `AsyncSarvamAI`, a different class with a fundamentally different
(connect-then-send/recv-over-a-socket) usage shape that doesn't fit that mixin's `_call_sarvam`
wrapper.

Key handling: uses only the FIRST configured Sarvam key (same source as SarvamClient --
`SARVAM_API_KEYS` or `SARVAM_API_KEY`). Mid-stream key rotation on a 402 quota-exhausted error
(what `SarvamKeyRotationMixin` does for the batch client) is deliberately not implemented here for
v1 -- reconnecting and retrying the whole utterance on failure is an acceptable fallback for a new,
opt-in feature; revisit only if this becomes the primary voice path.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager

from sarvamai import AsyncSarvamAI

from backend.config import settings
from backend.services.sarvam_client import AIServiceError

_REALTIME_STT_MODEL = "saaras:v3-realtime"
_STREAMING_TTS_MODEL = "bulbul:v3"
_REALTIME_AUDIO_ENCODING = "linear16"
_REALTIME_AUDIO_SAMPLE_RATE = "16000"


class SarvamRealtimeClient:
    """Opens realtime STT / streaming TTS WebSocket sessions against Sarvam. Each `open_*` method
    returns the SDK's own async context manager directly (not re-wrapped) -- the caller drives the
    send/recv loop itself, since that loop needs to interleave with the caller's own WebSocket to
    the browser (forwarding audio in, streaming audio out) in a way a further abstraction here
    would only get in the way of."""

    def __init__(self) -> None:
        keys_config = settings.SARVAM_API_KEYS or settings.SARVAM_API_KEY
        first_key = next((key.strip() for key in keys_config.split(",") if key.strip()), None) if keys_config else None
        self._api_key = first_key
        self._client = AsyncSarvamAI(api_subscription_key=first_key) if first_key else None

    def configured(self) -> bool:
        return self._client is not None

    def open_transcription_stream(self, language_code: str) -> AbstractAsyncContextManager:
        """Realtime STT session: `async with` this, then loop `socket.send_realtime_audio_input(...)`
        with incoming mic audio and `await socket.recv()` for `RealtimeVadSpeechStart/End` and
        `RealtimeTranscriptPartial/Final` events -- Sarvam's own VAD (`endpointing="vad"`) decides
        where one utterance ends, so the caller never needs its own silence-detection logic."""
        if self._client is None:
            raise AIServiceError("Sarvam AI is not configured (missing SARVAM_API_KEY).")
        return self._client.speech_to_text_realtime_streaming.connect(
            language_code=language_code,
            model=_REALTIME_STT_MODEL,
            endpointing="vad",
            encoding=_REALTIME_AUDIO_ENCODING,
            sample_rate=_REALTIME_AUDIO_SAMPLE_RATE,
        )

    def open_tts_stream(self) -> AbstractAsyncContextManager:
        """Streaming TTS session: `async with` this, then `socket.configure(...)`, `socket.convert(text)`,
        `socket.flush()`, and loop `await socket.recv()` for `AudioOutput` chunks -- each one
        playable the moment it arrives, rather than waiting for the whole answer to synthesize."""
        if self._client is None:
            raise AIServiceError("Sarvam AI is not configured (missing SARVAM_API_KEY).")
        return self._client.text_to_speech_streaming.connect(model=_STREAMING_TTS_MODEL)
