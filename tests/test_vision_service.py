"""Tests for backend/services/vision_service.py.

Never downloads/loads the real ~1.9B-param local model in tests, and never makes a real Gemini
API call either -- that would make the suite extremely slow, network-dependent, and quota-
consuming. Instead, `_model`/`_tokenizer` are set directly (bypassing `_get_model()`) or the
loading call is mocked, mirroring how ComplaintAgent's tests inject a fake SarvamClient instead of
hitting the real Sarvam API; Gemini's own HTTP call is mocked via `httpx.post`.

`_no_gemini` (autouse) clears `settings.GEMINI_API_KEY` for every test in this file by default --
most of these tests are specifically about the LOCAL fallback path, which only runs at all when
Gemini is unconfigured or fails; a real key present in `.env` during a test run would otherwise
make `describe_image()` skip straight to a real (or mocked-away) Gemini call instead of exercising
the local-model code these tests actually target. Tests that specifically cover the Gemini path
set the key back via `monkeypatch.setattr` themselves.
"""

from unittest.mock import Mock, patch

import pytest

from backend.services.vision_service import VisionService, VisionServiceError


@pytest.fixture(autouse=True)
def _no_gemini(monkeypatch):
    monkeypatch.setattr("backend.config.settings.GEMINI_API_KEY", "")

# A minimal, genuinely valid 1x1 JPEG (not just fake bytes with a jpeg-ish prefix) -- same fixture
# used by frontend-react/e2e/evidence-upload.spec.ts, so image decoding is proven against a real
# image, not a stub.
import base64

JPEG_1PX = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJ"
    "DRENDg8QEBEQCgwSExIQEw8QEBD/2wBDAQMDAwQDBAgEBAgQCwkLEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ"
    "EBAQEBAQEBAQEBAQEBAQEBAQEBAQEBD/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAU"
    "EAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMB"
    "AAIRAxEAPwCdABmX/9k="
)


def _service_with_fake_model(answer: str = "A pothole in the middle of a paved road.") -> VisionService:
    service = VisionService(model_name="fake/model")
    fake_model = Mock()
    fake_model.encode_image.return_value = "encoded"
    fake_model.answer_question.return_value = answer
    service._model = fake_model
    service._tokenizer = Mock()
    return service


def test_describe_image_returns_caption_from_model():
    service = _service_with_fake_model("A large pothole in the road, partially filled with water.")

    caption = service.describe_image(JPEG_1PX)

    assert caption == "A large pothole in the road, partially filled with water."


def test_describe_image_strips_whitespace():
    service = _service_with_fake_model("  A broken streetlight.  \n")

    assert service.describe_image(JPEG_1PX) == "A broken streetlight."


def test_describe_image_invalid_bytes_raises_vision_service_error():
    service = _service_with_fake_model()

    with pytest.raises(VisionServiceError):
        service.describe_image(b"not a real image")


def test_describe_image_model_failure_raises_vision_service_error():
    service = VisionService(model_name="fake/model")
    fake_model = Mock()
    fake_model.encode_image.side_effect = RuntimeError("model exploded")
    service._model = fake_model
    service._tokenizer = Mock()

    with pytest.raises(VisionServiceError):
        service.describe_image(JPEG_1PX)


def test_get_model_wraps_load_failure_in_vision_service_error():
    service = VisionService(model_name="fake/model")

    with patch("transformers.AutoModelForCausalLM.from_pretrained", side_effect=OSError("no such model")):
        with pytest.raises(VisionServiceError):
            service.load()

    assert service._model is None


def test_model_name_defaults_from_settings():
    service = VisionService()

    assert service.model_name  # non-empty, pulled from backend.config.settings.VISION_MODEL_NAME


def _fake_gemini_response(caption: str = "A pothole caption from Gemini.", token_count: int = 12) -> Mock:
    response = Mock()
    response.raise_for_status = Mock()
    response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": caption}]}}],
        "usageMetadata": {"candidatesTokenCount": token_count},
    }
    return response


def test_describe_image_prefers_gemini_when_configured(monkeypatch):
    monkeypatch.setattr("backend.config.settings.GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr("backend.config.settings.GEMINI_VISION_MODEL", "gemini-3.5-flash-lite")
    service = _service_with_fake_model("SHOULD NOT BE USED -- local fallback")

    with patch("httpx.post", return_value=_fake_gemini_response("A pothole, per Gemini.")) as fake_post:
        caption = service.describe_image(JPEG_1PX)

    assert caption == "A pothole, per Gemini."
    fake_post.assert_called_once()
    # Real model check, not just "didn't crash" -- the local fallback must never even be touched
    # when Gemini already produced a usable caption.
    service._model.encode_image.assert_not_called()
    assert service.model_name == "gemini-3.5-flash-lite"
    assert service.count_tokens(caption) == 12


def test_describe_image_falls_back_to_local_model_when_gemini_fails(monkeypatch):
    monkeypatch.setattr("backend.config.settings.GEMINI_API_KEY", "fake-key")
    service = _service_with_fake_model("A broken streetlight, per the local model.")

    with patch("httpx.post", side_effect=TimeoutError("Gemini took too long")):
        caption = service.describe_image(JPEG_1PX)

    assert caption == "A broken streetlight, per the local model."
    service._model.encode_image.assert_called_once()
    assert service.model_name == "fake/model"


def test_describe_image_local_model_timeout_raises_vision_service_error_promptly(monkeypatch):
    """LIVE-REPORTED: confirmed directly against production -- a real citizen photo left a
    request stuck on "Thinking..." for over 15 minutes with no way out, because the local
    model's own inference call had nothing capping how long it could run (unlike Gemini's own
    httpx `timeout=`). describe_image() must now give up and raise VisionServiceError once
    settings.VISION_LOCAL_MODEL_TIMEOUT_SECONDS elapses, not hang indefinitely -- verified here
    with a real, measured wall-clock bound (a slow fake model that would otherwise sleep for far
    longer than the configured timeout), not just that the call eventually returns."""
    import time

    monkeypatch.setattr("backend.config.settings.VISION_LOCAL_MODEL_TIMEOUT_SECONDS", 0.2)
    service = VisionService(model_name="fake/model")
    fake_model = Mock()
    fake_model.encode_image.return_value = "encoded"

    def _slow_answer_question(*_args, **_kwargs):
        time.sleep(5)  # far longer than the 0.2s timeout above
        return "too slow to matter"

    fake_model.answer_question.side_effect = _slow_answer_question
    service._model = fake_model
    service._tokenizer = Mock()

    started = time.monotonic()
    with pytest.raises(VisionServiceError):
        service.describe_image(JPEG_1PX)
    elapsed = time.monotonic() - started

    # The real point of this fix: elapsed must be close to the configured timeout (0.2s), not the
    # fake model's full 5s sleep -- a generous margin (2s) for CI/scheduling jitter, still nowhere
    # near the 5s it would take if the timeout weren't actually enforced.
    assert elapsed < 2, f"describe_image() waited {elapsed:.2f}s -- the local-model timeout was not enforced"


def test_describe_image_skips_gemini_entirely_when_unconfigured():
    # `_no_gemini` (autouse) already clears the key -- this just makes that assumption explicit
    # and confirms httpx is never even touched, not just that the local caption comes back right.
    service = _service_with_fake_model("A pothole in the middle of a paved road.")

    with patch("httpx.post") as fake_post:
        caption = service.describe_image(JPEG_1PX)

    fake_post.assert_not_called()
    assert caption == "A pothole in the middle of a paved road."
