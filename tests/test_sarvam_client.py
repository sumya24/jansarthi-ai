"""Tests for backend/services/sarvam_client.py's synthesize_speech() -- the new text-to-speech
method backing Ask Sarthi's voice assistant. Mocks the underlying SarvamAI SDK client, same
convention as ComplaintAgent's tests mocking SarvamClient itself -- never a real network call.
"""

import base64
import io
import wave
from unittest.mock import Mock

import pytest

from backend.services.sarvam_client import (
    AIServiceError,
    SarvamClient,
    _concatenate_wav_audio,
    _split_text_for_tts,
)


def _client_with_fake_sdk(convert_return=None, convert_side_effect=None) -> SarvamClient:
    client = SarvamClient.__new__(SarvamClient)  # bypass __init__'s real SarvamAI() construction
    fake_sdk = Mock()
    if convert_side_effect is not None:
        fake_sdk.text_to_speech.convert.side_effect = convert_side_effect
    else:
        fake_sdk.text_to_speech.convert.return_value = convert_return
    client._client = fake_sdk
    return client


def test_synthesize_speech_returns_base64_audio_from_sdk():
    fake_response = Mock(audios=["dGVzdC1hdWRpby1ieXRlcw=="])
    client = _client_with_fake_sdk(convert_return=fake_response)

    audio = client.synthesize_speech("Hello there.", "en-IN")

    assert audio == "dGVzdC1hdWRpby1ieXRlcw=="


def test_synthesize_speech_passes_defaults_from_settings():
    fake_response = Mock(audios=["abc"])
    client = _client_with_fake_sdk(convert_return=fake_response)

    client.synthesize_speech("Hello there.", "hi-IN")

    _, kwargs = client._client.text_to_speech.convert.call_args
    assert kwargs["text"] == "Hello there."
    assert kwargs["language_code"] == "hi-IN"
    assert kwargs["speaker"] == "anushka"
    assert kwargs["model"] == "bulbul:v2"
    assert kwargs["output_audio_codec"] == "wav"


def test_synthesize_speech_honors_explicit_speaker_and_model():
    fake_response = Mock(audios=["abc"])
    client = _client_with_fake_sdk(convert_return=fake_response)

    client.synthesize_speech("Hello there.", "en-IN", speaker="kabir", model="bulbul:v3")

    _, kwargs = client._client.text_to_speech.convert.call_args
    assert kwargs["speaker"] == "kabir"
    assert kwargs["model"] == "bulbul:v3"


def test_synthesize_speech_raises_ai_service_error_on_sdk_failure():
    client = _client_with_fake_sdk(convert_side_effect=RuntimeError("network exploded"))

    with pytest.raises(AIServiceError):
        client.synthesize_speech("Hello there.", "en-IN")


def test_synthesize_speech_raises_ai_service_error_on_empty_audio_list():
    fake_response = Mock(audios=[])
    client = _client_with_fake_sdk(convert_return=fake_response)

    with pytest.raises(AIServiceError):
        client.synthesize_speech("Hello there.", "en-IN")


def test_synthesize_speech_requires_configured_client():
    client = SarvamClient.__new__(SarvamClient)
    client._client = None

    with pytest.raises(AIServiceError):
        client.synthesize_speech("Hello there.", "en-IN")


# --- identify_language() -- backs the auto-detect-response-language fix (see
# orchestration/nodes.py's language_node docstring for the live-reported mismatch this closes) ---


def test_identify_language_returns_detected_bcp47_code():
    fake_response = Mock(language_code="mr-IN")
    client = _client_with_fake_sdk()
    client._client.text.identify_language.return_value = fake_response

    detected = client.identify_language("बेंगळुरूमध्ये पाणीपुरवठ्याबाबत तक्रार")

    assert detected == "mr-IN"
    _, kwargs = client._client.text.identify_language.call_args
    assert kwargs["input"] == "बेंगळुरूमध्ये पाणीपुरवठ्याबाबत तक्रार"


def test_identify_language_is_best_effort_returns_none_on_sdk_failure():
    """Unlike this class's other methods, a failed detection must never raise -- it's a
    best-effort signal a caller falls back from, not a hard dependency (see
    TranslationService.detect_language's own docstring)."""
    client = _client_with_fake_sdk()
    client._client.text.identify_language.side_effect = RuntimeError("network exploded")

    assert client.identify_language("some text") is None


def test_identify_language_returns_none_without_a_configured_client():
    client = SarvamClient.__new__(SarvamClient)
    client._client = None

    assert client.identify_language("some text") is None


# --- synthesize_speech_long() -- see this module's own _TTS_SAFE_CHUNK_CHARS comment for the
# live-reproduced bug this closes (Sarvam's TTS silently truncates long text, no error at all). ---


def _wav_bytes(num_frames: int, framerate: int = 8000) -> bytes:
    """A minimal, genuinely valid mono 16-bit WAV of silence -- real enough for the stdlib `wave`
    module to read back frame counts from, which is exactly what these tests check."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(framerate)
        writer.writeframes(b"\x00\x00" * num_frames)
    return buf.getvalue()


def test_split_text_for_tts_keeps_short_text_as_one_chunk():
    assert _split_text_for_tts("Hello there.", max_chars=180) == ["Hello there."]


def test_split_text_for_tts_splits_at_sentence_boundaries_not_mid_sentence():
    text = "First sentence here. Second sentence follows. Third one wraps it up nicely."
    chunks = _split_text_for_tts(text, max_chars=30)

    assert chunks == ["First sentence here.", "Second sentence follows.", "Third one wraps it up nicely."]
    # Rejoining every chunk must reproduce the original words exactly -- proves nothing was
    # dropped or duplicated across the split, not just that it "looks reasonable".
    assert " ".join(chunks) == text


def test_split_text_for_tts_keeps_an_overlong_single_sentence_whole():
    long_sentence = "This is one single very long sentence with no period in the middle at all so it cannot be split further"
    chunks = _split_text_for_tts(long_sentence, max_chars=30)

    assert chunks == [long_sentence]


def test_concatenate_wav_audio_combines_real_frame_counts():
    chunk_a = _wav_bytes(num_frames=100)
    chunk_b = _wav_bytes(num_frames=50)

    combined = _concatenate_wav_audio([chunk_a, chunk_b])

    with wave.open(io.BytesIO(combined), "rb") as reader:
        assert reader.getnframes() == 150


def test_synthesize_speech_long_short_text_makes_exactly_one_call():
    fake_response = Mock(audios=[base64.b64encode(_wav_bytes(10)).decode()])
    client = _client_with_fake_sdk(convert_return=fake_response)

    client.synthesize_speech_long("Hello there.", "en-IN")

    client._client.text_to_speech.convert.assert_called_once()


def test_synthesize_speech_long_splits_and_stitches_a_long_answer():
    # Long enough to need 2 chunks at the real _TTS_SAFE_CHUNK_CHARS budget -- mirrors the actual
    # live-reproduced case (an image caption + a follow-up question, ~300+ characters combined).
    text = (
        "I can see the photo you attached. It looks like a large pothole filled with loose "
        "gravel sitting in the middle of a paved residential road. "
        "Are you reporting a problem with Roads Potholes, or would you like information about it?"
    )
    client = _client_with_fake_sdk()
    client._client.text_to_speech.convert.side_effect = [
        Mock(audios=[base64.b64encode(_wav_bytes(200)).decode()]),
        Mock(audios=[base64.b64encode(_wav_bytes(80)).decode()]),
    ]

    audio_b64 = client.synthesize_speech_long(text, "en-IN")

    assert client._client.text_to_speech.convert.call_count == 2
    with wave.open(io.BytesIO(base64.b64decode(audio_b64)), "rb") as reader:
        # Real proof the two chunks were actually stitched together, not just that the second
        # (or first) call's audio alone was returned.
        assert reader.getnframes() == 280


def test_synthesize_speech_long_propagates_a_failed_chunk():
    client = _client_with_fake_sdk()
    client._client.text_to_speech.convert.side_effect = RuntimeError("network exploded")

    long_text = "First sentence. " * 30  # forces multiple chunks
    with pytest.raises(AIServiceError):
        client.synthesize_speech_long(long_text, "en-IN")
