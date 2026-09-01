"""A fake, in-process stand-in for the `sarvamai.SarvamAI` SDK client, used only when
`settings.SARVAM_MOCK_MODE` is on (local dev/e2e-testing only -- see that setting's own docstring
in config.py).

Why this exists: every real Sarvam call this app makes (STT, TTS, translate, and the four
chat-completion callers -- summary/normalization/category/answer-generation) costs real credits,
and the Playwright e2e suite makes dozens of them per run. A single local dev run can burn through
an account's entire balance in an afternoon of repeated test runs -- confirmed live (`status_code:
402, insufficient_quota_error`) after a heavy day of e2e debugging. This gives every one of those
call sites a response shaped exactly like the real SDK's, entirely offline, so the suite can be
run as many times as needed without spending anything.

Wired in via each service's own `client_factory` parameter to `_init_sarvam_keys()` (the exact
seam `backend/services/sarvam_key_pool.py`'s own docstring already documents was built for tests
to substitute a fake client) -- not a new mechanism, the same one this codebase's pytest suite
already relies on, just switched on for a live server process instead of a single test function.

What's faked, and how faithfully:
  - `speech_to_text.transcribe()`: returns a fixed, plausible civic-complaint sentence -- there's
    no real audio content to transcribe offline, so this can't reflect what was actually said, only
    provide enough real text for the rest of the pipeline (normalize/translate/summarize/classify)
    to have something real to work with.
  - `text_to_speech.convert()`: returns a real, valid, silent WAV file (correct header, correct
    frame data for its declared duration) -- not audible speech, but something the app's own WAV
    handling (concatenation, playback) can genuinely decode, matching real audio's actual shape.
  - `text.translate()`: returns the input text unchanged. Good enough for e2e purposes (no test
    asserts on exact translated wording), and callers already treat translation as best-effort.
  - `text.identify_language()`: returns a fixed language code -- this call is already
    best-effort/fail-open in every real caller (see SarvamClient.identify_language's own
    docstring), so any fixed answer is as good as a real one for testing purposes.
  - `chat.completions()`: the one call site with four different real callers expecting different
    kinds of content -- distinguished by sniffing the system prompt for each one's own distinctive
    marker text (the same prompt files each real call already sends, see prompts/*.txt), not a
    generic canned string, so each caller gets a response actually shaped like what it asked for:
      - complaint category classification: returns a real category via simple keyword matching
        against the complaint text (same categories the real model would return), or "UNSURE".
      - Ask Sarthi answer generation: returns the retrieved context verbatim (mirroring
        AnswerGenerationService's own `_fallback_answer()` -- the first non-FAQ-shaped excerpt),
        or the same honest "context is insufficient" line the real prompt asks the model to say
        when there's nothing to answer from -- genuinely grounded, since it's built from the same
        real context the real model would have been given, not fabricated.
      - summary/normalization (no distinctive enough marker to tell apart, and both are fine with
        the same fallback): returns the input complaint text itself, trimmed to a short sentence --
        a real no-op "summary"/"normalization", not a real rewrite, but real, valid, non-empty
        content either way.
"""

import base64
import io
import re
import wave
from types import SimpleNamespace

from backend.schemas.rag_knowledge import ServiceCategory

_FAKE_TRANSCRIPT = "There is a problem with garbage collection in my area."

# A short, valid, silent WAV -- real header + real (silent) frame data, decodable by the same
# `wave` module _concatenate_wav_audio() already uses, and playable by a real browser <audio>
# element. Mono, 16-bit, 16kHz, ~0.3s -- small and fast to generate on every call.
_FAKE_WAV_FRAMERATE = 16000
_FAKE_WAV_DURATION_SECONDS = 0.3


def _make_fake_wav_base64() -> str:
    frame_count = int(_FAKE_WAV_FRAMERATE * _FAKE_WAV_DURATION_SECONDS)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(_FAKE_WAV_FRAMERATE)
        writer.writeframes(b"\x00\x00" * frame_count)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# Same 4 real categories complaint_category_service.py's own _VALID_CATEGORIES checks against --
# duplicated here rather than imported from there to keep this module's only real dependency on
# the rest of the app to the category enum itself, not a specific service module.
_CATEGORY_KEYWORDS: dict[ServiceCategory, tuple[str, ...]] = {
    ServiceCategory.WASTE_SANITATION: ("garbage", "waste", "trash", "sanitation", "bin"),
    ServiceCategory.WATER_DRAINAGE: ("water", "drainage", "sewage", "sewer", "leak", "pipe"),
    ServiceCategory.ROADS_POTHOLES: ("road", "pothole", "street", "pavement"),
    ServiceCategory.STREETLIGHTS: ("streetlight", "street light", "lamp", "lighting"),
}


def _guess_category(complaint_text: str) -> str:
    lowered = complaint_text.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return category.value
    return "UNSURE"


def _extract_ask_sarthi_context(user_prompt: str) -> str:
    """Pulls the `{context}` block back out of a rendered ask_sarthi_answer_prompt.txt -- the
    same text real answer_generation_service.py already builds by joining context_chunks with
    "\\n\\n---\\n\\n" (see that service's own `context = "\\n\\n---\\n\\n".join(...)` line)."""
    match = re.search(
        r"Knowledge context.*?:\n\n(.*?)\n\nWrite a concise answer", user_prompt, re.DOTALL
    )
    return match.group(1).strip() if match else ""


def _fake_ask_sarthi_answer(user_prompt: str) -> str:
    context = _extract_ask_sarthi_context(user_prompt)
    if not context:
        return "I don't have enough verified information to answer that right now."
    chunks = [c.strip() for c in context.split("---") if c.strip()]
    for chunk in chunks:
        if not chunk.lstrip().startswith("Q: "):
            return chunk
    return chunks[0] if chunks else "I don't have enough verified information to answer that right now."


def _extract_complaint_text(user_prompt: str) -> str:
    """Pulls the actual `{complaint_text}` back out of a rendered normalize_prompt.txt/
    summary_prompt.txt/complaint_category_prompt.txt -- all three render it the same way, as
    the last thing in the prompt, right after a literal "Complaint:\\n" line. Without this, a
    naive "first line of the whole prompt" fake would return template/instruction text instead
    of the citizen's own words -- confirmed live: it broke every test asserting the citizen's own
    complaint text appears somewhere in the app (worker queue, complaint detail, etc.), since real
    normalize/summarize are expected to preserve (or only lightly rephrase) that exact text, not
    replace it with something else entirely."""
    match = re.search(r"Complaint:\n(.*)", user_prompt, re.DOTALL)
    return match.group(1).strip() if match else user_prompt.strip()


def _fake_chat_completion(messages: list[dict]) -> SimpleNamespace:
    system_content = next((m["content"] for m in messages if m.get("role") == "system"), "")
    user_content = next((m["content"] for m in messages if m.get("role") == "user"), "")

    if "classify civic complaints" in system_content:
        content = _guess_category(_extract_complaint_text(user_content))
    elif "You are Sarthi, the AI assistant of JanSarthi AI" in system_content:
        content = _fake_ask_sarthi_answer(user_content)
    else:
        # summary_service.py ("...already been transcribed and translated into English") /
        # normalization_service.py ("...correct obvious spelling and typing mistakes"): both ask
        # the model to preserve the complaint's own text/meaning (a real 1-2 sentence "summary" of
        # an already-short test complaint, or a spelling correction pass over already-clean text,
        # is realistically a no-op) -- returning the real complaint text verbatim is both a
        # faithful fake AND satisfies every test asserting that exact text appears later in the app.
        content = _extract_complaint_text(user_content) or "Complaint noted."

    prompt_tokens = max(1, len(user_content) // 4)
    completion_tokens = max(1, len(content) // 4)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


class _FakeSpeechToText:
    def transcribe(self, file, model, language_code):  # noqa: ARG002 -- real SDK signature
        return SimpleNamespace(transcript=_FAKE_TRANSCRIPT)


class _FakeTextToSpeech:
    def convert(self, text, language_code, speaker, model, output_audio_codec="wav", request_options=None):  # noqa: ARG002
        return SimpleNamespace(audios=[_make_fake_wav_base64()])


class _FakeText:
    def translate(self, input, source_language_code, target_language_code, model):  # noqa: A002, ARG002
        return SimpleNamespace(translated_text=input)

    def identify_language(self, input):  # noqa: A002
        return SimpleNamespace(language_code="en-IN")


class _FakeChat:
    def completions(self, model, messages, max_tokens=None, reasoning_effort=None, temperature=None):  # noqa: ARG002
        return _fake_chat_completion(messages)


class FakeSarvamAI:
    """Drop-in replacement for `sarvamai.SarvamAI` -- same constructor signature (accepts and
    ignores every real kwarg), same attribute surface this codebase actually calls."""

    def __init__(self, *args, **kwargs) -> None:  # noqa: ARG002 -- real SDK accepts api_subscription_key/timeout
        self.speech_to_text = _FakeSpeechToText()
        self.text_to_speech = _FakeTextToSpeech()
        self.text = _FakeText()
        self.chat = _FakeChat()
