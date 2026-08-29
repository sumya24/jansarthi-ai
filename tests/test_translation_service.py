"""Unit tests for TranslationService's language-code handling."""

from unittest.mock import Mock

import pytest

from backend.config import from_bcp47
from backend.services.translation_service import TranslationService


def test_to_english_uses_correct_bcp47_codes():
    """to_english should translate from the source BCP-47 code to English's."""
    fake_sarvam = Mock()
    fake_sarvam.translate.return_value = "Garbage has not been collected."
    service = TranslationService(sarvam_client=fake_sarvam)

    result = service.to_english("कचरा उचलला नाही", "mr")

    fake_sarvam.translate.assert_called_once_with(
        "कचरा उचलला नाही", source_language_code="mr-IN", target_language_code="en-IN"
    )
    assert result == "Garbage has not been collected."


def test_to_language_uses_correct_bcp47_codes():
    """to_language should translate from English's BCP-47 code to the target's."""
    fake_sarvam = Mock()
    fake_sarvam.translate.return_value = "कचरा उचलला नाही।"
    service = TranslationService(sarvam_client=fake_sarvam)

    result = service.to_language("Garbage has not been collected.", "hi")

    fake_sarvam.translate.assert_called_once_with(
        "Garbage has not been collected.", source_language_code="en-IN", target_language_code="hi-IN"
    )
    assert result == "कचरा उचलला नाही।"


@pytest.mark.parametrize(
    "code,bcp47",
    [("or", "od-IN"), ("gu", "gu-IN"), ("bn", "bn-IN")],
)
def test_to_language_supports_newly_added_indian_languages(code, bcp47):
    """Odia, Gujarati, and Bengali should map to the BCP-47 codes verified against Sarvam's docs."""
    fake_sarvam = Mock()
    fake_sarvam.translate.return_value = "translated"
    service = TranslationService(sarvam_client=fake_sarvam)

    service.to_language("Garbage has not been collected.", code)

    fake_sarvam.translate.assert_called_once_with(
        "Garbage has not been collected.", source_language_code="en-IN", target_language_code=bcp47
    )


def test_unsupported_language_raises_value_error():
    """An unsupported language code should raise ValueError before calling Sarvam."""
    fake_sarvam = Mock()
    service = TranslationService(sarvam_client=fake_sarvam)

    with pytest.raises(ValueError):
        service.to_english("some text", "fr")

    fake_sarvam.translate.assert_not_called()


# --- detect_language() -- backs the auto-detect-response-language fix (see
# orchestration/nodes.py's language_node docstring for the live-reported mismatch this closes) ---


def test_detect_language_maps_detected_bcp47_back_to_short_code():
    fake_sarvam = Mock()
    fake_sarvam.identify_language.return_value = "mr-IN"
    service = TranslationService(sarvam_client=fake_sarvam)

    assert service.detect_language("बेंगळुरूमध्ये पाणीपुरवठ्याबाबत तक्रार") == "mr"
    fake_sarvam.identify_language.assert_called_once_with("बेंगळुरूमध्ये पाणीपुरवठ्याबाबत तक्रार")


def test_detect_language_returns_none_when_detection_unavailable():
    """Never raises: a failed/unavailable detection call is a best-effort miss, not an error --
    the caller (language_node) falls back to the citizen's originally-selected language."""
    fake_sarvam = Mock()
    fake_sarvam.identify_language.return_value = None
    service = TranslationService(sarvam_client=fake_sarvam)

    assert service.detect_language("some text") is None


@pytest.mark.parametrize(
    "bcp47,expected",
    [("en-IN", "en"), ("mr-IN", "mr"), ("hi-IN", "hi"), ("od-IN", "or"), ("gu-IN", "gu"), ("bn-IN", "bn")],
)
def test_from_bcp47_is_the_exact_reverse_of_to_bcp47(bcp47, expected):
    assert from_bcp47(bcp47) == expected


def test_from_bcp47_returns_none_for_an_unsupported_code():
    assert from_bcp47("ta-IN") is None


def test_detect_language_returns_none_for_a_real_but_unsupported_language():
    """Sarvam correctly detects a real language (e.g. Tamil) this app has no
    SUPPORTED_LANGUAGES/TTS-voice coverage for -- must degrade to None (caller's fallback), never
    raise or silently pick an arbitrary supported language."""
    fake_sarvam = Mock()
    fake_sarvam.identify_language.return_value = "ta-IN"
    service = TranslationService(sarvam_client=fake_sarvam)

    assert service.detect_language("சென்னையில் தெருவிளக்கு புகார்") is None
