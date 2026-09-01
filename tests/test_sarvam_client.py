"""Tests for backend/services/sarvam_client.py's synthesize_speech() -- the new text-to-speech
method backing Ask Sarthi's voice assistant. Mocks the underlying SarvamAI SDK client, same
convention as ComplaintAgent's tests mocking SarvamClient itself -- never a real network call.

Also covers transcribe()/translate() (previously untested) and the retry/backoff behavior added
to all three of transcribe/synthesize_speech/translate -- see sarvam_client.py's own
_retry_sarvam_call docstring. Retry tests use a real (not mocked) tenacity wait -- the policy's
own worst case (3 attempts, exponential backoff capped at 2s) adds at most a couple of real
seconds per test, an acceptable trade-off for testing the actual configured behavior rather than
a stubbed-out approximation of it.
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


def _bare_client_with_fake_sdk() -> SarvamClient:
    """Same bypass-__init__ convention as _client_with_fake_sdk, without pre-wiring
    text_to_speech.convert -- for transcribe()/translate() tests, which call different SDK
    methods (speech_to_text.transcribe / text.translate)."""
    client = SarvamClient.__new__(SarvamClient)
    client._client = Mock()
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
    assert kwargs["model"] == "bulbul:v3"
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


def test_synthesize_speech_retries_a_transient_failure_then_succeeds():
    """A network blip on the first attempt must not fail the call outright -- the retry policy
    (see sarvam_client.py's _retry_sarvam_call) should give it a second try before this method's
    own except-block ever sees a failure."""
    fake_response = Mock(audios=["abc"])
    client = _client_with_fake_sdk()
    client._client.text_to_speech.convert.side_effect = [RuntimeError("transient network error"), fake_response]

    audio = client.synthesize_speech("Hello there.", "en-IN")

    assert audio == "abc"
    assert client._client.text_to_speech.convert.call_count == 2


def test_synthesize_speech_raises_ai_service_error_after_retries_exhausted():
    """A failure on every attempt must still surface as AIServiceError, not tenacity's own
    RetryError -- see _retry_sarvam_call's reraise=True."""
    client = _client_with_fake_sdk(convert_side_effect=RuntimeError("network exploded"))

    with pytest.raises(AIServiceError):
        client.synthesize_speech("Hello there.", "en-IN")

    # stop_after_attempt(3): the original call plus 2 retries, never more, never fewer.
    assert client._client.text_to_speech.convert.call_count == 3


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


# --- transcribe() -- previously had no test coverage in this file at all. ---


def test_transcribe_returns_the_transcript_from_the_sdk():
    client = _bare_client_with_fake_sdk()
    client._client.speech_to_text.transcribe.return_value = Mock(transcript="pothole on the main road")

    text = client.transcribe(b"fake-wav-bytes", "en-IN")

    assert text == "pothole on the main road"
    _, kwargs = client._client.speech_to_text.transcribe.call_args
    assert kwargs["language_code"] == "en-IN"
    assert kwargs["model"] == "saaras:v3"


def test_transcribe_requires_configured_client():
    client = SarvamClient.__new__(SarvamClient)
    client._client = None

    with pytest.raises(AIServiceError):
        client.transcribe(b"fake-wav-bytes", "en-IN")


def test_transcribe_raises_ai_service_error_on_sdk_failure():
    client = _bare_client_with_fake_sdk()
    client._client.speech_to_text.transcribe.side_effect = RuntimeError("network exploded")

    with pytest.raises(AIServiceError):
        client.transcribe(b"fake-wav-bytes", "en-IN")


def test_transcribe_retries_a_transient_failure_then_succeeds():
    client = _bare_client_with_fake_sdk()
    client._client.speech_to_text.transcribe.side_effect = [
        RuntimeError("transient network error"),
        Mock(transcript="pothole on the main road"),
    ]

    text = client.transcribe(b"fake-wav-bytes", "en-IN")

    assert text == "pothole on the main road"
    assert client._client.speech_to_text.transcribe.call_count == 2


def test_transcribe_raises_ai_service_error_after_retries_exhausted():
    client = _bare_client_with_fake_sdk()
    client._client.speech_to_text.transcribe.side_effect = RuntimeError("network exploded")

    with pytest.raises(AIServiceError):
        client.transcribe(b"fake-wav-bytes", "en-IN")

    assert client._client.speech_to_text.transcribe.call_count == 3


def test_transcribe_rewinds_the_audio_stream_before_each_retry_attempt():
    """A failed first attempt must not leave the BytesIO's read cursor consumed for the retry --
    see _call_transcribe's own seek(0) comment for the real bug this prevents (a silent
    empty/truncated re-upload on retry instead of an actual retry)."""
    client = _bare_client_with_fake_sdk()
    positions_seen_at_call_time = []

    def _fake_transcribe(file, **kwargs):
        positions_seen_at_call_time.append(file.tell())
        file.read()  # simulate the SDK consuming the stream while building the multipart upload
        if len(positions_seen_at_call_time) == 1:
            raise RuntimeError("transient network error")
        return Mock(transcript="ok")

    client._client.speech_to_text.transcribe.side_effect = _fake_transcribe

    client.transcribe(b"fake-wav-bytes", "en-IN")

    # Every attempt (including the retry) must have started reading from position 0.
    assert positions_seen_at_call_time == [0, 0]


# --- translate() -- previously had no test coverage in this file at all. ---


def test_translate_returns_the_translated_text_from_the_sdk():
    client = _bare_client_with_fake_sdk()
    client._client.text.translate.return_value = Mock(translated_text="सड़क पर गड्ढा")

    text = client.translate("pothole on the road", "en-IN", "hi-IN")

    assert text == "सड़क पर गड्ढा"
    _, kwargs = client._client.text.translate.call_args
    assert kwargs["source_language_code"] == "en-IN"
    assert kwargs["target_language_code"] == "hi-IN"
    assert kwargs["model"] == "sarvam-translate:v1"


def test_translate_returns_the_text_unchanged_when_source_and_target_match():
    """Short-circuits before ever calling the SDK -- see translate()'s own docstring."""
    client = _bare_client_with_fake_sdk()

    text = client.translate("no-op", "en-IN", "en-IN")

    assert text == "no-op"
    client._client.text.translate.assert_not_called()


def test_translate_uses_mayura_for_auto_source_detection():
    client = _bare_client_with_fake_sdk()
    client._client.text.translate.return_value = Mock(translated_text="ok")

    client.translate("some text", "auto", "hi-IN")

    _, kwargs = client._client.text.translate.call_args
    assert kwargs["model"] == "mayura:v1"


def test_translate_requires_configured_client():
    client = SarvamClient.__new__(SarvamClient)
    client._client = None

    with pytest.raises(AIServiceError):
        client.translate("some text", "en-IN", "hi-IN")


def test_translate_raises_ai_service_error_on_sdk_failure():
    client = _bare_client_with_fake_sdk()
    client._client.text.translate.side_effect = RuntimeError("network exploded")

    with pytest.raises(AIServiceError):
        client.translate("some text", "en-IN", "hi-IN")


def test_translate_retries_a_transient_failure_then_succeeds():
    client = _bare_client_with_fake_sdk()
    client._client.text.translate.side_effect = [
        RuntimeError("transient network error"),
        Mock(translated_text="सड़क पर गड्ढा"),
    ]

    text = client.translate("pothole on the road", "en-IN", "hi-IN")

    assert text == "सड़क पर गड्ढा"
    assert client._client.text.translate.call_count == 2


def test_translate_raises_ai_service_error_after_retries_exhausted():
    client = _bare_client_with_fake_sdk()
    client._client.text.translate.side_effect = RuntimeError("network exploded")

    with pytest.raises(AIServiceError):
        client.translate("some text", "en-IN", "hi-IN")

    assert client._client.text.translate.call_count == 3
