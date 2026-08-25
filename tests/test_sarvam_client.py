"""Tests for backend/services/sarvam_client.py's synthesize_speech() -- the new text-to-speech
method backing Ask Sarthi's voice assistant. Mocks the underlying SarvamAI SDK client, same
convention as ComplaintAgent's tests mocking SarvamClient itself -- never a real network call.
"""

from unittest.mock import Mock

import pytest

from backend.services.sarvam_client import AIServiceError, SarvamClient


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
