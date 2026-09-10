"""Tests for the WebSocket /ask-sarthi/voice/live route ("Live" voice mode) -- see that route's
own docstring in backend/routes/ask_sarthi.py for the full design.

Fakes Sarvam's realtime STT/streaming TTS sockets entirely (no real network call, no real Sarvam
account needed) via a small in-process double standing in for `SarvamRealtimeClient` -- this suite
is about the new WebSocket plumbing itself (auth, message protocol/ordering, rate limiting, error
handling), not the orchestration graph, which is already exhaustively covered by
test_ask_sarthi.py/test_ask_sarthi_voice.py. `AskSarthiService.ask()` is mocked directly for the
same reason (avoids spinning up the real Chroma/embedding stack for a test that isn't about RAG).
"""

import time
from contextlib import asynccontextmanager
from unittest.mock import Mock

import backend.routes.ask_sarthi as ask_sarthi_module
from backend.schemas.ask_sarthi import AskSarthiResponse
from backend.services.intent_classifier import QuestionIntent
from sarvamai.types.audio_output import AudioOutput
from sarvamai.types.audio_output_data import AudioOutputData
from sarvamai.types.event_response import EventResponse
from sarvamai.types.event_response_data import EventResponseData
from sarvamai.types.realtime_error import RealtimeError as SarvamRealtimeError
from sarvamai.types.realtime_transcript_final import RealtimeTranscriptFinal


class _FakeFatalErrorTranscriptionSocket:
    """LIVE-REPORTED BUG: reproduces Sarvam's real behavior when its realtime STT quota is
    exhausted (confirmed live against a real, credit-exhausted local dev account) -- sends one
    fatal RealtimeError, then the underlying connection is gone (Sarvam closes it right after).
    `.recv()` after the first call raises, exactly like the real `websockets` client does against
    an already-closed connection, so this also proves the route doesn't try to keep reading from a
    dead socket."""

    def __init__(self) -> None:
        self._sent_error = False

    async def send_realtime_audio_input(self, message) -> None:
        pass

    async def recv(self):
        if not self._sent_error:
            self._sent_error = True
            return SarvamRealtimeError(
                code="quota_exceeded", is_fatal=True,
                message="Credits exhausted. Visit the API Dashboard to review and manage your subscription.",
                status_code=402,
            )
        raise ConnectionError("connection closed")


class _FakeTranscriptionSocket:
    """`.recv()` yields each of `transcripts` in order as a finalized utterance, then blocks
    "forever" (a long sleep) once exhausted -- mirrors Sarvam's real socket staying open and
    listening for more speech rather than closing after one utterance."""

    def __init__(self, transcripts: list[str]) -> None:
        import asyncio

        self._asyncio = asyncio
        self._transcripts = list(transcripts)
        self.sent_audio: list[str] = []

    async def send_realtime_audio_input(self, message) -> None:
        self.sent_audio.append(message.audio)

    async def recv(self):
        if self._transcripts:
            text = self._transcripts.pop(0)
            return RealtimeTranscriptFinal(utterance_idx=0, text=text)
        await self._asyncio.sleep(3600)


class _FakeTTSSocket:
    def __init__(self, chunks: list[str]) -> None:
        self._events = [AudioOutput(data=AudioOutputData(content_type="audio/wav", audio=chunk)) for chunk in chunks]
        self._events.append(EventResponse(data=EventResponseData(event_type="final")))
        self.configured_with: dict | None = None
        self.converted_text: str | None = None

    async def configure(self, **kwargs) -> None:
        self.configured_with = kwargs

    async def convert(self, text: str) -> None:
        self.converted_text = text

    async def flush(self) -> None:
        pass

    async def recv(self):
        return self._events.pop(0)


class _FakeRealtimeClient:
    def __init__(self, transcripts: list[str], tts_chunks: list[str]) -> None:
        self._transcripts = transcripts
        self._tts_chunks = tts_chunks
        self.stt_socket: _FakeTranscriptionSocket | None = None
        self.tts_sockets: list[_FakeTTSSocket] = []

    def configured(self) -> bool:
        return True

    @asynccontextmanager
    async def open_transcription_stream(self, language_code: str):
        self.stt_socket = _FakeTranscriptionSocket(self._transcripts)
        yield self.stt_socket

    @asynccontextmanager
    async def open_tts_stream(self):
        socket = _FakeTTSSocket(self._tts_chunks)
        self.tts_sockets.append(socket)
        yield socket


class _FakeRealtimeClientWithFatalSttError:
    """Same shape as `_FakeRealtimeClient` above, but its STT socket sends one fatal error
    instead of any real transcript -- see `_FakeFatalErrorTranscriptionSocket`'s own docstring."""

    def configured(self) -> bool:
        return True

    @asynccontextmanager
    async def open_transcription_stream(self, language_code: str):
        yield _FakeFatalErrorTranscriptionSocket()

    @asynccontextmanager
    async def open_tts_stream(self):
        yield _FakeTTSSocket([])


def _fake_response(**overrides) -> AskSarthiResponse:
    fields = {
        "answer": "Your Waste Sanitation complaint has been filed (complaint #1).",
        "intent": QuestionIntent.TYPE_A_COMPLAINT,
        "language": "en",
        "routed_to": "COMPLAINT_CREATED",
    }
    fields.update(overrides)
    return AskSarthiResponse(**fields)


def test_voice_live_full_turn_streams_answer_and_audio(client, monkeypatch, make_citizen):
    """The full happy path: connect, send one audio frame, Sarvam's (faked) VAD finalizes an
    utterance, the same AskSarthiService.ask() every other endpoint uses runs, and the answer
    streams back as turn_started -> answer -> audio_chunk(s) -> turn_complete, in that order."""
    token, _user = make_citizen(phone="9100000601")

    fake_client = _FakeRealtimeClient(
        transcripts=["Garbage is not being collected in my street."], tts_chunks=["ZmFrZQ==", "Y2h1bmsy"],
    )
    monkeypatch.setattr(ask_sarthi_module, "_realtime_client", fake_client)
    mock_ask = Mock(return_value=_fake_response())
    monkeypatch.setattr(ask_sarthi_module._service, "ask", mock_ask)

    with client.websocket_connect("/ask-sarthi/voice/live?language=en") as ws:
        ws.send_bytes(b"\x00\x01" * 100)

        assert ws.receive_json() == {
            "type": "turn_started", "transcript": "Garbage is not being collected in my street.",
        }

        answer_msg = ws.receive_json()
        assert answer_msg["type"] == "answer"
        assert answer_msg["answer"] == "Your Waste Sanitation complaint has been filed (complaint #1)."
        assert answer_msg["routed_to"] == "COMPLAINT_CREATED"

        assert ws.receive_json() == {"type": "audio_chunk", "audio_base64": "ZmFrZQ==", "content_type": "audio/wav"}
        assert ws.receive_json() == {"type": "audio_chunk", "audio_base64": "Y2h1bmsy", "content_type": "audio/wav"}

        assert ws.receive_json() == {"type": "turn_complete"}

    # The exact transcript Sarvam's (faked) VAD finalized is what actually reached the
    # orchestration -- proves this route doesn't re-transcribe or otherwise mangle it.
    assert mock_ask.call_args[0][2].question == "Garbage is not being collected in my street."
    assert mock_ask.call_args[0][2].was_voice_input is True


def test_voice_live_sends_thinking_pings_during_a_slow_turn(client, monkeypatch, make_citizen):
    """LIVE-REPORTED BUG this closes: a knowledge-question turn can legitimately take 20-25s (the
    only path that calls the reasoning LLM) -- the connection sat completely silent that whole
    time, which was long enough in production to trip uvicorn's own WebSocket keepalive and kill
    the session outright. `_service.ask()` now runs as a task polled every
    `_THINKING_PING_INTERVAL_SECONDS`, sending a harmless `{"type": "thinking"}` ping on each
    poll that doesn't yet see it done -- shrunk here to a few milliseconds so this test doesn't
    need a real multi-second sleep to prove the loop actually fires more than once."""
    make_citizen(phone="9100000605")
    monkeypatch.setattr(ask_sarthi_module, "_THINKING_PING_INTERVAL_SECONDS", 0.05)

    fake_client = _FakeRealtimeClient(transcripts=["How do I report a pothole?"], tts_chunks=["ZmFrZQ=="])
    monkeypatch.setattr(ask_sarthi_module, "_realtime_client", fake_client)

    def _slow_ask(db, user, request):
        time.sleep(0.2)  # long enough, relative to the shrunk interval above, for several polls
        return _fake_response(answer="Potholes are reported via...", intent=QuestionIntent.TYPE_B_SERVICE_INFO, routed_to="RAG")

    monkeypatch.setattr(ask_sarthi_module._service, "ask", Mock(side_effect=_slow_ask))

    with client.websocket_connect("/ask-sarthi/voice/live?language=en") as ws:
        ws.send_bytes(b"\x00\x01")

        assert ws.receive_json() == {"type": "turn_started", "transcript": "How do I report a pothole?"}

        seen_thinking = 0
        message = ws.receive_json()
        while message["type"] == "thinking":
            seen_thinking += 1
            message = ws.receive_json()

        assert seen_thinking >= 2
        assert message["type"] == "answer"
        assert message["answer"] == "Potholes are reported via..."


def test_voice_live_rejects_a_connection_with_no_valid_session(client, monkeypatch):
    """No login at all on this TestClient instance -- no access_token cookie ever set -- so the
    connection must be refused (closed) rather than silently proceeding unauthenticated."""
    fake_client = _FakeRealtimeClient(transcripts=[], tts_chunks=[])
    monkeypatch.setattr(ask_sarthi_module, "_realtime_client", fake_client)

    with client.websocket_connect("/ask-sarthi/voice/live?language=en") as ws:
        message = ws.receive_json()
        assert message["type"] == "error"


def test_voice_live_rejects_an_unsupported_language(client, monkeypatch, make_citizen):
    make_citizen(phone="9100000602")
    fake_client = _FakeRealtimeClient(transcripts=[], tts_chunks=[])
    monkeypatch.setattr(ask_sarthi_module, "_realtime_client", fake_client)

    with client.websocket_connect("/ask-sarthi/voice/live?language=zz") as ws:
        message = ws.receive_json()
        assert message["type"] == "error"
        assert "Unsupported language" in message["detail"]


def test_voice_live_rate_limits_per_utterance(client, monkeypatch, make_citizen):
    """Same limiter/settings require_ai_rate_limit uses for every other Ask Sarthi entry point --
    a citizen can't get a higher effective quota just by switching to Live mode."""
    make_citizen(phone="9100000603")
    monkeypatch.setattr(ask_sarthi_module.settings, "AI_RATE_LIMIT", 1)

    fake_client = _FakeRealtimeClient(transcripts=["first", "second"], tts_chunks=["ZmFrZQ=="])
    monkeypatch.setattr(ask_sarthi_module, "_realtime_client", fake_client)
    monkeypatch.setattr(ask_sarthi_module._service, "ask", Mock(return_value=_fake_response()))

    with client.websocket_connect("/ask-sarthi/voice/live?language=en") as ws:
        ws.send_bytes(b"\x00\x01")

        assert ws.receive_json() == {"type": "turn_started", "transcript": "first"}
        assert ws.receive_json()["type"] == "answer"
        assert ws.receive_json()["type"] == "audio_chunk"
        assert ws.receive_json() == {"type": "turn_complete"}

        # Second utterance: quota already spent this window -- an error, not another real turn.
        second = ws.receive_json()
        assert second["type"] == "error"
        assert "Too many requests" in second["detail"]


def test_voice_live_forwards_a_fatal_sarvam_error_to_the_client(client, monkeypatch, make_citizen):
    """LIVE-REPORTED BUG: confirmed live against a real, credit-exhausted local dev Sarvam
    account -- a fatal RealtimeError used to be logged server-side only. The client never learned
    anything went wrong, and the very next `stt_socket.recv()` call then raised an uncaught
    ConnectionClosedError (Sarvam closes its own socket right after sending the error), silently
    killing the session -- the citizen's UI just sat on "Listening..." forever with no error shown
    and no way to know why. Fixed by forwarding the error message to the client and returning
    (not looping back into recv() again) as soon as a FATAL one arrives."""
    make_citizen(phone="9100000604")
    monkeypatch.setattr(ask_sarthi_module, "_realtime_client", _FakeRealtimeClientWithFatalSttError())

    with client.websocket_connect("/ask-sarthi/voice/live?language=en") as ws:
        message = ws.receive_json()
        assert message["type"] == "error"
        assert "Credits exhausted" in message["detail"]
