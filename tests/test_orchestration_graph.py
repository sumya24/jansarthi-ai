"""Tests for the LangGraph orchestration layer itself (backend/services/orchestration/) --
node-level unit tests and routing-function unit tests that don't need the full HTTP/RAG stack,
plus the multi-turn clarification scenario from the spec's own worked example. End-to-end
routing/RAG/complaint-creation behavior through the real `/ask-sarthi` endpoint is covered by
tests/test_ask_sarthi.py; this file focuses on the graph's own structure and state handling.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import backend.routes.ask_sarthi as ask_sarthi_module
from backend.models import Complaint
from backend.schemas.ask_sarthi import ConversationTurn
from backend.schemas.rag_knowledge import ServiceCategory
from backend.services.intent_classifier import QuestionIntent
from backend.services.orchestration.graph import (
    _route_after_complaint,
    _route_after_intent,
    _route_after_location,
    build_graph,
)
from backend.services.orchestration.nodes import (
    GraphDeps,
    _recover_text_before_intent_ambiguous_turn,
    agent_flow_node,
    input_processing_node,
    intent_node,
    language_node,
    rag_flow_node,
)
from backend.services.rag_retriever import RetrievalOutcome
from backend.services.vector_store import ScoredChunk
from tests.test_ask_sarthi import _ask, _install_real_service, _real_ask_sarthi_service


def _minimal_graph_deps(**overrides) -> GraphDeps:
    kwargs = dict(
        retriever=Mock(), location_extractor=Mock(), answer_service=Mock(),
        complaint_agent=Mock(), location_resolver=Mock(),
    )
    kwargs.update(overrides)
    return GraphDeps(**kwargs)


# --- node-level unit tests (no DB/config needed for these three) ---


def test_input_processing_node_normalizes_whitespace_preserves_original():
    state = {"user_message": "  Street light not working.  "}
    update = input_processing_node(state, config={})
    assert update["normalized_message"] == "Street light not working."
    assert update["input_type"] == "text"
    assert state["user_message"] == "  Street light not working.  "  # untouched


def test_input_processing_node_preserves_explicit_input_type():
    update = input_processing_node({"user_message": "x", "input_type": "voice"}, config={})
    assert update["input_type"] == "voice"


def test_language_node_falls_back_to_original_language_when_no_text():
    """No `normalized_message` at all (e.g. an image-only turn) -- nothing to detect from, so
    this must fall back to the client-supplied `original_language` exactly as the old identity
    behavior did."""
    update = language_node({"original_language": "hi"}, config={})
    assert update["response_language"] == "hi"


def test_language_node_defaults_to_english_when_missing():
    update = language_node({}, config={})
    assert update["response_language"] == "en"


def test_language_node_falls_back_on_missing_config_without_crashing():
    """config={} (no `deps` at all) must degrade gracefully to the fallback, not raise -- covers
    any caller (including these very unit tests) that invokes this node directly without the
    full GraphDeps machinery."""
    update = language_node({"original_language": "hi", "normalized_message": "What is the status of my complaint?"}, config={})
    assert update["response_language"] == "hi"


def test_language_node_uses_detected_language_over_a_mismatched_original_language():
    """The actual auto-detect fix, live-reported: a citizen's UI language toggle says "en" but
    the message they actually typed is Marathi -- response_language must follow what they
    ACTUALLY wrote, matching ChatGPT/Claude-style "answer in whatever language I asked in", not
    the stale toggle value."""
    fake_translation = Mock()
    fake_translation.detect_language = Mock(return_value="mr")
    deps = _minimal_graph_deps(translation_service=fake_translation)
    config = {"configurable": {"deps": deps}}
    state = {"original_language": "en", "normalized_message": "बेंगळुरूमध्ये पाणीपुरवठ्याबाबत तक्रार करण्याची प्रक्रिया काय आहे?"}

    update = language_node(state, config)

    assert update["response_language"] == "mr"
    fake_translation.detect_language.assert_called_once_with("बेंगळुरूमध्ये पाणीपुरवठ्याबाबत तक्रार करण्याची प्रक्रिया काय आहे?")


def test_language_node_falls_back_when_detection_returns_none():
    """Detection unavailable/failed/unsupported-language (see TranslationService.detect_language's
    own docstring) -- must fall back to original_language, never leave response_language unset or
    raise."""
    fake_translation = Mock()
    fake_translation.detect_language = Mock(return_value=None)
    deps = _minimal_graph_deps(translation_service=fake_translation)
    config = {"configurable": {"deps": deps}}
    state = {"original_language": "hi", "normalized_message": "What is the status of my complaint?"}

    update = language_node(state, config)

    assert update["response_language"] == "hi"


def test_language_node_prefers_established_language_when_real_detection_genuinely_fails():
    """LIVE-REPORTED BUG (voice input): real detection was genuinely ATTEMPTED for a short,
    slightly garbled voice-transcribed reply ("हो दासल तर.") -- Sarvam's own text-lid returned
    language_code=null (confirmed directly against the live API), not enough signal to identify
    ANY language at all. Falling straight to the stale client-supplied `original_language` here is
    the exact same unreliable-signal problem the established-language fallback already exists to
    avoid for the SKIPPED-detection cases (see the sibling tests above) -- this must ALSO apply
    when detection was attempted but came back empty, not just when it was skipped outright."""
    last_assistant_text = "तुमची तक्रार वॉर्ड 3 मधील स्ट्रीटलाईटबद्दल असेल. ही तक्रार दाखल करायची आहे का?"
    text = "हो दासल तर."

    def fake_detect(candidate: str) -> str | None:
        # The real attempt on THIS turn's own (short, garbled) text genuinely fails, exactly like
        # Sarvam's real text-lid did live -- only the fallback call on the established, full
        # assistant sentence succeeds.
        return "mr" if candidate == last_assistant_text else None

    fake_translation = Mock()
    fake_translation.detect_language = Mock(side_effect=fake_detect)
    deps = _minimal_graph_deps(translation_service=fake_translation)
    config = {"configurable": {"deps": deps}}
    state = {
        "original_language": "en",
        "normalized_message": text,
        "conversation_history": [
            {"role": "user", "content": "माझ्या घरासमोर स्ट्रीट लाईट खराब आहे."},
            {"role": "assistant", "content": last_assistant_text},
        ],
    }

    update = language_node(state, config)

    assert update["response_language"] == "mr"
    fake_translation.detect_language.assert_any_call(text)
    fake_translation.detect_language.assert_any_call(last_assistant_text)


def test_language_node_falls_back_when_translation_service_is_unavailable():
    """GraphDeps.translation_service defaults to None (see its own docstring) -- this node must
    degrade to the fallback exactly like a failed detection call, never raise."""
    deps = _minimal_graph_deps()  # translation_service left at its None default
    config = {"configurable": {"deps": deps}}
    state = {"original_language": "hi", "normalized_message": "What is the status of my complaint?"}

    update = language_node(state, config)

    assert update["response_language"] == "hi"


def test_language_node_skips_detection_for_a_short_confirmation_reply():
    """A citizen who has been conversing entirely in Marathi replies with the plain English
    confirmation word "yes" (see intent_classifier.py's _CONFIRMATION_EXACT_WORDS["en"]) -- this
    single low-signal word must NOT flip response_language to English mid-conversation. Detection
    is skipped entirely for short, non-question replies (mirrors intent_classifier's own
    continuation-detection heuristic) -- the translation_service mock asserts it was never even
    called, proving this is a skip, not a lucky fallback from a call that happened to return
    nothing."""
    fake_translation = Mock()
    fake_translation.detect_language = Mock(return_value="en")  # would (wrongly) flip it if called
    deps = _minimal_graph_deps(translation_service=fake_translation)
    config = {"configurable": {"deps": deps}}
    state = {"original_language": "mr", "normalized_message": "yes"}

    update = language_node(state, config)

    assert update["response_language"] == "mr"
    fake_translation.detect_language.assert_not_called()


def test_language_node_still_detects_a_short_non_ascii_statement():
    """LIVE-REPORTED BUG: a citizen's very FIRST message of a brand-new conversation -- a genuine,
    complete complaint in Marathi that happens to be exactly 6 words and isn't phrased as a
    question -- got the short-reply skip applied to it too, falling back to the stale
    client-supplied `original_language` ("en") instead of detecting the citizen's real language.
    Unlike "yes"/"ok", real Devanagari script is never ambiguous about not being English
    regardless of word count, so a short NON-ASCII statement must still go through real detection
    -- only a short PLAIN-ASCII reply (see the sibling test above) gets skipped."""
    fake_translation = Mock()
    fake_translation.detect_language = Mock(return_value="mr")
    deps = _minimal_graph_deps(translation_service=fake_translation)
    config = {"configurable": {"deps": deps}}
    text = "माझ्या घराजवळ कचरा साचला आहे, कोलकातामध्ये."
    state = {"original_language": "en", "normalized_message": text}

    update = language_node(state, config)

    assert update["response_language"] == "mr"
    fake_translation.detect_language.assert_called_once_with(text)


def test_language_node_still_detects_a_short_question():
    """The short-reply skip must not swallow a genuinely short QUESTION -- "?" is a strong enough
    signal on its own (see intent_classifier._looks_like_question) that a short question still
    goes through real detection."""
    fake_translation = Mock()
    fake_translation.detect_language = Mock(return_value="mr")
    deps = _minimal_graph_deps(translation_service=fake_translation)
    config = {"configurable": {"deps": deps}}
    state = {"original_language": "en", "normalized_message": "पाणी कधी येईल?"}

    update = language_node(state, config)

    assert update["response_language"] == "mr"
    fake_translation.detect_language.assert_called_once_with("पाणी कधी येईल?")


def test_language_node_preserves_established_language_for_a_short_ascii_button_click():
    """SECOND LIVE-REPORTED BUG: a multilingual quick-reply button's clicked VALUE always stays
    canonical English regardless of what language its LABEL was shown in (see nodes.py's
    `_localize_options` docstring) -- clicking one after an entirely-Hindi conversation used to
    fall back straight to the stale client-supplied `original_language` ("en"), flipping every
    following reply back to English. Must instead detect the language from the last ASSISTANT
    turn's own text (a real sentence, reliable to detect from) and use THAT as the fallback."""
    fake_translation = Mock()
    fake_translation.detect_language = Mock(return_value="hi")
    deps = _minimal_graph_deps(translation_service=fake_translation)
    config = {"configurable": {"deps": deps}}
    last_assistant_text = "क्या आप वेस्ट सैनिटेशन के साथ किसी समस्या की रिपोर्ट कर रही हैं, या आप इसके बारे में जानकारी चाहेंगी?"
    state = {
        "original_language": "en",
        "normalized_message": "Report a problem",
        "conversation_history": [
            {"role": "user", "content": "मेरे घर के पास कचरा जमा हो गया है, कोलकाता में"},
            {"role": "assistant", "content": last_assistant_text},
        ],
    }

    update = language_node(state, config)

    assert update["response_language"] == "hi"
    fake_translation.detect_language.assert_called_once_with(last_assistant_text)


def test_language_node_falls_back_to_original_language_when_no_assistant_history_yet():
    """The established-language fallback above must not fire on a genuinely first message --
    no assistant turn exists yet to detect a language from, so this degrades to the pre-existing
    `original_language` fallback exactly as before, without ever calling detect_language a second
    time."""
    fake_translation = Mock()
    fake_translation.detect_language = Mock(return_value="hi")
    deps = _minimal_graph_deps(translation_service=fake_translation)
    config = {"configurable": {"deps": deps}}
    state = {"original_language": "mr", "normalized_message": "yes", "conversation_history": []}

    update = language_node(state, config)

    assert update["response_language"] == "mr"
    fake_translation.detect_language.assert_not_called()


def test_language_node_preserves_established_language_for_a_question_shaped_quick_reply():
    """THIRD LIVE-REPORTED BUG: "What is the procedure?" is one of the exact quick-reply VALUES a
    translated button sends back (see nodes.py's `_ALL_QUICK_REPLY_OPTIONS` docstring) -- but
    unlike "Report a problem"/"Yes, submit it", it's phrased as a real English question, so the
    short/non-question skip never applied to it: real detection ran on the literal English text
    and correctly (on its own narrow terms) identified it as English, flipping an established
    Hindi conversation back to English on this one button click. Every quick-reply value is
    equally "not organic typed text" regardless of its own shape, so this must ALSO preserve the
    established conversation language, exactly like the short/non-question case."""
    fake_translation = Mock()
    fake_translation.detect_language = Mock(return_value="hi")
    deps = _minimal_graph_deps(translation_service=fake_translation)
    config = {"configurable": {"deps": deps}}
    last_assistant_text = "क्या आप वेस्ट सैनिटेशन के साथ किसी समस्या की रिपोर्ट कर रही हैं, या आप इसके बारे में जानकारी चाहेंगी?"
    state = {
        "original_language": "en",
        "normalized_message": "What is the procedure?",
        "conversation_history": [
            {"role": "user", "content": "मेरे घर के पास कचरा जमा हो गया है, कोलकाता में"},
            {"role": "assistant", "content": last_assistant_text},
        ],
    }

    update = language_node(state, config)

    assert update["response_language"] == "hi"
    fake_translation.detect_language.assert_called_once_with(last_assistant_text)


def test_recover_text_before_intent_ambiguous_turn_recovers_the_original_hindi_complaint():
    """LIVE-REPORTED BUG: a citizen's own complaint-shaped Hindi message got asked "Are you
    reporting a problem with Waste Sanitation, or would you like information about it?" (in
    Hindi -- see clarification_flow_node's `intent_ambiguous` branch, now localized). Clicking
    "Report a problem" carries no description of its own -- before this fix, NOTHING recovered
    the citizen's original text for a TEXT-ONLY (no photo) intent_ambiguous turn (only the
    photo-caption case was ever recovered, see `_recover_photo_context_from_intent_ambiguous_turn`
    's own docstring), so the complaint got stored with the bare button label "Report a problem"
    as its entire description. Language-independent: matched via the SAME multi-language
    `_INTENT_AMBIGUOUS_CLARIFICATION_MARKERS` list `_last_turn_invites_complaint_reply` already
    uses, not an English-only pattern."""
    original_text = "मेरे घर के पास कचरा जमा हो गया है, कोलकाता में"
    state = {
        "conversation_history": [
            {"role": "user", "content": original_text},
            {
                "role": "assistant",
                "content": "क्या आप वेस्ट सैनिटेशन के साथ किसी समस्या की रिपोर्ट कर रही हैं, या आप इसके बारे में जानकारी चाहेंगी?",
            },
        ],
    }

    assert _recover_text_before_intent_ambiguous_turn(state) == original_text


def test_recover_text_before_intent_ambiguous_turn_none_when_last_turn_is_something_else():
    """Must not fire on an unrelated assistant turn (e.g. a location clarification) -- only the
    specific intent_ambiguous marker set should match."""
    state = {
        "conversation_history": [
            {"role": "user", "content": "Street light not working."},
            {"role": "assistant", "content": "What is the location? This helps me give you the correct local information."},
        ],
    }

    assert _recover_text_before_intent_ambiguous_turn(state) is None


def test_recover_text_before_intent_ambiguous_turn_none_with_no_history():
    assert _recover_text_before_intent_ambiguous_turn({}) is None
    assert _recover_text_before_intent_ambiguous_turn({"conversation_history": []}) is None


def test_rag_flow_node_recovers_category_from_history_when_this_turns_own_text_has_none():
    """LIVE-REPORTED BUG: clicking "What is the procedure?" after an intent_ambiguous clarification
    carries no category of its own (see rag_flow_node's own comment on this fix) -- unlike the
    "Report a problem" side of the same fork, this RAG/info path had no recovery at all, so
    retrieval ran with category=None and could match a completely different service's chunk.
    Direct, mock-based unit test (rather than a live-content one, which turned out NOT to
    discriminate for at least one real city/category combination where the correct chunk still
    ranked first by semantic similarity alone) -- asserts the retriever is actually called with
    the RECOVERED category, the one thing this fix changes."""
    fake_retriever = Mock()
    fake_retriever.retrieve = Mock(return_value=RetrievalOutcome(insufficient_knowledge=True))
    deps = _minimal_graph_deps(retriever=fake_retriever)
    config = {"configurable": {"deps": deps}}
    state = {
        "normalized_message": "What is the procedure?",
        "service_category": None,
        "response_language": "en",
        "conversation_history": [
            {"role": "user", "content": "Garbage in Kolkata."},
            {
                "role": "assistant",
                "content": "Are you reporting a problem with Waste Sanitation, or would you like information about it?",
            },
        ],
    }

    rag_flow_node(state, config)

    fake_retriever.retrieve.assert_called_once()
    called_category = fake_retriever.retrieve.call_args[0][1]
    assert called_category == ServiceCategory.WASTE_SANITATION


def test_rag_flow_node_translates_the_no_llm_fallback_answer_when_llm_answer_generation_fails():
    """LIVE-REPORTED BUG: when `AnswerGenerationService.generate()` can't reach the LLM (its own
    graceful-degradation fallback -- see that method's docstring), it returns the knowledge base's
    raw English excerpt verbatim, `was_llm_generated=False`. Confirmed live: a genuine 45s Sarvam
    reasoning-model timeout on a Hindi conversation produced an answer that was raw English body
    text glued onto the one Hindi sentence the separate `in_app_note` footer adds below -- unlike
    the LLM path (prompted to answer `in {language_name}`), this fallback was never translated at
    all. Asserts the fallback text is now run through `_localize`'s translation call (the SAME
    fast, non-reasoning-model translate endpoint already used for every other hardcoded string in
    this module) before being returned -- so it doesn't reintroduce the timeout risk that caused
    the fallback in the first place."""
    fake_chunk = ScoredChunk(
        chunk_id="c1", score=0.9,
        metadata={"content": "Required information: exact location.", "source_id": "s1"},
    )
    fake_retriever = Mock()
    fake_retriever.retrieve = Mock(return_value=RetrievalOutcome(results=[fake_chunk]))
    fake_answer_service = Mock()
    fake_answer_service.generate = Mock(return_value=("Required information: exact location.", False, None))
    fake_translation_service = Mock()
    fake_translation_service.to_language = Mock(return_value="आवश्यक जानकारी: सटीक स्थान।")
    deps = _minimal_graph_deps(
        retriever=fake_retriever, answer_service=fake_answer_service,
        translation_service=fake_translation_service,
    )
    config = {"configurable": {"deps": deps, "ctx": Mock()}}
    state = {
        "normalized_message": "सड़क पर गड्ढा है",
        "service_category": ServiceCategory.ROADS_POTHOLES.value,
        "response_language": "hi",
    }

    with patch("backend.services.orchestration.nodes.get_cached_answer", return_value=None), \
         patch("backend.services.orchestration.nodes.store_answer") as fake_store:
        result = rag_flow_node(state, config)

    fake_translation_service.to_language.assert_any_call("Required information: exact location.", "hi")
    assert "Required information" not in result["response_text"]
    assert "आवश्यक जानकारी" in result["response_text"]
    fake_store.assert_not_called()  # a degraded fallback answer must never be frozen into the cache


# --- agent_flow_node -- the supervisor/multi-agent node for a genuinely multi-category question
# (see docs/ask_sarthi_orchestration.md §17) -----------------------------------------------


def test_agent_flow_node_calls_the_retriever_once_per_detected_category():
    fake_retriever = Mock()
    fake_retriever.retrieve = Mock(return_value=RetrievalOutcome(insufficient_knowledge=True, reason="none"))
    deps = _minimal_graph_deps(retriever=fake_retriever)
    config = {"configurable": {"deps": deps}}
    state = {
        "normalized_message": "There is garbage piling up and also a pothole on my street.",
        "response_language": "en",
    }

    agent_flow_node(state, config)

    assert fake_retriever.retrieve.call_count == 2
    called_categories = {call.args[1] for call in fake_retriever.retrieve.call_args_list}
    assert called_categories == {ServiceCategory.WASTE_SANITATION, ServiceCategory.ROADS_POTHOLES}


def test_agent_flow_node_combines_per_category_answers_and_merges_sources():
    waste_chunk = ScoredChunk(
        chunk_id="w1", score=0.9,
        metadata={
            "content": "Garbage is collected every Tuesday and Friday.", "source_id": "WASTE_SRC",
            "verification_status": "VERIFIED",
        },
    )
    roads_chunk = ScoredChunk(
        chunk_id="r1", score=0.85,
        metadata={
            "content": "Report potholes to the public works department.", "source_id": "ROADS_SRC",
            "verification_status": "SYNTHETIC",
        },
    )

    def fake_retrieve(query, category, city, state_):
        if category == ServiceCategory.WASTE_SANITATION:
            return RetrievalOutcome(results=[waste_chunk])
        if category == ServiceCategory.ROADS_POTHOLES:
            return RetrievalOutcome(results=[roads_chunk])
        return RetrievalOutcome(insufficient_knowledge=True)

    fake_retriever = Mock()
    fake_retriever.retrieve = Mock(side_effect=fake_retrieve)
    fake_answer_service = Mock()
    fake_answer_service.generate = Mock(side_effect=lambda q, chunks, lang, context_labels=None: (chunks[0], True, None))
    deps = _minimal_graph_deps(retriever=fake_retriever, answer_service=fake_answer_service)
    config = {"configurable": {"deps": deps}}
    state = {
        "normalized_message": "There is garbage piling up and also a pothole on my street.",
        "response_language": "en",
    }

    result = agent_flow_node(state, config)

    assert result["routed_to"] == "RAG_MULTI_CATEGORY"
    assert result["insufficient_knowledge"] is False
    assert "Garbage is collected every Tuesday and Friday." in result["response_text"]
    assert "Report potholes to the public works department." in result["response_text"]
    source_ids = {s["source_id"] for s in result["sources"]}
    assert source_ids == {"WASTE_SRC", "ROADS_SRC"}
    assert result["verification_status"] == "MIXED"
    # BUG FIX (code review): agent_flow_node was missing the same "Report Issue" in-app note
    # rag_flow_node appends for a single-category answer -- a real, live quality gap between the
    # two paths for what should be an equivalent citizen experience.
    assert "Report Issue" in result["response_text"]


def test_agent_flow_node_skips_the_report_issue_note_when_every_category_is_insufficient():
    """Matches rag_flow_node's own gate -- nothing to "also report" if nothing was actually
    answered at all."""
    fake_retriever = Mock()
    fake_retriever.retrieve = Mock(return_value=RetrievalOutcome(insufficient_knowledge=True, reason="none"))
    deps = _minimal_graph_deps(retriever=fake_retriever)
    config = {"configurable": {"deps": deps}}
    state = {
        "normalized_message": "There is garbage piling up and also a pothole on my street.",
        "response_language": "en",
    }

    result = agent_flow_node(state, config)

    assert "Report Issue" not in result["response_text"]


def test_agent_flow_node_skips_the_report_issue_note_for_a_new_connection_question():
    waste_chunk = ScoredChunk(
        chunk_id="w1", score=0.9,
        metadata={"content": "Garbage is collected every Tuesday.", "source_id": "WASTE_SRC", "verification_status": "VERIFIED"},
    )
    fake_retriever = Mock()
    fake_retriever.retrieve = Mock(return_value=RetrievalOutcome(results=[waste_chunk]))
    fake_answer_service = Mock()
    fake_answer_service.generate = Mock(return_value=("Garbage is collected every Tuesday.", True, None))
    deps = _minimal_graph_deps(retriever=fake_retriever, answer_service=fake_answer_service)
    config = {"configurable": {"deps": deps}}
    state = {
        "normalized_message": "There is garbage piling up and also a pothole on my street.",
        "response_language": "en",
        "requests_new_connection": True,
    }

    result = agent_flow_node(state, config)

    assert "Report Issue" not in result["response_text"]


def test_agent_flow_node_is_only_insufficient_when_every_category_has_nothing():
    fake_retriever = Mock()
    fake_retriever.retrieve = Mock(return_value=RetrievalOutcome(insufficient_knowledge=True, reason="none"))
    deps = _minimal_graph_deps(retriever=fake_retriever)
    config = {"configurable": {"deps": deps}}
    state = {
        "normalized_message": "There is garbage piling up and also a pothole on my street.",
        "response_language": "en",
    }

    result = agent_flow_node(state, config)

    assert result["insufficient_knowledge"] is True
    assert result["sources"] == []
    # Still a real, honest per-category answer for each -- never silently empty.
    assert "Waste Sanitation" in result["response_text"]
    assert "Roads Potholes" in result["response_text"]


def test_agent_flow_node_partial_coverage_is_not_treated_as_fully_insufficient():
    """One category answered, one not -- a citizen who reported two issues and got one real
    answer plus one honest 'don't have that' section still got real help."""
    waste_chunk = ScoredChunk(
        chunk_id="w1", score=0.9,
        metadata={"content": "Garbage is collected every Tuesday.", "source_id": "WASTE_SRC", "verification_status": "VERIFIED"},
    )

    def fake_retrieve(query, category, city, state_):
        if category == ServiceCategory.WASTE_SANITATION:
            return RetrievalOutcome(results=[waste_chunk])
        return RetrievalOutcome(insufficient_knowledge=True, reason="none")

    fake_retriever = Mock()
    fake_retriever.retrieve = Mock(side_effect=fake_retrieve)
    fake_answer_service = Mock()
    fake_answer_service.generate = Mock(return_value=("Garbage is collected every Tuesday.", True, None))
    deps = _minimal_graph_deps(retriever=fake_retriever, answer_service=fake_answer_service)
    config = {"configurable": {"deps": deps}}
    state = {
        "normalized_message": "There is garbage piling up and also a pothole on my street.",
        "response_language": "en",
    }

    result = agent_flow_node(state, config)

    assert result["insufficient_knowledge"] is False
    assert len(result["sources"]) == 1


def test_agent_flow_node_processes_categories_concurrently_not_sequentially():
    """BUG FIX (code review, efficiency): each category's retrieve()+generate() pair used to run
    one after another even though they're fully independent -- a 3-category question took
    roughly 3x a single category's wall-clock time. Proves the fix with a REAL timing measurement
    (not just call-count correctness, already covered by the other tests above): a message naming
    3 categories, each with a fake generate() that sleeps 0.3s, must complete in well under
    3 x 0.3s = 0.9s if the three are genuinely running concurrently."""
    import time

    chunk = ScoredChunk(
        chunk_id="c1", score=0.9,
        metadata={"content": "Some real content.", "source_id": "SRC", "verification_status": "VERIFIED"},
    )
    fake_retriever = Mock()
    fake_retriever.retrieve = Mock(return_value=RetrievalOutcome(results=[chunk]))

    def slow_generate(question, chunks, language, context_labels=None):
        time.sleep(0.3)
        return ("A real answer.", True, None)

    fake_answer_service = Mock()
    fake_answer_service.generate = Mock(side_effect=slow_generate)
    deps = _minimal_graph_deps(retriever=fake_retriever, answer_service=fake_answer_service)
    config = {"configurable": {"deps": deps}}
    state = {
        "normalized_message": "There is garbage piling up, a pothole on my street, and the streetlight is broken.",
        "response_language": "en",
    }

    start = time.perf_counter()
    result = agent_flow_node(state, config)
    elapsed = time.perf_counter() - start

    assert fake_answer_service.generate.call_count == 3
    # Sequential would be >=0.9s; concurrent should land close to one slot (0.3s) plus overhead.
    # A generous 0.7s ceiling comfortably distinguishes "ran concurrently" from "ran sequentially"
    # without being flaky on a loaded CI machine.
    assert elapsed < 0.7, f"expected concurrent execution (~0.3s), took {elapsed:.2f}s -- looks sequential"
    assert result["routed_to"] == "RAG_MULTI_CATEGORY"


def test_intent_node_wraps_existing_classifier():
    update = intent_node({"normalized_message": "I need a new electricity connection"}, config={})
    assert update["out_of_scope_service"] == "ELECTRICITY"
    assert update["service_category"] is None


# --- routing function unit tests (pure functions, no I/O) ---


def test_route_after_intent_status_first():
    assert _route_after_intent({"intent": QuestionIntent.TYPE_C_STATUS.value}) == "status_flow"


def test_route_after_intent_out_of_scope_before_location():
    state = {"intent": QuestionIntent.TYPE_B_SERVICE_INFO.value, "out_of_scope_service": "ELECTRICITY"}
    assert _route_after_intent(state) == "out_of_scope_flow"


def test_route_after_intent_else_goes_to_location():
    state = {"intent": QuestionIntent.TYPE_A_COMPLAINT.value, "out_of_scope_service": None}
    assert _route_after_intent(state) == "location_resolution"


def test_route_after_intent_capabilities():
    assert _route_after_intent({"intent": QuestionIntent.CAPABILITIES.value, "out_of_scope_service": None}) == "capabilities_flow"


def test_route_after_intent_greeting():
    """PRODUCTION ARCHITECTURE UPGRADE: a greeting must route to its own dedicated flow, never
    into location_resolution/complaint_flow (which would ask a complaint-shaped clarification
    question in response to "Hello")."""
    assert _route_after_intent({"intent": QuestionIntent.GREETING.value, "out_of_scope_service": None}) == "greeting_flow"


def test_route_after_intent_unclear():
    assert _route_after_intent({"intent": QuestionIntent.UNCLEAR.value, "out_of_scope_service": None}) == "unclear_flow"


def test_route_after_location_type_a_always_goes_to_complaint_flow():
    """Even with no location resolved yet -- complaint_flow decides for itself whether it still
    needs category/location (see nodes.py's complaint_flow_node)."""
    state = {"intent": QuestionIntent.TYPE_A_COMPLAINT.value, "location_city": None, "location_state": None}
    assert _route_after_location(state) == "complaint_flow"


def test_route_after_location_type_b_missing_location_asks_clarification():
    state = {"intent": QuestionIntent.TYPE_B_SERVICE_INFO.value, "location_city": None, "location_state": None}
    assert _route_after_location(state) == "clarification_flow"


def test_route_after_location_type_b_ambiguous_asks_clarification():
    state = {"intent": QuestionIntent.TYPE_B_SERVICE_INFO.value, "location_is_ambiguous": True}
    assert _route_after_location(state) == "clarification_flow"


def test_route_after_location_type_b_with_location_goes_to_rag():
    state = {"intent": QuestionIntent.TYPE_B_SERVICE_INFO.value, "location_city": "Mumbai", "location_state": "Maharashtra"}
    assert _route_after_location(state) == "rag_flow"


def test_route_after_location_multi_category_goes_to_agent_flow():
    """See docs/ask_sarthi_orchestration.md §17 and nodes.py's agent_flow_node -- a genuinely
    multi-category message (garbage AND a pothole named explicitly) routes to agent_flow instead
    of the single-category rag_flow, once location is resolved."""
    state = {
        "intent": QuestionIntent.TYPE_B_SERVICE_INFO.value,
        "location_city": "Mumbai", "location_state": "Maharashtra",
        "normalized_message": "There is garbage piling up and also a pothole on my street.",
    }
    assert _route_after_location(state) == "agent_flow"


def test_route_after_location_single_category_still_goes_to_rag_not_agent_flow():
    """Regression guard: an ordinary single-category question (even one whose category also
    happens to name a location-context word) must keep going to rag_flow, not agent_flow --
    the multi-category gate is deliberately conservative (see detect_multiple_categories())."""
    state = {
        "intent": QuestionIntent.TYPE_B_SERVICE_INFO.value,
        "location_city": "Mumbai", "location_state": "Maharashtra",
        "normalized_message": "The street light on Main Road near my house is broken.",
    }
    assert _route_after_location(state) == "rag_flow"


def test_route_after_location_type_a_complaint_never_goes_to_agent_flow_even_if_multi_category():
    """A TYPE_A complaint always goes to complaint_flow first, regardless of how many categories
    its text happens to name -- multi-issue complaint filing is a separate, unbuilt feature (see
    agent_flow_node's own docstring), not something the multi-category gate silently starts doing."""
    state = {
        "intent": QuestionIntent.TYPE_A_COMPLAINT.value,
        "location_city": "Mumbai", "location_state": "Maharashtra",
        "normalized_message": "There is garbage piling up and also a pothole on my street.",
    }
    assert _route_after_location(state) == "complaint_flow"


def test_route_after_complaint_needs_clarification():
    assert _route_after_complaint({"needs_clarification": True}) == "clarification_flow"


def test_route_after_complaint_done():
    assert _route_after_complaint({"needs_clarification": False}) == "response_generation"


# --- graph structure ---


def test_graph_compiles_with_expected_nodes():
    graph = build_graph()
    nodes = set(graph.get_graph().nodes.keys())
    expected = {
        "__start__", "input_processing", "language_detection", "intent_classification",
        "location_resolution", "complaint_flow", "rag_flow", "status_flow",
        "clarification_flow", "out_of_scope_flow", "capabilities_flow", "unclear_flow",
        # PRODUCTION ARCHITECTURE UPGRADE: "greeting_flow" added -- see graph.py's own module
        # docstring/route_after_intent for the new GREETING branch this exhaustive set must stay
        # in sync with.
        "greeting_flow",
        # Supervisor/multi-agent node (see docs/ask_sarthi_orchestration.md §17 and nodes.py's
        # agent_flow_node) -- routed to from _route_after_location for a genuinely multi-category
        # question, this exhaustive set must stay in sync with that addition too.
        "agent_flow",
        "response_generation", "__end__",
    }
    assert nodes == expected


# --- multi-turn clarification: the exact scenario from the orchestration spec's own example ---


def test_multi_turn_complaint_filing_category_then_location(client, monkeypatch, db_session, make_citizen, make_worker):
    """TURN 1: 'I want to file a complaint.' -> asks for the issue.
    TURN 2: 'Streetlight.' -> asks for the location (category now known from THIS turn alone).
    TURN 3: 'Use my current location.' (+ explicit location_text, standing in for a real
    'use current location' UI action) -> resolves, category recovered from TURN 2's history --
    P0 SAFETY FIX (production-safety audit): this no longer files the complaint immediately; it
    shows a confirmation summary instead, with complaint_id still null.
    TURN 4: 'Yes, submit it.' -> the complaint is actually filed only now, using the category
    recovered from TURN 2 and the location recovered from TURN 3.
    Verifies the graph "does not lose previously collected information" (spec's own wording)
    across four separate, stateless HTTP requests -- exactly how this codebase's multi-turn
    conversation support already worked pre-graph (client-resent conversation_history, see
    docs/ask_sarthi_rag_architecture.md's "why no server-side conversation store" note) -- the
    graph adds routing/state structure on top, not a new persistence mechanism.
    """
    _install_real_service(monkeypatch)
    make_worker(phone="9100099002", ward="Mohali")
    token, _ = make_citizen(phone="9100000030")

    turn1 = _ask(client, token, "I want to file a complaint.")
    assert turn1.status_code == 200
    body1 = turn1.json()
    assert body1["follow_up_required"] is True
    assert body1["follow_up_question"] == "What issue would you like to report?"
    assert body1.get("complaint_id") is None

    history = [
        ConversationTurn(role="user", content="I want to file a complaint.").model_dump(),
        ConversationTurn(role="assistant", content=body1["answer"]).model_dump(),
    ]
    turn2 = _ask(client, token, "Streetlight.", conversation_history=history)
    assert turn2.status_code == 200
    body2 = turn2.json()
    assert body2["follow_up_required"] is True
    assert body2["follow_up_question"] == "What is the location?"
    assert body2.get("complaint_id") is None

    history.append(ConversationTurn(role="user", content="Streetlight.").model_dump())
    history.append(ConversationTurn(role="assistant", content=body2["answer"]).model_dump())
    turn3 = _ask(client, token, "Use my current location.", conversation_history=history, location_text="Mohali")
    assert turn3.status_code == 200
    body3 = turn3.json()
    assert body3["routed_to"] == "NONE_AWAITING_CONFIRMATION"
    assert body3["service_category"] == "STREETLIGHTS"  # recovered from turn 2, not lost
    assert body3.get("complaint_id") is None
    assert db_session().query(Complaint).count() == 0

    history.append(ConversationTurn(role="user", content="Use my current location.").model_dump())
    history.append(ConversationTurn(role="assistant", content=body3["answer"]).model_dump())
    turn4 = _ask(client, token, "Yes, submit it.", conversation_history=history)
    assert turn4.status_code == 200
    body4 = turn4.json()
    assert body4["routed_to"] == "COMPLAINT_CREATED"
    assert body4["service_category"] == "STREETLIGHTS"
    assert body4["complaint_id"] is not None

    db = db_session()
    complaint = db.query(Complaint).filter(Complaint.id == body4["complaint_id"]).first()
    assert complaint is not None
    assert complaint.ward == "Mohali"
    assert complaint.status == "assigned"
    db.close()


def test_category_recovered_across_turns_even_when_current_turn_has_none():
    """Unit-level check of the exact carryover helper used by complaint_flow_node (see
    _recover_category_from_history in nodes.py) -- isolates the logic from the full HTTP path."""
    from backend.schemas.rag_knowledge import ServiceCategory
    from backend.services.orchestration.nodes import _recover_category_from_history

    state = {
        "conversation_history": [
            {"role": "user", "content": "I want to file a complaint."},
            {"role": "assistant", "content": "What issue would you like to report?"},
            {"role": "user", "content": "Streetlight."},
        ]
    }
    assert _recover_category_from_history(state) == ServiceCategory.STREETLIGHTS


def test_category_recovery_returns_none_when_history_has_no_category():
    from backend.services.orchestration.nodes import _recover_category_from_history

    state = {"conversation_history": [{"role": "user", "content": "I want to file a complaint."}]}
    assert _recover_category_from_history(state) is None


def test_category_recovery_does_not_reach_past_an_unrelated_completed_exchange():
    """Live-reported: an abandoned (never confirmed/cancelled) "Garbage" complaint draft was left
    in history; a completely unrelated, fully-answered exchange happened afterward (a Nagpur
    streetlight civic-info question); the citizen then asked a vague, unrelated message again --
    and the old "Garbage" category got silently reattached to it, reaching straight past the
    unrelated exchange in between. Fixed by stopping the backward scan at the first assistant turn
    that wasn't itself part of an open complaint flow (see _turn_is_open_complaint_flow)."""
    from backend.services.orchestration.nodes import _recover_category_from_history

    state = {
        "conversation_history": [
            {"role": "user", "content": "is this report is true"},
            {"role": "assistant", "content": "What issue would you like to report?", "complaint_workflow_state": "DRAFT"},
            {"role": "user", "content": "Garbage"},
            {
                "role": "assistant",
                "content": 'Your complaint would be about Waste Sanitation...: "is this report is true". Would you like me to submit this complaint?',
                "complaint_workflow_state": "AWAITING_CONFIRMATION",
            },
            {"role": "user", "content": "how do I report a broken street light in Maharashtra"},
            {"role": "assistant", "content": "Which city are you asking about — Mumbai, Nagpur?", "complaint_workflow_state": "DRAFT"},
            {"role": "user", "content": "Nagpur"},
            {
                "role": "assistant",
                "content": "To report a broken streetlight in Nagpur, contact the Electrical Department.",
                "complaint_workflow_state": None,
            },
        ]
    }
    assert _recover_category_from_history(state) is None


def test_category_recovery_still_works_across_a_genuinely_open_multi_turn_flow():
    """Regression guard: the stopping point must not break the legitimate case this whole
    mechanism exists for -- a category named in turn 1, still-open location clarification in
    between, current turn answering that same clarification."""
    from backend.schemas.rag_knowledge import ServiceCategory
    from backend.services.orchestration.nodes import _recover_category_from_history

    state = {
        "conversation_history": [
            {"role": "user", "content": "Streetlight."},
            {"role": "assistant", "content": "What is the location?", "complaint_workflow_state": "DRAFT"},
        ]
    }
    assert _recover_category_from_history(state) == ServiceCategory.STREETLIGHTS


def test_category_recovery_does_not_reach_past_an_already_filed_complaint():
    """Live-reported (a THIRD case, found after the first two fixes shipped): a citizen filed a
    real streetlight complaint (category+location resolved, confirmed, a real complaint created),
    then started a brand-new, different complaint with "I want to file a complaint." (no category
    named at all) -- and the scan still answered "Streetlights" again, because that message is
    TYPE_A_MAYBE, not one of `_TOPIC_BOUNDARY_INTENTS`, so the earlier user-message-only boundary
    never fired for it. A successfully filed complaint is its own kind of "moved on", even when
    every turn in it was complaint-shaped start to finish -- see _turn_closes_a_filed_complaint."""
    from backend.services.orchestration.nodes import _recover_category_from_history

    state = {
        "conversation_history": [
            {"role": "user", "content": "There's a streetlight not working near my home in Ahmedabad."},
            {
                "role": "assistant",
                "content": 'Your complaint would be about Streetlights in "Ward 11 — Navrangpura, Ahmedabad": '
                '... Would you like me to submit this complaint?',
            },
            {"role": "user", "content": "yes"},
            {"role": "assistant", "content": "Your Streetlights complaint has been filed (complaint #19) and assigned to a worker."},
        ]
    }
    assert _recover_category_from_history(state) is None


# --- location clarification: explicit-but-unrecognized location gets an honest, distinct message ---


def test_clarification_gives_honest_message_when_explicit_location_unresolved(client, monkeypatch, make_citizen):
    """Live-reported bug: a citizen picks an explicit location (the "Select location"/"Use
    current location" UI passes this through as location_text) that isn't a real place the
    gazetteer recognizes -- e.g. a leftover test ward name with no real city in it. Previously
    this fell through to the exact same "What is the location?" first-ask question, which reads
    to the citizen as the assistant ignoring what they just did (a stuck-loop feeling reported
    live). Must now get a distinct, honest message instead."""
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9100000040")

    resp = _ask(
        client, token, "How do I report a garbage collection issue?",
        location_text="Notif Test Ward 1786400621684 (unrelated)",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["follow_up_required"] is True
    answer_lower = body["answer"].lower()
    assert "couldn't recognize" in answer_lower
    assert answer_lower != "what is the location? this helps me give you the correct local information."


def test_clarification_default_message_unchanged_when_no_location_given_at_all(client, monkeypatch, make_citizen):
    """Regression check: a citizen who has given NO location signal at all (never picked
    anything, nothing in the message/history/profile) must still get the original, unchanged
    first-ask question -- the new honest message is only for the "picked something real-looking
    but unrecognized" case, never a general replacement for this one."""
    _install_real_service(monkeypatch)
    token, _ = make_citizen(phone="9100000041")

    resp = _ask(client, token, "How do I report a garbage collection issue?")
    assert resp.status_code == 200
    body = resp.json()
    assert body["follow_up_required"] is True
    assert body["answer"] == "What is the location? This helps me give you the correct local information."


# --- error handling: a node exception must not silently produce a fabricated answer ---


def test_complaint_agent_failure_returns_honest_error_not_a_fake_complaint_id(client, monkeypatch, make_citizen, make_worker):
    fake_answers = Mock()
    fake_answers.generate = lambda q, chunks, lang, context_labels=None: ("x", False, None)

    class _RaisingComplaintAgent:
        def create_complaint(self, **kwargs):
            raise ValueError("simulated complaint-agent failure")

    service = _real_ask_sarthi_service(complaint_agent=_RaisingComplaintAgent())
    monkeypatch.setattr(ask_sarthi_module, "_service", service)

    make_worker(phone="9100099031", ward="Mohali")
    token, _ = make_citizen(phone="9100000031")
    resp = _ask(client, token, "Street light not working in Mohali.", location_text="Mohali")
    assert resp.status_code == 200
    body = resp.json()
    assert body["routed_to"] == "NONE_AWAITING_CONFIRMATION"  # P0 fix: confirmation first
    assert body.get("complaint_id") is None

    history = [
        ConversationTurn(role="user", content="Street light not working in Mohali.").model_dump(),
        ConversationTurn(role="assistant", content=body["answer"]).model_dump(),
    ]
    confirm_resp = _ask(client, token, "Yes, submit it.", conversation_history=history)
    assert confirm_resp.status_code == 200  # handled gracefully, not a 500
    confirm_body = confirm_resp.json()
    assert confirm_body["routed_to"] == "COMPLAINT_CREATION_FAILED"
    assert confirm_body.get("complaint_id") is None
    assert "couldn't file" in confirm_body["answer"].lower()
