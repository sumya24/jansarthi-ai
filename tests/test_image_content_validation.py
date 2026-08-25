"""Security tests for real image-content validation (see backend/services/evidence_service.py's
module docstring for the design). Covers both upload paths that funnel through
evidence_service.validate_and_write(): complaint evidence (POST /complaints, .../updates,
.../resolve) and Ask Sarthi image analysis (POST /ask-janmitra/image).

Uses tests/image_fixtures.py for real, genuinely-decodable image bytes -- placeholder bytes with
just a correct magic-byte prefix (this suite's older convention, before this hardening) are no
longer valid uploads on purpose; see that module's own docstring.
"""

from unittest.mock import Mock

from backend.config import settings
from backend.services.ask_janmitra_service import AskJanMitraService
import backend.routes.ask_janmitra as ask_janmitra_module
import backend.routes.complaints as complaints_module
from backend.models import Complaint
from tests.image_fixtures import (
    CORRUPTED_PNG_BYTES,
    FAKE_EXECUTABLE_BYTES,
    RANDOM_BINARY_BYTES,
    TEXT_FILE_BYTES,
    VALID_GIF_BYTES,
    VALID_JPEG_BYTES,
    VALID_PNG_BYTES,
)
from tests.test_ask_janmitra_image import _FakeComplaintAgent, _get_shared_chroma_deps


def _fake_agent_create_complaint(db, citizen_id, language_code, text, audio_chunks, photo_path, category=None):
    complaint = Complaint(
        citizen_id=citizen_id, original_text=text or "", original_language=language_code,
        translated_text=text or "", summary=(text or "")[:80], photo_path=photo_path, status="pending",
        service_category=category.value if category else None,
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    return complaint


def _upload(client, token, filename: str, content: bytes, content_type: str):
    return client.post(
        "/complaints", headers={"Authorization": f"Bearer {token}"},
        data={"language": "en", "text": "Evidence content-validation test"},
        files=[("photos", (filename, content, content_type))],
    )


# --- 1/2/3: valid uploads accepted -------------------------------------------------------------


def test_valid_jpeg_is_accepted(client, monkeypatch, make_citizen):
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    token, _ = make_citizen(phone="9200000001")
    response = _upload(client, token, "photo.jpg", VALID_JPEG_BYTES, "image/jpeg")
    assert response.status_code == 200, response.text


def test_valid_png_is_accepted(client, monkeypatch, make_citizen):
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    token, _ = make_citizen(phone="9200000002")
    response = _upload(client, token, "photo.png", VALID_PNG_BYTES, "image/png")
    assert response.status_code == 200, response.text


def test_every_currently_supported_format_is_accepted():
    """Documents, from the real config, exactly which formats this assertion set covers -- if
    ALLOWED_PHOTO_CONTENT_TYPES ever changes, this fails loudly instead of silently under-testing
    a newly-added format. Only JPEG and PNG are supported today (two content-type spellings for
    JPEG: "image/jpeg" and the non-standard "image/jpg")."""
    assert set(settings.ALLOWED_PHOTO_CONTENT_TYPES) == {"image/jpeg", "image/png", "image/jpg"}
    # test_valid_jpeg_is_accepted / test_valid_png_is_accepted above are that real coverage.


# --- 4/5/6/7/8: invalid content rejected, regardless of claimed Content-Type/filename -----------


def test_random_bytes_with_image_content_type_is_rejected(client, monkeypatch, make_citizen):
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    token, _ = make_citizen(phone="9200000003")
    response = _upload(client, token, "photo.jpg", RANDOM_BINARY_BYTES, "image/jpeg")
    assert response.status_code == 400
    assert "not a valid image" in response.json()["detail"].lower()


def test_text_file_renamed_to_jpg_is_rejected(client, monkeypatch, make_citizen):
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    token, _ = make_citizen(phone="9200000004")
    response = _upload(client, token, "photo.jpg", TEXT_FILE_BYTES, "image/jpeg")
    assert response.status_code == 400
    assert "not a valid image" in response.json()["detail"].lower()


def test_executable_renamed_to_jpg_is_rejected(client, monkeypatch, make_citizen):
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    token, _ = make_citizen(phone="9200000005")
    response = _upload(client, token, "photo.jpg", FAKE_EXECUTABLE_BYTES, "image/jpeg")
    assert response.status_code == 400
    assert "not a valid image" in response.json()["detail"].lower()


def test_corrupted_image_is_rejected(client, monkeypatch, make_citizen):
    """A real PNG signature + IHDR declaration, but garbage instead of real chunk data -- looks
    right at a glance, isn't. Proves the check decodes, not just sniffs a magic-byte prefix."""
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    token, _ = make_citizen(phone="9200000006")
    response = _upload(client, token, "photo.png", CORRUPTED_PNG_BYTES, "image/png")
    assert response.status_code == 400
    assert "not a valid image" in response.json()["detail"].lower()


def test_empty_file_is_rejected(client, monkeypatch, make_citizen):
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    token, _ = make_citizen(phone="9200000007")
    response = _upload(client, token, "photo.jpg", b"", "image/jpeg")
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_unsupported_but_genuinely_valid_image_format_is_rejected(client, monkeypatch, make_citizen):
    """A REAL, fully-decodable image (PIL opens and reads it fine) in a format this app simply
    doesn't support -- proves the format allow-list is enforced against the actual decoded
    format, not just "did it fail to decode at all". Content-Type is spoofed as image/jpeg
    (otherwise it would be rejected at the earlier, shallower Content-Type gate instead of
    proving THIS specific check)."""
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    token, _ = make_citizen(phone="9200000008")
    response = _upload(client, token, "photo.jpg", VALID_GIF_BYTES, "image/jpeg")
    assert response.status_code == 400
    assert "unsupported image format" in response.json()["detail"].lower()
    assert "gif" in response.json()["detail"].lower()


# --- 10/11: mismatch handling -- intended, documented, tested behavior --------------------------


def test_wrong_content_type_with_an_otherwise_valid_image_is_rejected(client, monkeypatch, make_citizen):
    """Intended behavior (documented in evidence_service.py's module docstring): Content-Type is
    checked FIRST as a cheap filter and is NOT bypassed just because the actual bytes turn out to
    be fine -- a client claiming a Content-Type outside the two allowed strings is rejected before
    the real decode check ever runs, exactly as it was before this hardening (this hardening adds
    a new, stronger check on top; it does not relax the existing one)."""
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    token, _ = make_citizen(phone="9200000009")
    response = _upload(client, token, "photo.jpg", VALID_JPEG_BYTES, "application/octet-stream")
    assert response.status_code == 400
    assert "unsupported photo type" in response.json()["detail"].lower()


def test_filename_extension_mismatch_with_valid_content_type_and_bytes_is_accepted(client, monkeypatch, make_citizen):
    """Intended behavior: the filename is a display label only, never a security check in this
    design (the pre-existing code never validated it either) -- a correct Content-Type plus
    genuinely valid, matching bytes is accepted regardless of what the filename claims."""
    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint))
    token, _ = make_citizen(phone="9200000010")
    response = _upload(client, token, "photo.png", VALID_JPEG_BYTES, "image/jpeg")
    assert response.status_code == 200, response.text


# --- 12: invalid files never reach Moondream2 ---------------------------------------------------


def _install_service_with_vision_spy(monkeypatch):
    """Same shape as test_ask_janmitra_image.py's _install_real_service, but returns the vision
    mock itself (that file only returns the answer-generation mock) so a test here can assert
    describe_image was never called for a rejected upload."""
    store, provider = _get_shared_chroma_deps()
    fake_answers = Mock()
    fake_answers.generate = Mock(side_effect=lambda q, chunks, lang, context_labels=None: (q, False))
    fake_vision = Mock()
    fake_vision.describe_image = Mock(return_value="A caption.")
    service = AskJanMitraService(
        vector_store=store, embedding_provider=provider, answer_service=fake_answers,
        complaint_agent=_FakeComplaintAgent(), vision_service=fake_vision,
    )
    monkeypatch.setattr(ask_janmitra_module, "_service", service)
    return fake_vision


def test_invalid_image_never_reaches_moondream2_via_ask_janmitra(client, monkeypatch, make_citizen):
    fake_vision = _install_service_with_vision_spy(monkeypatch)
    token, _ = make_citizen(phone="9200000011")

    response = client.post(
        "/ask-janmitra/image",
        headers={"Authorization": f"Bearer {token}"},
        data={"question": "What is this?", "language": "en"},
        files=[("image", ("photo.jpg", TEXT_FILE_BYTES, "image/jpeg"))],
    )

    assert response.status_code == 400
    fake_vision.describe_image.assert_not_called()


def test_valid_image_does_reach_moondream2_via_ask_janmitra(client, monkeypatch, make_citizen):
    """Sanity control for the test above -- proves describe_image genuinely gets called for a
    real, valid image, so "never called" for the invalid case above is a meaningful assertion and
    not just a mock that's never wired up correctly."""
    fake_vision = _install_service_with_vision_spy(monkeypatch)
    token, _ = make_citizen(phone="9200000012")

    response = client.post(
        "/ask-janmitra/image",
        headers={"Authorization": f"Bearer {token}"},
        data={"question": "What is this?", "language": "en"},
        files=[("image", ("photo.jpg", VALID_JPEG_BYTES, "image/jpeg"))],
    )

    assert response.status_code == 200, response.text
    fake_vision.describe_image.assert_called_once()
