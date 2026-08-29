"""Security/authorization regression for the multimodal Ask Sarthi endpoints (Phase 8).

Verifies the new `/ask-janmitra/image` and `/ask-janmitra/voice` endpoints do not introduce any
new authorization bypass relative to the existing, already-tested `/ask-janmitra` (text) endpoint
and the existing complaint/evidence system:

- Both require authentication (already covered in test_ask_janmitra_image.py/
  test_ask_janmitra_voice.py; not repeated here).
- A citizen still cannot see another citizen's complaint status through the image/voice
  endpoints -- the same `status_flow_node` ownership check the text endpoint already relies on,
  now proven to apply through both new entry points too, not just assumed because they share a
  graph.
- A complaint created via the image/voice endpoints is stamped with the AUTHENTICATED citizen's
  own id -- there is no client-suppliable `citizen_id` field on either endpoint at all, so this
  also proves no such spoofing surface exists.
- `ComplaintEvidence.uploaded_by` for an image attached via these endpoints matches the real
  authenticated citizen, never a client-supplied value.
"""

import json
from pathlib import Path
from unittest.mock import Mock

import backend.routes.ask_janmitra as ask_janmitra_module
from backend.config import settings
from backend.models import Complaint, ComplaintEvidence
from backend.services.ask_janmitra_service import AskJanMitraService
from backend.services.embedding_provider import SentenceTransformerEmbeddingProvider
from backend.services.vector_store import ChromaVectorStore
from tests.image_fixtures import VALID_JPEG_BYTES as _JPEG_BYTES

_FAKE_CAPTION = "A large pothole in the middle of a paved road."

_shared_store: ChromaVectorStore | None = None
_shared_provider: SentenceTransformerEmbeddingProvider | None = None


def _get_shared_chroma_deps():
    global _shared_store, _shared_provider
    if _shared_provider is None:
        _shared_provider = SentenceTransformerEmbeddingProvider()
        _shared_provider.load()
    if _shared_store is None:
        _shared_store = ChromaVectorStore(settings.CHROMA_PERSIST_DIR, settings.CHROMA_COLLECTION_NAME)
        _shared_store.load()
    return _shared_store, _shared_provider


class _FakeComplaintAgent:
    def create_complaint(self, db, citizen_id, language_code, text, audio_chunks, photo_path, category=None):
        complaint = Complaint(
            citizen_id=citizen_id,
            original_text=text or "",
            original_language=language_code,
            translated_text=text or "",
            summary=(text or "")[:80],
            photo_path=photo_path,
            status="open",
            service_category=category.value if category else None,
        )
        db.add(complaint)
        db.commit()
        db.refresh(complaint)
        return complaint


def _install_real_service(monkeypatch):
    store, provider = _get_shared_chroma_deps()
    fake_answers = Mock()
    fake_answers.generate = Mock(side_effect=lambda q, chunks, lang, context_labels=None: (q, False, None))
    fake_sarvam = Mock()
    fake_sarvam.transcribe = Mock(return_value="Street light near my home is not working.")
    fake_sarvam.synthesize_speech_long = Mock(return_value="ZmFrZQ==")
    fake_vision = Mock()
    fake_vision.describe_image = Mock(return_value=_FAKE_CAPTION)

    service = AskJanMitraService(
        vector_store=store,
        embedding_provider=provider,
        answer_service=fake_answers,
        complaint_agent=_FakeComplaintAgent(),
        sarvam_client=fake_sarvam,
        vision_service=fake_vision,
    )
    monkeypatch.setattr(ask_janmitra_module, "_service", service)
    return fake_sarvam


def _ask_image(client, token, question, **kwargs):
    data = {"question": question, "language": "en", **kwargs}
    return client.post(
        "/ask-janmitra/image", headers={"Authorization": f"Bearer {token}"},
        data=data, files=[("image", ("photo.jpg", _JPEG_BYTES, "image/jpeg"))],
    )


def _ask_voice(client, token, **kwargs):
    data = {"language": "en", **kwargs}
    return client.post(
        "/ask-janmitra/voice", headers={"Authorization": f"Bearer {token}"},
        data=data, files=[("audio", ("seg0.wav", b"chunk1", "audio/wav"))],
    )


def _ask_voice_with_image(client, token, **kwargs):
    data = {"language": "en", **kwargs}
    return client.post(
        "/ask-janmitra/voice", headers={"Authorization": f"Bearer {token}"},
        data=data,
        files=[("audio", ("seg0.wav", b"chunk1", "audio/wav")), ("image", ("photo.jpg", _JPEG_BYTES, "image/jpeg"))],
    )


# ---------------------------------------------------------------------------
# TYPE_C ownership: a citizen cannot see another citizen's complaint status
# through either new multimodal endpoint (mirrors test_ask_janmitra.py's
# test_type_c_cannot_see_another_citizens_complaint for the text endpoint).
# ---------------------------------------------------------------------------


def _seed_complaint_for(db_session, owner_id: str) -> int:
    db = db_session()
    complaint = Complaint(
        citizen_id=owner_id, original_text="x", original_language="en",
        translated_text="x", summary="x", status="pending",
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    complaint_id = complaint.id
    db.close()
    return complaint_id


def test_image_endpoint_cannot_see_another_citizens_complaint_status(client, monkeypatch, db_session, make_citizen):
    _install_real_service(monkeypatch)
    _, owner = make_citizen(phone="9000000301")
    other_token, _ = make_citizen(phone="9000000302")
    complaint_id = _seed_complaint_for(db_session, str(owner["id"]))

    response = _ask_image(client, other_token, f"What is the status of complaint #{complaint_id}?")

    assert response.status_code == 200, response.text
    assert "couldn't find" in response.json()["answer"].lower()


def test_voice_endpoint_cannot_see_another_citizens_complaint_status(client, monkeypatch, db_session, make_citizen):
    _install_real_service(monkeypatch)
    _, owner = make_citizen(phone="9000000303")
    other_token, _ = make_citizen(phone="9000000304")
    complaint_id = _seed_complaint_for(db_session, str(owner["id"]))

    monkeypatch.setattr(
        ask_janmitra_module._service._sarvam_client, "transcribe",
        Mock(return_value=f"What is the status of complaint #{complaint_id}?"),
    )
    response = _ask_voice(client, other_token)

    assert response.status_code == 200, response.text
    assert "couldn't find" in response.json()["answer"].lower()


def test_image_endpoint_owner_can_see_their_own_complaint_status(client, monkeypatch, db_session, make_citizen):
    """Negative-control counterpart to the two tests above -- proves the ownership check is a
    real boundary (blocks strangers) and not just a blanket "always says not found" bug."""
    _install_real_service(monkeypatch)
    token, owner = make_citizen(phone="9000000305")
    complaint_id = _seed_complaint_for(db_session, str(owner["id"]))

    response = _ask_image(client, token, f"What is the status of complaint #{complaint_id}?")

    assert response.status_code == 200, response.text
    assert "couldn't find" not in response.json()["answer"].lower()


# ---------------------------------------------------------------------------
# Complaint/evidence ownership stamping: always the authenticated citizen, never
# spoofable (neither endpoint exposes a client-suppliable citizen_id field at all).
# ---------------------------------------------------------------------------


def test_image_complaint_is_stamped_with_the_authenticated_citizens_own_id(client, monkeypatch, db_session, make_citizen, make_worker):
    _install_real_service(monkeypatch)
    make_worker(phone="9000099306", ward="Mohali")
    token, citizen = make_citizen(phone="9000000306")

    response = _ask_image(client, token, "Street light near my home is not working.", location_text="Mohali")
    assert response.status_code == 200, response.text
    body = response.json()
    # P0 SAFETY FIX (production-safety audit): confirmation required before creation -- see
    # tests/test_ask_janmitra.py's test_type_a_complaint_creates_and_assigns_complaint.
    assert body.get("complaint_id") is None

    history = json.dumps([
        {"role": "user", "content": "Street light near my home is not working."},
        {"role": "assistant", "content": body["answer"]},
    ])
    confirm = _ask_image(client, token, "Yes, submit it.", conversation_history=history)
    assert confirm.status_code == 200, confirm.text
    complaint_id = confirm.json()["complaint_id"]
    assert complaint_id is not None

    db = db_session()
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).one()
    assert complaint.citizen_id == str(citizen["id"])

    evidence = db.query(ComplaintEvidence).filter(ComplaintEvidence.complaint_id == complaint_id).all()
    assert len(evidence) == 1
    assert evidence[0].uploaded_by == citizen["id"]
    assert evidence[0].uploader_role == "citizen"
    on_disk = Path(settings.UPLOAD_FOLDER) / complaint.photo_path
    assert on_disk.is_file()
    db.close()


def test_voice_complaint_is_stamped_with_the_authenticated_citizens_own_id(client, monkeypatch, db_session, make_citizen, make_worker):
    fake_sarvam = _install_real_service(monkeypatch)
    make_worker(phone="9000099307", ward="Mohali")
    token, citizen = make_citizen(phone="9000000307")

    response = _ask_voice_with_image(client, token, location_text="Mohali")
    assert response.status_code == 200, response.text
    body = response.json()
    # P0 SAFETY FIX (production-safety audit): confirmation required before creation -- see
    # tests/test_ask_janmitra.py's test_type_a_complaint_creates_and_assigns_complaint. The fixed
    # `transcribe` mock always returns the same phrase, so the confirmation reply is sent via a
    # second transcript override rather than real distinguishable audio.
    assert body.get("complaint_id") is None

    fake_sarvam.transcribe = Mock(return_value="Yes, submit it.")
    history = json.dumps([
        {"role": "user", "content": "Street light near my home is not working."},
        {"role": "assistant", "content": body["answer"]},
    ])
    confirm = _ask_voice_with_image(client, token, conversation_history=history)
    assert confirm.status_code == 200, confirm.text
    complaint_id = confirm.json()["complaint_id"]
    assert complaint_id is not None

    db = db_session()
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).one()
    assert complaint.citizen_id == str(citizen["id"])

    evidence = db.query(ComplaintEvidence).filter(ComplaintEvidence.complaint_id == complaint_id).all()
    assert len(evidence) == 1
    assert evidence[0].uploaded_by == citizen["id"]
    assert evidence[0].uploader_role == "citizen"
    db.close()


def test_image_endpoint_has_no_client_suppliable_citizen_id_field(client, monkeypatch, db_session, make_citizen, make_worker):
    """An attempt to smuggle a `citizen_id` form field through the image endpoint must have zero
    effect -- the complaint is still stamped with the real authenticated citizen, proving the
    field (if sent) is simply ignored, not read from anywhere in the request."""
    _install_real_service(monkeypatch)
    make_worker(phone="9000099308", ward="Mohali")
    token, real_citizen = make_citizen(phone="9000000308")
    _, victim = make_citizen(phone="9000000309")

    response = _ask_image(
        client, token, "Street light near my home is not working.",
        location_text="Mohali", citizen_id=str(victim["id"]),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # P0 SAFETY FIX (production-safety audit): confirmation required before creation.
    assert body.get("complaint_id") is None

    history = json.dumps([
        {"role": "user", "content": "Street light near my home is not working."},
        {"role": "assistant", "content": body["answer"]},
    ])
    confirm = _ask_image(client, token, "Yes, submit it.", conversation_history=history, citizen_id=str(victim["id"]))
    assert confirm.status_code == 200, confirm.text
    complaint_id = confirm.json()["complaint_id"]
    db = db_session()
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).one()
    assert complaint.citizen_id == str(real_citizen["id"])
    assert complaint.citizen_id != str(victim["id"])
    db.close()
