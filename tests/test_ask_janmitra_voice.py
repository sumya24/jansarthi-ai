"""Tests for POST /ask-janmitra/voice (Ask JanMitra voice-to-voice assistant, phases 5 and 7).

Real ChromaDB retrieval + real graph routing, with the LLM answer-generation, complaint-agent,
vision, and Sarvam STT/TTS calls all swapped for deterministic fakes -- same no-real-network-call
convention as test_ask_janmitra.py/test_ask_janmitra_image.py. STT is faked at the SarvamClient
level (not the whole stt_service module) so the real chunk-stitching/retry logic in
backend/services/stt_service.py is still genuinely exercised.

Phase 7 (combined voice+image turns): the `image` field on this endpoint was already threaded
through in phase 5 (ask_voice() reuses the exact same `_process_image()` helper ask_with_image()
uses) -- the tests at the bottom of this file are the phase-7 verification that it actually works
end to end, including the "image with no spoken text" clarification path.
"""

import json
from unittest.mock import Mock

import backend.routes.ask_janmitra as ask_janmitra_module
import backend.services.observability.tracing as tracing_module
from backend.config import settings
from backend.models import Complaint
from backend.services.ask_janmitra_service import AskJanMitraService
from backend.services.embedding_provider import SentenceTransformerEmbeddingProvider
from backend.services.sarvam_client import AIServiceError
from backend.services.vector_store import ChromaVectorStore
from backend.services.vision_service import VisionServiceError
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


def _install_real_service(
    monkeypatch, *, transcript="Who do I contact about road potholes in Nagpur?",
    stt_error=False, tts_error=False, caption=_FAKE_CAPTION, caption_error=False,
):
    store, provider = _get_shared_chroma_deps()
    fake_answers = Mock()
    fake_answers.generate = Mock(side_effect=lambda q, chunks, lang, context_labels=None: (q, False, None))

    fake_sarvam = Mock()
    if stt_error:
        fake_sarvam.transcribe = Mock(side_effect=AIServiceError("STT down"))
    else:
        fake_sarvam.transcribe = Mock(return_value=transcript)
    if tts_error:
        fake_sarvam.synthesize_speech_long = Mock(side_effect=AIServiceError("TTS down"))
    else:
        fake_sarvam.synthesize_speech_long = Mock(return_value="ZmFrZS1hdWRpby1ieXRlcw==")
    # Identity passthrough -- this fake never needed real translation semantics before (nothing
    # asserted on the translated text's content), but it must still return a real string: an
    # unstubbed Mock() attribute returns a Mock object, and _localize() (orchestration/nodes.py)
    # now genuinely reads len() off this value for its own Phoenix cost-tracing span.
    fake_sarvam.translate = Mock(side_effect=lambda text, source_language_code, target_language_code: text)

    fake_vision = Mock()
    if caption_error:
        fake_vision.describe_image = Mock(side_effect=VisionServiceError("model unavailable"))
    else:
        fake_vision.describe_image = Mock(return_value=caption)

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


def _ask_voice(client, token, audio_bytes_list=(b"chunk1",), image_bytes=None, **kwargs):
    data = {"language": "en", **kwargs}
    files = [("audio", (f"seg{i}.wav", b, "audio/wav")) for i, b in enumerate(audio_bytes_list)]
    if image_bytes is not None:
        files.append(("image", ("photo.jpg", image_bytes, "image/jpeg")))
    return client.post("/ask-janmitra/voice", headers={"Authorization": f"Bearer {token}"}, data=data, files=files)


def test_voice_returns_real_transcribed_text_and_real_audio(client, monkeypatch, make_citizen):
    fake_sarvam = _install_real_service(monkeypatch, transcript="Who do I contact about road potholes in Nagpur?")
    token, _ = make_citizen(phone="9000000201")

    response = _ask_voice(client, token)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["question"] == "Who do I contact about road potholes in Nagpur?"
    assert "Who do I contact about road potholes in Nagpur?" in body["answer"]
    assert body["audio_base64"] == "ZmFrZS1hdWRpby1ieXRlcw=="
    assert body["audio_format"] == "wav"
    fake_sarvam.transcribe.assert_called_once()
    fake_sarvam.synthesize_speech_long.assert_called_once()
    tts_call_text = fake_sarvam.synthesize_speech_long.call_args[0][0]
    assert tts_call_text == body["answer"]


def test_voice_answer_and_speech_follow_the_actual_spoken_language_not_the_request_field(
    client, monkeypatch, make_citizen
):
    """Voice equivalent of the text-mode auto-detect-response-language fix (see
    orchestration/nodes.py's language_node docstring for the live-reported mismatch): the
    citizen's `language` form field (their UI toggle) says "en", but they actually SPOKE Marathi
    -- both the answer TEXT and the synthesized SPEECH must follow what they actually said, not
    the stale toggle. Two distinct mechanisms verified together: STT itself must decode with
    Sarvam's own auto-detect ("unknown"), never a language forced from the request field, and the
    resulting transcript is then run through the same text-based detection every text turn uses
    to pick the final response_language."""
    marathi_transcript = "बेंगळुरूमध्ये पाणीपुरवठ्याबाबत तक्रार करण्याची प्रक्रिया काय आहे?"
    fake_sarvam = _install_real_service(monkeypatch, transcript=marathi_transcript)
    fake_sarvam.identify_language = Mock(return_value="mr-IN")
    token, _ = make_citizen(phone="9000000210")

    response = _ask_voice(client, token, language="en")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["question"] == marathi_transcript
    assert body["language"] == "mr"

    fake_sarvam.transcribe.assert_called_once()
    stt_call_language = fake_sarvam.transcribe.call_args[0][1]
    assert stt_call_language == "unknown"

    fake_sarvam.synthesize_speech_long.assert_called_once()
    tts_call_language = fake_sarvam.synthesize_speech_long.call_args[0][1]
    assert tts_call_language == "mr-IN"


def test_voice_stitches_multiple_audio_segments_in_order(client, monkeypatch, make_citizen):
    """Each segment is transcribed independently and joined -- same chunked-STT pipeline
    ComplaintAgent's voice complaints already use (backend/services/stt_service.py)."""
    fake_sarvam = _install_real_service(monkeypatch)
    fake_sarvam.transcribe = Mock(side_effect=["Who do I contact", "about road potholes in Nagpur?"])
    token, _ = make_citizen(phone="9000000202")

    response = _ask_voice(client, token, audio_bytes_list=(b"chunk1", b"chunk2"))

    assert response.status_code == 200, response.text
    assert fake_sarvam.transcribe.call_count == 2
    assert response.json()["question"] == "Who do I contact about road potholes in Nagpur?"


def test_voice_tts_failure_still_returns_the_real_text_answer(client, monkeypatch, make_citizen):
    """TTS is best-effort -- a synthesis failure must not fail the whole turn, and must never
    fake/placeholder audio_base64; it's honestly None."""
    _install_real_service(monkeypatch, tts_error=True)
    token, _ = make_citizen(phone="9000000203")

    response = _ask_voice(client, token)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["audio_base64"] is None
    assert body["answer"]  # real text answer still present


def test_voice_stt_failure_is_a_real_error_not_a_fabricated_answer(client, monkeypatch, make_citizen):
    """Unlike TTS, STT failure is NOT best-effort -- there's no text to answer at all, so this
    must be a real error, never a fabricated/empty answer."""
    _install_real_service(monkeypatch, stt_error=True)
    token, _ = make_citizen(phone="9000000204")

    response = _ask_voice(client, token)

    assert response.status_code == 503


def test_voice_requires_at_least_one_audio_segment(client, monkeypatch, make_citizen):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9000000205")

    response = client.post(
        "/ask-janmitra/voice", headers={"Authorization": f"Bearer {token}"},
        data={"language": "en"}, files=[],
    )

    assert response.status_code in (400, 422)


def test_voice_rejects_unsupported_language(client, monkeypatch, make_citizen):
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9000000206")

    response = _ask_voice(client, token, language="fr")

    assert response.status_code == 400


def test_voice_requires_authentication(client, monkeypatch):
    _install_real_service(monkeypatch)

    response = client.post(
        "/ask-janmitra/voice",
        data={"language": "en"},
        files=[("audio", ("seg0.wav", b"chunk1", "audio/wav"))],
    )

    assert response.status_code in (401, 403)


def test_voice_can_file_a_real_complaint_from_the_transcribed_text(client, monkeypatch, make_citizen, db_session, make_worker):
    """P0 SAFETY FIX (production-safety audit): category + location resolving together no longer
    creates a complaint on the first call -- see tests/test_ask_janmitra.py's
    test_type_a_complaint_creates_and_assigns_complaint for the full rationale."""
    fake_sarvam = _install_real_service(monkeypatch, transcript="Street light near my home is not working.")
    make_worker(phone="9000099207", ward="Mohali")
    token, _ = make_citizen(phone="9000000207")

    response = _ask_voice(client, token, location_text="Mohali")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["routed_to"] == "NONE_AWAITING_CONFIRMATION"
    assert body.get("complaint_id") is None

    fake_sarvam.transcribe = Mock(return_value="Yes, submit it.")
    history = json.dumps([
        {"role": "user", "content": "Street light near my home is not working."},
        {"role": "assistant", "content": body["answer"]},
    ])
    confirm = _ask_voice(client, token, conversation_history=history)
    assert confirm.status_code == 200, confirm.text
    confirm_body = confirm.json()
    assert confirm_body["routed_to"] == "COMPLAINT_CREATED"
    assert confirm_body["complaint_id"] is not None

    db = db_session()
    complaint = db.query(Complaint).filter(Complaint.id == confirm_body["complaint_id"]).one()
    assert complaint.original_text == "Street light near my home is not working."
    db.close()


# ---------------------------------------------------------------------------
# Phase 7: combined voice + image turns
# ---------------------------------------------------------------------------


def test_voice_plus_image_folds_the_caption_into_the_spoken_question(client, monkeypatch, make_citizen):
    """A spoken question with an attached photo: the caption folds into the same text the
    transcribed speech produced, exactly like the image-only-via-text endpoint already does."""
    fake_sarvam = _install_real_service(monkeypatch, transcript="Who do I contact about road potholes in Nagpur?")
    token, _ = make_citizen(phone="9000000208")

    response = _ask_voice(client, token, image_bytes=_JPEG_BYTES)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["question"] == "Who do I contact about road potholes in Nagpur?"
    assert _FAKE_CAPTION in body["answer"]
    assert body["audio_base64"] is not None
    fake_sarvam.synthesize_speech_long.assert_called_once()


def test_voice_plus_image_with_no_spoken_text_gets_a_real_spoken_clarification(client, monkeypatch, make_citizen):
    """The core anti-guessing rule applies to voice too: an image attached to a turn where
    nothing intelligible was transcribed (e.g. silence) must never be guessed as a complaint or
    info request -- it gets a real clarification question, AND that clarification question
    itself gets synthesized to real speech (never left as text-only just because the turn
    started as an image edge case)."""
    _install_real_service(monkeypatch, transcript="")
    token, _ = make_citizen(phone="9000000209")

    response = _ask_voice(client, token, image_bytes=_JPEG_BYTES)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["question"] == ""
    assert body["follow_up_required"] is True
    assert body["routed_to"] == "NONE_CLARIFICATION_NEEDED"
    assert _FAKE_CAPTION in body["answer"]
    assert body["audio_base64"] is not None  # the clarification question itself is spoken back


def test_voice_plus_image_caption_failure_still_produces_a_real_spoken_clarification(client, monkeypatch, make_citizen):
    _install_real_service(monkeypatch, transcript="", caption_error=True)
    token, _ = make_citizen(phone="9000000210")

    response = _ask_voice(client, token, image_bytes=_JPEG_BYTES)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["follow_up_required"] is True
    assert body["audio_base64"] is not None


def test_voice_plus_image_can_file_a_real_complaint_with_evidence(client, monkeypatch, make_citizen, db_session, make_worker):
    """The evidence system is reused identically for a voice-originated complaint with a photo --
    same ComplaintEvidence row, same real file on disk, as the text/image endpoint's own test."""
    from pathlib import Path

    from backend.models import ComplaintEvidence

    fake_sarvam = _install_real_service(monkeypatch, transcript="Street light near my home is not working.")
    make_worker(phone="9000099211", ward="Mohali")
    token, _ = make_citizen(phone="9000000211")

    response = _ask_voice(client, token, image_bytes=_JPEG_BYTES, location_text="Mohali")
    assert response.status_code == 200, response.text
    body = response.json()
    # P0 SAFETY FIX (production-safety audit): confirmation required before creation -- see
    # tests/test_ask_janmitra.py's test_type_a_complaint_creates_and_assigns_complaint. Image
    # re-attached on the confirmation call, matching the backend's per-request statelessness.
    assert body.get("complaint_id") is None

    fake_sarvam.transcribe = Mock(return_value="Yes, submit it.")
    history = json.dumps([
        {"role": "user", "content": "Street light near my home is not working."},
        {"role": "assistant", "content": body["answer"]},
    ])
    confirm = _ask_voice(client, token, image_bytes=_JPEG_BYTES, conversation_history=history)
    assert confirm.status_code == 200, confirm.text
    body = confirm.json()
    assert body["routed_to"] == "COMPLAINT_CREATED"
    complaint_id = body["complaint_id"]
    assert complaint_id is not None

    db = db_session()
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).one()
    assert complaint.photo_path is not None
    evidence = db.query(ComplaintEvidence).filter(ComplaintEvidence.complaint_id == complaint_id).all()
    assert len(evidence) == 1
    assert evidence[0].stage == "CITIZEN_COMPLAINT"
    on_disk = Path(settings.UPLOAD_FOLDER) / complaint.photo_path
    assert on_disk.is_file()
    db.close()


# ---------------------------------------------------------------------------
# Phase 7 (combined-flow spec, item 7.6): missing/empty input, no image
# ---------------------------------------------------------------------------


def test_voice_with_empty_transcript_and_no_image_never_crashes_or_fabricates(client, monkeypatch, make_citizen):
    """A voice turn whose audio transcribes to nothing usable (e.g. silence) and has no image
    attached must never crash and must never fabricate an answer -- it's just an empty question
    routed through the same real classification/clarification path an empty typed question would
    hit (the image-no-text special case in nodes.py's input_processing_node does NOT apply here,
    since there's no image -- this proves the two "empty input" paths don't get confused)."""
    _install_real_service(monkeypatch, transcript="")
    token, _ = make_citizen(phone="9000000214")

    response = _ask_voice(client, token)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["question"] == ""
    # Some real, honest response came back (most likely a category clarification, since an empty
    # message can't be classified into a service) -- never a routed_to implying a fabricated
    # complaint/answer was produced from nothing.
    assert body["routed_to"] != "COMPLAINT_CREATED"


# ---------------------------------------------------------------------------
# Phase 9 (LangSmith §9.1): speech_to_text and text_to_speech are real child
# spans of the SAME trace as the graph's own spans, not separate/missing ones.
# ---------------------------------------------------------------------------


def test_voice_request_creates_stt_and_tts_child_spans_of_the_same_root_run(client, monkeypatch, make_citizen):
    start_root_calls = []
    start_child_calls = []
    monkeypatch.setattr(
        tracing_module, "start_root_run",
        lambda name, **kw: start_root_calls.append((name, kw)) or "FAKE_ROOT",
    )
    monkeypatch.setattr(
        tracing_module, "start_child_run",
        lambda parent, name, run_type="chain", **kw: start_child_calls.append((parent, name, kw)) or name,
    )
    end_calls = []
    monkeypatch.setattr(tracing_module, "end_run", lambda run, **kw: end_calls.append((run, kw)))

    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9000000216")

    response = _ask_voice(client, token)

    assert response.status_code == 200, response.text
    assert len(start_root_calls) == 1
    assert start_root_calls[0][1]["metadata"]["input_mode"] == "VOICE_ASSISTANT"
    assert start_root_calls[0][1]["metadata"]["tts_used"] is True

    child_names = [c[1] for c in start_child_calls]
    assert "speech_to_text" in child_names
    assert "text_to_speech" in child_names
    for parent, _name, _kw in start_child_calls:
        assert parent == "FAKE_ROOT"  # both nest under the exact same trace, not a separate one

    # The root run itself is ended exactly once, after TTS (not before) -- proves ask_voice()
    # correctly deferred it via end_root_run=False rather than double-ending or leaking it open.
    root_end_calls = [c for c in end_calls if c[0] == "FAKE_ROOT"]
    assert len(root_end_calls) == 1


def test_voice_plus_image_request_creates_all_three_child_spans_in_order(client, monkeypatch, make_citizen):
    start_child_calls = []
    monkeypatch.setattr(tracing_module, "start_root_run", lambda name, **kw: "FAKE_ROOT")
    monkeypatch.setattr(
        tracing_module, "start_child_run",
        lambda parent, name, run_type="chain", **kw: start_child_calls.append(name) or name,
    )
    monkeypatch.setattr(tracing_module, "end_run", lambda run, **kw: None)

    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9000000217")

    response = _ask_voice(client, token, image_bytes=_JPEG_BYTES)

    assert response.status_code == 200, response.text
    # STT before vision before TTS -- matches docs/ask_janmitra_langsmith_observability.md §9.1's
    # "Ask Sarthi Voice -> STT -> Vision -> LangGraph -> ... -> TTS" diagram.
    assert start_child_calls.index("speech_to_text") < start_child_calls.index("vision_processing")
    assert start_child_calls.index("vision_processing") < start_child_calls.index("text_to_speech")
