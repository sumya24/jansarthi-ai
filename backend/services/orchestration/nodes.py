"""LangGraph node functions for the Ask Sarthi orchestrator.

Every node is a thin adapter: it reads what it needs from `GraphState`/the injected deps and
calls into an EXISTING service (`classify()`, `LocationExtractor`, `LocationResolver`,
`RagRetriever`, `AnswerGenerationService`, `ComplaintAgent`, `assign_next_worker`, `Complaint` DB
queries) -- no node contains business logic that didn't already exist somewhere in this codebase
before this phase. See `graph.py`'s module docstring for the full node/edge list, and
`docs/ask_janmitra_orchestration.md` for the architectural reasoning.

**Deliberate behavior change, confirmed with the user before implementing (see git history /
session transcript)**: previously, a TYPE_A_COMPLAINT-classified question ("Street light not
working near me") was answered by RAG, the same as a TYPE_B question -- Ask Sarthi could not
yet act on a complaint-shaped message, only describe relevant civic-service information about it.
This phase closes that gap: TYPE_A_COMPLAINT now routes to `complaint_flow_node`, which files a
real complaint via the existing `ComplaintAgent`/`assign_next_worker` services and returns a
complaint ID -- matching this phase's own spec, whose worked complaint-flow example ("Streetlight
near my home is not working.") is itself a TYPE_A-shaped sentence. TYPE_B_SERVICE_INFO ("who do I
contact for X", "how do I apply for Y") still routes to `rag_flow_node`, unchanged. This
necessarily changes several previously-tested TYPE_A cases -- see tests/test_ask_janmitra.py's
updated assertions and the final report for the full list.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy.orm import Session

from backend.models import User
from backend.repositories import complaint_repository, evidence_repository
from backend.schemas.rag_knowledge import ServiceCategory
from backend.services.answer_generation_service import AnswerGenerationService
from backend.services.assignment_service import assign_next_worker
from backend.services.complaint_agent import ComplaintAgent
from backend.services.evidence_service import SavedFile
from backend.services.intent_classifier import (
    QuestionIntent,
    _looks_like_question,
    classify,
    is_explicit_cancellation,
    is_explicit_confirmation,
)
from backend.services.location_extractor import LocationExtractor, LocationResolution, known_aliases_for_city
from backend.services.location_resolver import LocationResolver, ResolvedLocation
from backend.services.observability import tracing
from backend.services.orchestration.state import GraphState
from backend.services.rag_answer_cache import get_cached_answer, store_answer
from backend.services.rag_retriever import RagRetriever, chunk_context_label
from backend.services.sarvam_client import AIServiceError
from backend.services.translation_service import TranslationService

logger = logging.getLogger(__name__)

_COMPLAINT_NUMBER_PATTERN = re.compile(r"(?:complaint|complain)\s*#?\s*(\d+)|#(\d+)|\b(\d{2,6})\b", re.IGNORECASE)

_OUT_OF_SCOPE_TOPIC_NAMES = {
    "ELECTRICITY": "electricity connection/meter",
    # Only reached for a bare "new connection" with no utility named -- "new water connection"/
    # "new sewerage connection" now have real coverage for some cities and route to RAG instead
    # (see intent_classifier.py's _NEW_CONNECTION_KEYWORDS docstring for why this list shrank).
    "NEW_SERVICE_CONNECTION": "new utility connections without a specified service (please say which service — e.g. water or sewerage connection)",
}

# Human-readable category clarification options -- matches the spec's own §13 wording. The value
# the user actually picks/types comes back as a normal next-turn message and is reclassified by
# the existing `classify()` the same as any other input; these labels are chosen to contain the
# same keywords `_CATEGORY_KEYWORDS` (intent_classifier.py) already matches, so a straight
# pass-through of the picked label continues to classify correctly with zero new code.
_CATEGORY_CLARIFICATION_OPTIONS = ["Garbage", "Water", "Road", "Streetlight", "Other"]
_LOCATION_CLARIFICATION_OPTIONS = ["Use current location", "Enter location", "Select location"]


@dataclass
class GraphDeps:
    """Static, shared-across-requests dependencies -- built once (expensive: the embedding model
    load, the Chroma collection open) and reused for every graph invocation, matching this
    codebase's existing "construct once in the service constructor" pattern (see
    ask_janmitra_service.py, which this class's fields were lifted from unchanged)."""

    retriever: RagRetriever
    location_extractor: LocationExtractor
    answer_service: AnswerGenerationService
    complaint_agent: ComplaintAgent
    location_resolver: LocationResolver
    # Optional (defaults to None, matching RequestContext.image_saved's own optional-field
    # precedent below) so existing tests that build GraphDeps with only the five fields above
    # keep working unchanged. When unset, `_localize()` just returns text untranslated -- the
    # same honest degradation as a translation call that fails.
    translation_service: TranslationService | None = None


@dataclass
class RequestContext:
    """Per-request context: everything that legitimately differs between two invocations of the
    same compiled graph. Passed via LangGraph's `config["configurable"]`, not folded into
    `GraphState` -- this is request plumbing (a DB session, the authenticated user, raw GPS
    coordinates), not conversation data that the graph itself reasons about or that should be
    logged/serialized as part of the graph's own state."""

    db: Session
    user: User
    latitude: float | None
    longitude: float | None
    location_text: str | None
    # Set only by ask_janmitra_service.ask_with_image() -- the already-validated-and-written
    # image file (see backend/services/evidence_service.py), if one was attached. Request
    # plumbing, not conversation data the graph reasons about (same rationale as latitude/
    # longitude above) -- complaint_flow_node reads this to attach real complaint evidence.
    image_saved: SavedFile | None = None


def _deps(config: RunnableConfig) -> GraphDeps:
    return config["configurable"]["deps"]


def _ctx(config: RunnableConfig) -> RequestContext:
    return config["configurable"]["ctx"]


def _localize(text: str, state: GraphState, config: RunnableConfig) -> str:
    """Translates a hardcoded English response (clarification questions, out-of-scope notices)
    into the citizen's `response_language`, via the existing `TranslationService`/`SarvamClient`
    (same service `complaint_agent.py`/`ask_janmitra_service.py` already use for worker-facing
    complaint translation -- no new client, no new logic). Unlike `rag_flow_node`'s answers,
    these strings never went through an LLM prompted to answer in the target language in the
    first place, so without this they stay in English even in a fully Marathi/Hindi/etc.
    conversation -- confirmed via a live Marathi voice-assistant session where the transcript and
    every UI label were in Marathi but this exact clarification text came back in English.

    English is a no-op (skips a needless network call on the common case). A translation failure
    degrades to the original English text -- same honest fallback `AnswerGenerationService` and
    the voice flow's own TTS step already use -- never blocks the response."""
    language = state.get("response_language") or "en"
    if language == "en":
        return text
    deps = _deps(config)
    if deps.translation_service is None:
        return text
    try:
        return deps.translation_service.to_language(text, language)
    except AIServiceError as exc:
        logger.warning("Ask Sarthi: localizing response text to %s failed, keeping English: %s", language, exc)
        return text


def _trace_root(config: RunnableConfig):
    """The current request's LangSmith root span (see graph.py's `run_graph()`), or `None` if
    tracing is disabled/unavailable. `.get(...)`, not `[...]` -- unlike `deps`/`ctx`, this key is
    optional plumbing a direct unit test of a node function is not required to provide (every
    `tracing.*` call already treats `None` as a no-op, see that module)."""
    return config["configurable"].get("trace_root")


# ------------------------------------------------------------------
# input_processing / language -- both effectively identity nodes; kept as separate graph nodes
# (rather than folded together) because the spec calls them out as distinct architectural stages,
# and because a real normalization/detection step has exactly one place to be added later without
# touching any other node.
# ------------------------------------------------------------------


def input_processing_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Normalizes whitespace only -- never alters wording/meaning. The original message is kept
    untouched in `user_message`; `normalized_message` is a separate field so nothing downstream
    that wants the citizen's exact original words (e.g. `Complaint.original_text`) ever reads the
    normalized copy by accident.

    Also where an attached image (captioned upstream, see state.py's `image_description`) is
    folded into the text every downstream node already consumes -- unless there's no text at all,
    in which case this sets `clarification_reason="image_no_text"` so the graph routes straight to
    a real clarification question instead of guessing intent from an image alone (see
    `_route_after_language` in graph.py and `clarification_flow_node` below)."""
    message = state.get("user_message", "").strip()
    has_image = bool(state.get("has_image"))
    image_description = state.get("image_description")

    if not message and has_image:
        return {
            "normalized_message": "",
            "input_type": state.get("input_type") or "text",
            "clarification_reason": "image_no_text",
        }

    if image_description:
        message = f"{message}\n\n[Attached photo shows: {image_description}]"

    return {
        "normalized_message": message,
        "input_type": state.get("input_type") or "text",
    }


def language_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Identity node: `original_language` already arrives validated (`AskJanMitraRequest.language`
    is checked against `settings.SUPPORTED_LANGUAGES` by the route before the graph ever runs) --
    re-detecting it would just re-derive a value the caller already gave us authoritatively. Real
    translation/normalization already happens inside the flow nodes that need it (e.g.
    `AnswerGenerationService` receives the target language name directly) -- this node's only job
    is to seed `response_language` from the request so every downstream node can read one
    consistent field name."""
    return {"response_language": state.get("original_language") or "en"}


# ------------------------------------------------------------------
# intent
# ------------------------------------------------------------------


_MAX_CONTINUATION_REPLY_WORDS = 6

# RED-TEAM / FINAL VALIDATION FIX: the fixed marker text (English, plus real Sarvam-translated
# hi/mr/or/gu/bn equivalents -- see the per-group comments below for provenance) of every
# complaint-flow question Sarthi itself asks. Grouped by WHICH question each marker recognizes --
# category clarification, location clarification, the confirmation prompt, and the
# intent-ambiguous clarification -- so each group can be reused on its own (the confirmation-
# prompt group alone is what `_awaiting_confirmation` needs; all four together are what
# `_last_turn_invites_complaint_reply` needs). Consolidated from two previously-separate marker
# tuples that duplicated the confirmation-prompt fragments in both places -- see this module's own
# safety-review notes for why that duplication was flagged.
#
# `_last_turn_invites_complaint_reply` uses ALL FOUR groups together to decide whether a short,
# non-question reply ("okay", "my name is Sumit", "solve 25 * 4") should be treated as a
# continuation of an ACTIVE complaint flow at all -- see intent_node's own docstring for the
# live-reproduced bug this closes: without this check, a short reply was promoted into
# TYPE_A_COMPLAINT whenever *any* conversation history existed, regardless of what Sarthi had
# actually just asked, and complaint_flow_node's own category/text recovery would then happily
# pull from an unrelated EARLIER turn -- filing a real complaint whose "description" was, in one
# live-reproduced case, a citizen's own informational question ("what is the procedure for garbage
# collection?").
_CATEGORY_CLARIFICATION_MARKERS = (
    "what issue would you like to report",
    "समस्या की रिपोर्ट करना चाहेंगी",  # hi
    "मुद्दा नोंदवू इच्छिता",  # mr
    "ସମସ୍ୟା ବିଷୟରେ ଅଭିଯୋଗ କରିବାକୁ ଚାହାଁନ୍ତି",  # or
    "કયો મુદ્દો જાણવા માંગો",  # gu
    "কোন সমস্যাটি জানাতে চান",  # bn
)
_LOCATION_CLARIFICATION_MARKERS = (
    "what is the location",
    "स्थान क्या है",  # hi
    "ठिकाण काय आहे",  # mr
    "ସ୍ଥାନଟି କେଉଁଠାରେ",  # or
    "સ્થળ ક્યાં છે",  # gu
    "অবস্থানটি কী",  # bn
)
# FINAL VALIDATION FIX (production-safety red-team pass): these markers are matched against
# `conversation_history`, which now carries genuinely `_localize()`-translated text once a working
# Sarvam key is configured -- previously invisible because translation was failing (quota
# exhausted) and silently falling back to English, which accidentally kept the English marker
# matching. With translation actually working, a Hindi/Marathi citizen's legitimate short reply
# ("हाँ, सबमिट करो") stopped being recognized as continuing the complaint flow at all,
# live-reproduced losing the pending draft entirely (not merely an extra round-trip). Hindi/
# Marathi fragments are extracted directly from REAL Sarvam translation output captured live (see
# this fix's own validation report), not composed from memory; the or/gu/bn confirmation-prompt
# fragments are from a REAL live draft response, category/location/intent-ambiguous or/gu/bn
# fragments from a real standalone Sarvam translation call -- same verification standard, same
# caveat: LLM translation is not a fixed dictionary, so exact phrasing can still drift between
# calls. A missed match still only ever fails SAFE (falls to UNCLEAR/re-asks, never skips
# confirmation -- see this module's own top-of-file safety principle).
_CONFIRMATION_PROMPT_MARKER = "would you like me to submit this complaint"
_CONFIRMATION_PROMPT_MARKERS = (
    _CONFIRMATION_PROMPT_MARKER,
    "यह शिकायत दर्ज करूँ",  # hi
    "ही तक्रार मी दाखल करावी",  # mr
    "ଏହି ଅଭିଯୋଗ ଦାଖଲ କରେ",  # or
    "આ ફરિયાદ સબમિટ કરું",  # gu
    "এই অভিযোগটি জমা দিই",  # bn
)
_INTENT_AMBIGUOUS_CLARIFICATION_MARKERS = (
    "are you reporting a problem",
    "समस्या की रिपोर्ट कर रही हैं",  # hi
    "समस्येची तक्रार करत आहात",  # mr
    "ସମସ୍ୟା ବିଷୟରେ ଜଣାଉଛନ୍ତି",  # or
    "સમસ્યાની જાણ કરી રહ્યા છો",  # gu
    "কোনও সমস্যার রিপোর্ট করছেন",  # bn
)
_COMPLAINT_FLOW_PROMPT_MARKERS = (
    _CATEGORY_CLARIFICATION_MARKERS
    + _LOCATION_CLARIFICATION_MARKERS
    + _CONFIRMATION_PROMPT_MARKERS
    + _INTENT_AMBIGUOUS_CLARIFICATION_MARKERS
)


def _last_assistant_turn_matches(conversation_history: list[dict] | None, markers: tuple[str, ...]) -> bool:
    """Shared shape behind "is Sarthi currently waiting on a reply to one of its own complaint-flow
    questions" (`_last_turn_invites_complaint_reply`, matched against all four marker groups
    above) and "...specifically its confirmation prompt" (`_awaiting_confirmation`, matched
    against `_CONFIRMATION_PROMPT_MARKERS` alone) -- true only when the LAST turn in
    `conversation_history` is an assistant turn whose text contains one of `markers`."""
    if not conversation_history:
        return False
    last = conversation_history[-1]
    if last.get("role") != "assistant":
        return False
    content = (last.get("content") or "").lower()
    return any(marker in content for marker in markers)


def _last_assistant_turn_state(conversation_history: list[dict] | None) -> str | None:
    """BUG FIX (live Marathi validation): the explicit `complaint_workflow_state` a compliant
    caller echoes back on the last assistant turn (see `ConversationTurn.complaint_workflow_state`
    and `AskJanMitraResponse.complaint_workflow_state`'s own docstrings for the full round-trip).
    Returns None when the last turn isn't an assistant turn, or the field is absent/None -- either
    because this turn genuinely wasn't complaint-shaped, or because the caller predates this field
    -- both cases fall back to the existing marker-text matching in the callers below, so nothing
    that worked before regresses."""
    if not conversation_history:
        return None
    last = conversation_history[-1]
    if last.get("role") != "assistant":
        return None
    return last.get("complaint_workflow_state")


def _last_turn_invites_complaint_reply(conversation_history: list[dict] | None) -> bool:
    explicit_state = _last_assistant_turn_state(conversation_history)
    if explicit_state is not None:
        # DRAFT: category and/or location still being clarified. AWAITING_CONFIRMATION: the
        # confirmation prompt itself. Both invite a reply that continues the SAME complaint flow;
        # CONFIRMED/CANCELLED are terminal -- the next message starts fresh (see
        # GraphState.complaint_workflow_state's own docstring for the full value list).
        return explicit_state in ("DRAFT", "AWAITING_CONFIRMATION")
    return _last_assistant_turn_matches(conversation_history, _COMPLAINT_FLOW_PROMPT_MARKERS)


def intent_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Wraps the EXISTING `intent_classifier.classify()` -- no reimplementation, no new keyword
    lists. See this module's docstring for how the `QuestionIntent` values map onto this graph's
    routes."""
    text = state.get("normalized_message") or state.get("user_message", "")
    result = classify(text)
    intent = result.intent
    ctx = config.get("configurable", {}).get("ctx")
    has_gps = bool(ctx and ctx.latitude is not None and ctx.longitude is not None)
    history = state.get("conversation_history") or []
    # P0 SAFETY FIX: this override used to fire on "ANY conversation_history exists", regardless
    # of what the current message actually said -- a second, independently discovered danger
    # (see the production-safety audit's Part 8 finding): a genuinely unrelated mid-flow message
    # with zero keyword signal ("What's your favorite color?") got silently forced into
    # TYPE_A_COMPLAINT purely because *some* history existed, continuing a complaint flow the
    # citizen never meant to continue. Narrowed to only fire for a short, non-question-shaped
    # reply -- exactly the shape of a genuine continuation ("Streetlight.", "Use my current
    # location.", "Pune", "yes, submit it"), never a genuine off-topic question/statement, which
    # is reliably longer and/or interrogative-shaped (see intent_classifier.py's
    # _looks_like_question). This is a *safe* narrowing, not a precise one: the failure mode of
    # narrowing too far is an extra "I didn't understand that" reply (unclear_flow_node), never a
    # wrongly-continued complaint -- see this module's own top-of-file safety principle.
    is_short_non_question_reply = bool(text) and len(text.split()) <= _MAX_CONTINUATION_REPLY_WORDS and not _looks_like_question(text)
    # An explicit confirmation/cancellation reply ("Yes, submit it.") is ALSO always treated as a
    # continuation, even past the short-reply word cap -- needed for the image/voice endpoints,
    # where a re-attached photo's caption gets folded into `text` (see input_processing_node) and
    # can push a genuinely short spoken/typed reply well past `_MAX_CONTINUATION_REPLY_WORDS`
    # (e.g. "Yes, submit it." + "[Attached photo shows: ...]"). Safe to check unconditionally
    # here (regardless of length) because `is_explicit_confirmation`/`is_explicit_cancellation`
    # are already narrow, deterministic checks -- see intent_classifier.py's own docstring for
    # why using them as an extra continuation signal introduces no new risk.
    is_continuation_reply = is_short_non_question_reply or is_explicit_confirmation(text) or is_explicit_cancellation(text)
    # RED-TEAM SAFETY FIX: this override previously fired whenever *any* conversation_history
    # existed, regardless of what Sarthi had actually just asked -- live-reproduced as a real bug
    # by red-team testing: "my name is Sumit" (after a plain greeting reply) and "okay"/"fine"/
    # "continue" (after a CAPABILITIES answer) were all promoted into TYPE_A_COMPLAINT purely
    # because *some* history existed, and complaint_flow_node's own category/text recovery then
    # pulled from an unrelated EARLIER turn -- in one live-reproduced case, filing a real
    # complaint whose stored description was the citizen's own informational question ("what is
    # the procedure for garbage collection?"), not anything resembling a complaint. Narrowed
    # further here to require that the immediately preceding assistant turn was ACTUALLY a
    # complaint-flow question (category/location/confirmation/intent-ambiguous clarification --
    # see `_last_turn_invites_complaint_reply`), not merely that some history existed.
    last_turn_invites_reply = _last_turn_invites_complaint_reply(history)
    if intent == QuestionIntent.UNCLEAR and (
        (last_turn_invites_reply and is_continuation_reply) or state.get("has_image") or has_gps
    ):
        # classify() is deliberately a pure, single-turn, text-only function (see its own
        # docstring) -- it has no way to see that a short reply like "Streetlight." or "Use my
        # current location." carries real meaning from the conversation so far, that a vague
        # caption-only message has an attached photo behind it, or that "Use my current
        # location." arrived with real GPS coordinates attached. Zero keyword signal in THIS
        # turn's text alone isn't the same as the request being genuinely unclear in these three
        # cases, so this falls back to TYPE_A_COMPLAINT -- handing off to machinery that already
        # exists for exactly this: complaint_flow_node's _recover_category_from_history for the
        # conversation case, clarification_flow_node's _image_context_prefix for the image case,
        # and location_node's existing GPS-resolution path for the coordinates case. A real
        # regression this exact override was added for: without it, EVERY reply in a multi-turn
        # complaint conversation whose own text has no keyword ("Use my current location.") broke
        # out of the flow entirely (caught by
        # test_multi_turn_complaint_filing_category_then_location), and a GPS-only "use current
        # location" message never reached location_resolution at all (caught by
        # test_scenario_5_use_current_location_resolves_via_gps). A genuinely fresh, standalone,
        # no-context, no-image, no-GPS message (e.g. "What is my name?") has none of these and
        # correctly stays UNCLEAR.
        intent = QuestionIntent.TYPE_A_COMPLAINT
    elif intent == QuestionIntent.TYPE_A_MAYBE and last_turn_invites_reply and is_continuation_reply:
        # Same reasoning as the UNCLEAR case just above, for TYPE_A_MAYBE's own bare-category-or-
        # verb-with-no-other-signal case (see intent_classifier.py's QuestionIntent.TYPE_A_MAYBE
        # docstring): a short, non-question reply mid-conversation ("Streetlight.") is a genuine
        # answer to Sarthi's own "What issue would you like to report?" question, not a fresh,
        # ambiguous, out-of-context fragment -- classify() has no way to see that context on its
        # own. A real regression this exact branch was added for: without it, turn 2 of the
        # spec's own multi-turn worked example ("I want to file a complaint." -> "Streetlight.")
        # broke, asking "report a problem or get information?" about a category the citizen had
        # already unambiguously just named in direct reply to a question asking exactly that.
        # This narrowing is deliberately identical in shape to the UNCLEAR branch above (short +
        # non-question + history exists) -- the P0 bug this whole fix closes only ever involved
        # LONG, QUESTION-shaped messages ("What is the procedure for garbage collection
        # complaints in Pune?"), which this condition never matches (see _looks_like_question).
        intent = QuestionIntent.TYPE_A_COMPLAINT
    return {
        "intent": intent.value,
        "service_category": result.service_category.value if result.service_category else None,
        "out_of_scope_service": result.out_of_scope_service,
        "requests_new_connection": result.requests_new_connection,
        **({"clarification_reason": "intent_ambiguous"} if intent == QuestionIntent.TYPE_A_MAYBE else {}),
    }


# ------------------------------------------------------------------
# location
# ------------------------------------------------------------------


def _resolve_location(state: GraphState, config: RunnableConfig) -> LocationResolution:
    """Same priority order as the pre-graph `AskJanMitraService._resolve_location()` this
    replaces, plus one addition: explicit `location_text` > location named in the message text >
    GPS > a location mentioned in `conversation_history` > the citizen's own registered ward.
    Reuses `LocationExtractor` exactly as before -- this function only sequences the existing
    calls, it performs no resolution itself.

    The last step (citizen's own ward) is a deliberate final fallback, not a new resolution
    method: a citizen's `ward` free text (set once at signup, see models.User.ward) almost always
    contains their city name (e.g. "Ward 22 -- Kothrud, Pune"), so running it through the same
    `resolve_from_text` used for message text/conversation history lets a logged-in citizen who
    doesn't name a place get an answer scoped to where they actually live, instead of an
    unnecessary "no information for this area" when every earlier signal was silent. Workers and
    admins are unaffected in practice (their `ward` is an operational area string, not used here
    any differently) -- this only changes behavior for QUESTION_RAG intents where nothing else
    resolved a location."""
    deps = _deps(config)
    ctx = _ctx(config)
    extractor = deps.location_extractor

    if ctx.location_text:
        resolved = extractor.resolve_from_text(ctx.location_text)
        if resolved.city or resolved.state or resolved.is_ambiguous:
            return resolved

    text = state.get("normalized_message") or state.get("user_message", "")
    resolved = extractor.resolve_from_text(text)
    if resolved.city or resolved.state or resolved.is_ambiguous:
        return resolved

    if ctx.latitude is not None and ctx.longitude is not None:
        resolved = extractor.resolve_from_coordinates(ctx.latitude, ctx.longitude)
        if resolved.city or resolved.state:
            return resolved

    for turn in reversed(state.get("conversation_history", [])):
        if turn.get("role") != "user":
            continue
        resolved = extractor.resolve_from_text(turn.get("content", ""))
        if resolved.city or resolved.state or resolved.is_ambiguous:
            resolved.source = "conversation_history"
            return resolved

    if ctx.user.ward:
        resolved = extractor.resolve_from_text(ctx.user.ward)
        if resolved.city or resolved.state or resolved.is_ambiguous:
            resolved.source = "citizen_home_ward"
            return resolved

    return LocationResolution(source="none")


def location_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    resolution = _resolve_location(state, config)
    ctx = _ctx(config)
    # True only when the citizen gave an EXPLICIT location signal (the "Use current location"/
    # "Select location" UI, or typed free text passed as ctx.location_text) that still didn't
    # resolve to anywhere recognizable -- distinct from never having given one at all. See
    # clarification_flow_node's default branch for why this distinction has its own message.
    explicit_signal_unresolved = bool(ctx.location_text) and not (
        resolution.city or resolution.state or resolution.is_ambiguous
    )
    return {
        "location_city": resolution.city,
        "location_state": resolution.state,
        "location_source": resolution.source,
        "location_is_ambiguous": resolution.is_ambiguous,
        "location_ambiguous_candidates": resolution.ambiguous_candidates,
        "location_explicit_signal_unresolved": explicit_signal_unresolved,
    }


# ------------------------------------------------------------------
# out_of_scope flow
# ------------------------------------------------------------------


def out_of_scope_flow_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """A known-but-unsupported service (electricity, new-connection) was detected by the
    classifier -- an honest "don't have that" response, never a fabricated answer or a fallback
    to an unrelated RAG record (see intent_classifier.py's module docstring for the exact false
    positive this prevents)."""
    topic = _OUT_OF_SCOPE_TOPIC_NAMES.get(
        state.get("out_of_scope_service") or "", (state.get("out_of_scope_service") or "").lower()
    )
    text = (
        f"I don't currently have reliable information for {topic} in JanSarthi. "
        f"This may be available in a future update."
    )
    return {
        "response_text": _localize(text, state, config),
        "routed_to": "NONE_OUT_OF_SCOPE",
        "insufficient_knowledge": True,
        "sources": [],
    }


# ------------------------------------------------------------------
# greeting flow -- PRODUCTION ARCHITECTURE UPGRADE: a real, in-domain, fully-answerable
# conversational opener (see QuestionIntent.GREETING's own docstring). A static, warm answer --
# same reasoning as capabilities_flow_node just below (a fixed fact about this deployment, no RAG
# lookup needed) -- deliberately does NOT try to parse/echo back a self-introduced name ("my name
# is Sumit") -- that would need real name-extraction logic for a purely cosmetic touch, exactly
# the kind of complexity this codebase's own conventions warn against adding without a measured
# need (see e.g. answer_generation_service.py's plain template fallback).
# ------------------------------------------------------------------


def greeting_flow_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    text = (
        "Hello! I'm Sarthi, your civic services assistant. I can help you report a civic issue "
        "(garbage or waste, water or drainage, roads or potholes, and streetlights), check the "
        "status of a complaint you've already filed, or answer questions about local civic "
        "services. What would you like help with?"
    )
    return {
        "response_text": _localize(text, state, config),
        "routed_to": "NONE_GREETING",
        "sources": [],
    }


# ------------------------------------------------------------------
# capabilities flow -- "what can you do?" is a real, in-domain, fully-answerable question about
# Sarthi's own scope (see QuestionIntent.CAPABILITIES's docstring). A static, accurate answer,
# not a RAG lookup -- what Sarthi supports is a fixed fact about this deployment, not something
# to search a knowledge base for.
# ------------------------------------------------------------------


def capabilities_flow_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    text = (
        "I can help you report a civic issue (garbage or waste, water or drainage, roads or "
        "potholes, and streetlights), check the status of a complaint you've already filed, or "
        "answer questions about local civic services. What would you like help with?"
    )
    return {
        "response_text": _localize(text, state, config),
        "routed_to": "NONE_CAPABILITIES",
        "sources": [],
    }


# ------------------------------------------------------------------
# unclear flow -- genuinely no signal matched (see QuestionIntent.UNCLEAR's own docstring for the
# bug this replaces: every unrecognized question, regardless of what it actually asked, used to
# get the exact same complaint-shaped "what issue would you like to report?" clarification).
# ------------------------------------------------------------------


def unclear_flow_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    question = (
        "I'm not sure I understood that. I can help you report a civic issue (garbage, water, "
        "roads, or streetlights), check the status of a complaint, or answer questions about "
        "local civic services. What would you like help with?"
    )
    return {
        "response_text": _localize(question, state, config),
        # Genuinely ends in a question the citizen is expected to answer next -- same
        # follow_up_required semantics clarification_flow_node already uses, not a one-shot
        # answer like rag_flow_node's.
        "follow_up_required": True,
        "follow_up_question": question,
        "routed_to": "NONE_UNCLEAR",
        "insufficient_knowledge": True,
        "sources": [],
    }


# ------------------------------------------------------------------
# clarification flow
# ------------------------------------------------------------------


def _image_context_prefix(state: GraphState) -> str:
    """A short, honest acknowledgment of an attached image, prepended to whichever clarification
    question actually fires while an image is attached. Without this, a citizen who attached a
    photo but got routed to the category/location/location_ambiguous reason (because they also
    typed text, so the `image_no_text` case below never triggers) would see a generic question
    with no sign the photo was looked at at all -- even though it genuinely was (VisionService
    already ran, see ask_janmitra_service.py's `_process_image()`). Real, not padding: says
    honestly that the read wasn't clear if captioning failed, never invents a description."""
    if not state.get("has_image"):
        return ""
    description = state.get("image_description")
    if description:
        return f"I can see the photo you attached (it looks like: {description}). "
    return "I can see you've attached a photo, though I couldn't get a clear read on it. "


def clarification_flow_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Builds a follow-up question. `clarification_reason` (set by whichever flow node routed
    here) decides which question to ask -- category (complaint flow, missing service type),
    location (complaint or RAG flow, missing city/area), or an ambiguous multi-city state match
    (RAG flow, e.g. "Punjab" matches both Mohali and Patiala)."""
    reason = state.get("clarification_reason")

    if reason == "image_no_text":
        # An image with no text at all -- never guess whether it's a complaint or an information
        # request (see this module's docstring's image-handling note). image_description may be
        # None if captioning failed -- the question still works either way, it's just less
        # specific (matches VisionService's own best-effort-only contract).
        description = state.get("image_description")
        prompt = (
            f"I can see the photo you attached (it looks like: {description}). "
            if description
            else "I can see you've attached a photo. "
        )
        question = prompt + "Would you like to report an issue, or would you like information about what's shown?"
        return {
            "response_text": _localize(question, state, config),
            "follow_up_required": True,
            "follow_up_question": question,
            "follow_up_options": ["Report an issue", "What is this?"],
            "routed_to": "NONE_CLARIFICATION_NEEDED",
            "sources": [],
        }

    if reason == "intent_ambiguous":
        # P0 SAFETY FIX: a bare category/complaint-verb signal, with no confident complaint- or
        # question-shaped language either way (see intent_classifier.py's QuestionIntent.
        # TYPE_A_MAYBE docstring). Never guessed into either a complaint or a RAG answer -- ask
        # directly. Both follow_up_options are chosen to classify correctly with zero new code on
        # the next turn (same established convention as _CATEGORY_CLARIFICATION_OPTIONS above):
        # "Report a problem" matches _COMPLAINT_META_KEYWORDS's "report" directly; "What is the
        # procedure?" is both question-shaped (skips the risky UNCLEAR-continuation override in
        # intent_node) and matches _SERVICE_INFO_KEYWORDS's "what is the procedure" directly.
        category = state.get("service_category")
        category_label = category.replace("_", " ").title() if category else "this"
        question = f"Are you reporting a problem with {category_label}, or would you like information about it?"
        return {
            "response_text": _localize(_image_context_prefix(state) + question, state, config),
            "follow_up_required": True,
            "follow_up_question": question,
            "follow_up_options": ["Report a problem", "What is the procedure?"],
            "routed_to": "NONE_CLARIFICATION_NEEDED",
            "sources": [],
        }

    if reason == "category":
        question = "What issue would you like to report?"
        return {
            "response_text": _localize(_image_context_prefix(state) + question, state, config),
            "follow_up_required": True,
            "follow_up_question": question,
            "follow_up_options": _CATEGORY_CLARIFICATION_OPTIONS,
            "routed_to": "NONE_CLARIFICATION_NEEDED",
            "sources": [],
        }

    if reason == "location_ambiguous":
        candidates = state.get("location_ambiguous_candidates", [])
        question = f"Which city are you asking about — {', '.join(candidates)}?"
        return {
            "response_text": _localize(_image_context_prefix(state) + question, state, config),
            "follow_up_required": True,
            "follow_up_question": "Which city/area are you in?",
            "follow_up_options": candidates,
            "routed_to": "NONE_CLARIFICATION_NEEDED",
            "sources": [],
        }

    # Location missing entirely -- two different situations collapsed into one branch:
    # (a) the citizen never gave a location signal of any kind, vs. (b) they explicitly picked
    # one (via "Use current location"/"Select location", or typed free text) and it simply
    # didn't resolve to anywhere recognizable. Without distinguishing these, a citizen who just
    # picked a location sees the EXACT SAME "What is the location?" question again with no
    # acknowledgment anything happened -- indistinguishable from the assistant ignoring their
    # answer, live-reported as feeling like a stuck loop. (b) gets its own honest wording instead.
    if state.get("location_explicit_signal_unresolved"):
        question = "I couldn't recognize that as a location. Please try typing a city or area name, or use \"Use current location\"."
    else:
        question = "What is the location? This helps me give you the correct local information."
    return {
        "response_text": _localize(_image_context_prefix(state) + question, state, config),
        "follow_up_required": True,
        "follow_up_question": "What is the location?",
        "follow_up_options": _LOCATION_CLARIFICATION_OPTIONS,
        "routed_to": "NONE_CLARIFICATION_NEEDED",
        "sources": [],
    }


# ------------------------------------------------------------------
# status flow -- NEVER touches RAG, matches the pre-graph service's hardest rule exactly
# ------------------------------------------------------------------


def status_flow_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    db, user = ctx.db, ctx.user
    text = state.get("normalized_message") or state.get("user_message", "")

    match = _COMPLAINT_NUMBER_PATTERN.search(text)
    if not match:
        question = "Which complaint would you like the status of? Please give the complaint number, or check your complaints list."
        return {
            "response_text": _localize(question, state, config),
            "follow_up_required": True,
            "follow_up_question": "What is your complaint number?",
            "routed_to": "COMPLAINT_STATUS_API",
            "sources": [],
        }

    complaint_id = int(next(g for g in match.groups() if g))
    complaint = complaint_repository.get_complaint_by_id(db, complaint_id)
    if complaint is None or (user.role == "citizen" and complaint.citizen_id != str(user.id)):
        # Same message for "doesn't exist" and "not yours" -- never leaks which IDs exist to a
        # citizen who isn't the owner.
        return {
            "response_text": _localize(f"I couldn't find complaint #{complaint_id} for your account.", state, config),
            "routed_to": "COMPLAINT_STATUS_API",
            "sources": [],
        }

    status_text = {
        "pending": "still pending — not yet assigned to a worker.",
        "assigned": "assigned to a worker, awaiting their acceptance.",
        "accepted": "accepted and being worked on.",
        "resolved": "marked resolved.",
    }.get(complaint.status, complaint.status)
    return {
        "response_text": _localize(f"Complaint #{complaint.id} is {status_text}", state, config),
        "routed_to": "COMPLAINT_STATUS_API",
        "sources": [],
    }


# ------------------------------------------------------------------
# RAG flow -- unchanged retrieval pipeline (see docs/ask_janmitra_rag_architecture.md), this node
# only orchestrates the existing RagRetriever + AnswerGenerationService calls.
# ------------------------------------------------------------------

_LANGUAGE_NAMES = {"en": "English", "hi": "Hindi", "mr": "Marathi", "or": "Odia", "gu": "Gujarati", "bn": "Bengali"}


def rag_flow_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    deps = _deps(config)
    root = _trace_root(config)
    text = state.get("normalized_message") or state.get("user_message", "")
    category = state.get("service_category")
    category_enum = ServiceCategory(category) if category else None
    language_name = _LANGUAGE_NAMES.get(state.get("original_language") or "en", "English")

    retrieval_span = tracing.start_child_run(
        root, "rag_retrieval", "retriever",
        inputs={
            "query": tracing.redact_text(text),
            "service_category": category,
            "city": state.get("location_city"),
            "state": state.get("location_state"),
        },
    )
    outcome = deps.retriever.retrieve(text, category_enum, state.get("location_city"), state.get("location_state"))
    tracing.end_run(
        retrieval_span,
        outputs={
            "result_count": len(outcome.results),
            "insufficient_knowledge": outcome.insufficient_knowledge,
            "reason": outcome.reason,
            "top_score": outcome.results[0].score if outcome.results else None,
        },
    )

    # Post-retrieval topic filter for "new connection" questions -- measured, real reason this
    # exists (KB-expansion phase): once real new-water/sewerage-connection records existed for
    # Mohali/Patiala/Odisha, the classifier's out-of-scope short-circuit for those phrases was
    # removed (see intent_classifier.py's _NEW_CONNECTION_KEYWORDS docstring) so they could reach
    # RAG. But a city with NO new-connection record (e.g. Nagpur, synthetic-only) still passed the
    # category+location filter and the relevance threshold on its generic leak/repair chunk --
    # topically similar enough ("water supply") to score above threshold despite answering a
    # completely different question. Verified directly before this fix: Nagpur's synthetic
    # WATER_SUPPLY_DRAINAGE_NAGPUR leak-repair chunk scored 0.849 against "new water connection in
    # Nagpur", comfortably above the 0.79 relevance threshold. This filter closes that gap without
    # touching RagRetriever itself (its filtering/threshold/rerank logic is unchanged) -- it only
    # narrows the already-retrieved candidates to ones actually about a new connection (service_id
    # prefix "WATER_NEW_", the naming convention every new-connection record uses), and reports
    # insufficient_knowledge honestly if none qualify, exactly like any other unanswerable case.
    if state.get("requests_new_connection") and outcome.results:
        new_connection_results = [r for r in outcome.results if r.metadata.get("service_id", "").startswith("WATER_NEW_")]
        if new_connection_results:
            outcome.results = new_connection_results
        else:
            outcome.results = []
            outcome.insufficient_knowledge = True
            outcome.reason = "No new-connection record exists for this location -- only existing-issue repair/complaint records are covered here."

    if outcome.insufficient_knowledge or not outcome.results:
        reason = outcome.reason or "No information available."
        place = state.get("location_city") or state.get("location_state") or "this area"
        text = f"I don't currently have reliable information for this in {place}. ({reason})"
        return {
            "response_text": _localize(text, state, config),
            "routed_to": "RAG",
            "insufficient_knowledge": True,
            "sources": [],
        }

    context_chunks = [r.metadata["content"] for r in outcome.results]
    context_labels = [chunk_context_label(r) for r in outcome.results]
    answer_span = tracing.start_child_run(
        root, "answer_generation", "llm",
        inputs={"question": tracing.redact_text(text), "language": language_name, "context_chunk_count": len(context_chunks)},
    )
    # LIVE PRODUCT FEATURE: the same question, in the same language, with the same resolved
    # service category/city/state, is only ever sent to the LLM once -- reused from
    # RagAnswerCache on every later ask, the same "translate/generate once, cache forever"
    # pattern complaint_translation_cache.py already established for complaint text. Keyed on
    # resolved category/city/state (not just the question text) specifically so two askers in two
    # different cities can never see each other's cached city's answer (see RagAnswerCache's own
    # docstring). Most valuable for this app's own 4 featured starter questions (identical text,
    # asked by every new user) -- also what keeps Ask Sarthi useful when Sarvam credits/quota run
    # out (see this fix's own live-reproduced case): a previously-cached question still answers
    # normally, needing no LLM call at all.
    ctx = _ctx(config)
    language_code = state.get("original_language") or "en"
    location_city = state.get("location_city")
    location_state = state.get("location_state")
    cached_answer = get_cached_answer(ctx.db, text, language_code, category, location_city, location_state)
    if cached_answer is not None:
        answer_text, was_llm_generated = cached_answer, True
        tracing.end_run(answer_span, outputs={"answer_was_llm_generated": True, "cache_hit": True})
    else:
        answer_text, was_llm_generated = deps.answer_service.generate(text, context_chunks, language_name, context_labels)
        tracing.end_run(
            answer_span,
            outputs={"answer_was_llm_generated": was_llm_generated, "cache_hit": False, "answer": tracing.redact_text(answer_text)},
        )
        # Never cache the no-LLM-available fallback (raw chunk echo) -- only a genuinely
        # LLM-generated answer, so a temporary credits/network outage can never freeze a
        # degraded answer into the cache for everyone else who asks the same thing later.
        if was_llm_generated:
            store_answer(ctx.db, text, language_code, category, location_city, location_state, answer_text)

    sources = []
    seen: set[str] = set()
    for r in outcome.results:
        source_id = r.metadata.get("source_id")
        if source_id in seen:
            continue
        seen.add(source_id)
        sources.append({
            "source_id": source_id,
            "source_title": r.metadata.get("source_title"),
            "source_organization": r.metadata.get("source_organization"),
            "source_url": r.metadata.get("source_url"),
            "source_type": r.metadata.get("source_type"),
            "verification_status": r.metadata.get("verification_status"),
            "geographic_scope": r.metadata.get("geographic_scope"),
        })

    statuses = {s["verification_status"] for s in sources}
    overall_status = "MIXED" if len(statuses) > 1 else next(iter(statuses), None)

    return {
        "response_text": answer_text,
        "sources": sources,
        "verification_status": overall_status,
        "routed_to": "RAG",
        "answer_was_llm_generated": was_llm_generated,
    }


# ------------------------------------------------------------------
# complaint flow -- NEW this phase: actually files a complaint via the existing
# ComplaintAgent/assign_next_worker services (see this module's docstring for the confirmed
# behavior-change rationale).
# ------------------------------------------------------------------


_RECOVERABLE_INTENTS = (QuestionIntent.TYPE_A_COMPLAINT, QuestionIntent.TYPE_A_MAYBE)


def _recover_category_from_history(state: GraphState) -> ServiceCategory | None:
    """If the CURRENT message doesn't name a service category (e.g. a bare "Streetlight." reply
    already does, via the existing classifier -- but a later turn like "Use my current location."
    does not), scan prior USER turns, most recent first, through the same `classify()` used by
    `intent_node` for one that did. Mirrors the location node's own conversation_history fallback
    (`_resolve_location` above) -- same idiom, applied to category instead of place, so a
    multi-turn complaint conversation doesn't lose the issue type the citizen already gave.

    RED-TEAM SAFETY FIX: only recovers from a turn whose OWN bare classification was actually
    complaint-shaped (TYPE_A_COMPLAINT or the ambiguous TYPE_A_MAYBE -- see that intent's own
    docstring), never a turn that was confidently something else (TYPE_B_SERVICE_INFO,
    CAPABILITIES, ...). Live-reproduced bug this closes: a purely informational question ("what
    is the procedure for garbage collection?") mentions a category word too, and without this
    check its category got "recovered" many turns later and used to file a real complaint whose
    stored description was that same informational question -- not anything resembling a
    complaint."""
    for turn in reversed(state.get("conversation_history", [])):
        if turn.get("role") != "user":
            continue
        result = classify(turn.get("content", ""))
        if result.service_category is not None and result.intent in _RECOVERABLE_INTENTS:
            return result.service_category
    return None


def _awaiting_confirmation(conversation_history: list[dict]) -> bool:
    """True when the LAST turn in `conversation_history` is Sarthi's own confirmation prompt (see
    `_build_confirmation_prompt`) -- the one signal complaint_flow_node uses to decide whether
    THIS turn's message should be interpreted as a confirmation/cancellation reply at all, rather
    than a fresh message.

    BUG FIX (live Marathi validation): checks the explicit, caller-echoed
    `complaint_workflow_state` (`_last_assistant_turn_state`) FIRST -- this is language-independent
    by construction, so a Sarvam-generated Marathi confirmation prompt whose exact wording was
    never seen at fix-time still gates correctly. Only when that field is absent (an
    older/unaware caller, or a turn genuinely predating this field) does this fall back to the
    original `_CONFIRMATION_PROMPT_MARKERS` text match -- for a language not covered there this
    text match can still miss, but the failure mode of a miss is only ever one extra "please
    confirm" round-trip (see complaint_flow_node's own docstring), never a skipped confirmation --
    the safe direction to fail in for a check that gates a database write."""
    explicit_state = _last_assistant_turn_state(conversation_history)
    if explicit_state is not None:
        return explicit_state == "AWAITING_CONFIRMATION"
    return _last_assistant_turn_matches(conversation_history, _CONFIRMATION_PROMPT_MARKERS)


def _recover_complaint_draft_from_history(state: GraphState) -> tuple[ServiceCategory | None, str]:
    """Like `_recover_category_from_history`, but also returns the ORIGINAL text of the turn that
    established the category -- needed when the CURRENT message is just a short confirmation
    reply ("yes, submit it"), which must never itself become the complaint's stored description
    (see complaint_flow_node's confirmation-gate docstring). Skips any turn that is itself an
    explicit confirmation/cancellation reply, so a citizen who says "yes" twice in a row (e.g. to
    re-confirm after an ambiguous middle reply) still recovers the real original description, not
    "yes". RED-TEAM SAFETY FIX: also skips any turn whose own bare classification was NOT
    complaint-shaped -- see `_recover_category_from_history`'s own docstring for the
    live-reproduced bug this closes (an informational question's category/text being "recovered"
    as if it were a real complaint)."""
    for turn in reversed(state.get("conversation_history", [])):
        if turn.get("role") != "user":
            continue
        content = turn.get("content", "")
        if is_explicit_confirmation(content) or is_explicit_cancellation(content):
            continue
        result = classify(content)
        if result.service_category is not None and result.intent in _RECOVERABLE_INTENTS:
            return result.service_category, content
    return None, state.get("normalized_message") or state.get("user_message", "")


def _find_worker_ward_text_with_aliases(deps: GraphDeps, ctx: RequestContext, hint: str) -> str | None:
    """LOCATION NORMALIZATION FIX (live Hindi validation): `LocationResolver.find_worker_ward_text`
    itself is deliberately left untouched (exact/substring matching only, no alias awareness --
    see this codebase's own instruction to preserve it) -- this wraps it with ONE extra,
    general fallback: if `hint` doesn't directly match any worker, and `hint` happens to be (or
    contain) a RAG-gazetteer CANONICAL city name, also try every known alias of that city (see
    `known_aliases_for_city`'s own docstring for the full root-cause explanation and why this
    isn't specific to Mohali). Every tier of `_resolve_worker_ward_text` below calls this instead
    of the resolver method directly, so "equivalent city names resolve consistently" holds
    uniformly rather than being patched into only the one tier that happened to reproduce the bug
    live. Never invents a match beyond what `find_worker_ward_text` itself would already accept
    for the ALIAS text -- this only widens WHICH strings get tried, not HOW a string is judged to
    match."""
    match = deps.location_resolver.find_worker_ward_text(ctx.db, hint)
    if match is not None:
        return match
    for alias in known_aliases_for_city(hint):
        match = deps.location_resolver.find_worker_ward_text(ctx.db, alias)
        if match is not None:
            return match
    return None


def _resolve_worker_ward_text(
    deps: GraphDeps, ctx: RequestContext, state: GraphState, complaint_text: str
) -> tuple[str | None, Any, ResolvedLocation | None]:
    """Finds a REAL, currently-staffed worker ward for this complaint -- see complaint_flow_node's
    own docstring for why this must be a real worker match, not just a RAG-gazetteer city match.
    Tries, in order: explicit `ctx.location_text`, a city named in `complaint_text`, GPS
    coordinates, the RAG gazetteer's own already-resolved `location_city`/`location_state` (set
    upstream by location_node, which itself already scans `conversation_history` -- see that
    node's `_resolve_location`), and a raw scan of `conversation_history` text/tokens. That last
    fallback matters on a confirmation-reply turn: `complaint_text` there is the recovered
    original complaint description, which may not itself name a city if the citizen gave the
    location in a *separate* later turn (e.g. "Streetlight is broken." then, next turn, "Pune.")
    -- in that case only `location_city` (already carrying the citizen's answer forward) has it.

    Deliberately does NOT fall back to the citizen's own registered ward (`ctx.user.ward`) -- see
    `_resolve_own_ward_worker_text` below, and complaint_flow_node's own docstring, for why that
    fallback must only ever run when NO location signal of any kind (not even one that failed to
    match a real worker) exists anywhere in this exchange, a distinction this function alone
    cannot make (it only ever reports "found" or "not found", not "not even attempted").

    Returns (worker_ward_text, resolved_ward, gps_resolved) -- the latter two are optional bonus
    values, used only when a complaint is actually created, for the richer state/district/.../
    locality ID chain (see complaint_flow_node's own comment on why a resolved Ward's bare name
    is not, by itself, sufficient)."""
    worker_ward_text = _find_worker_ward_text_with_aliases(deps, ctx, ctx.location_text or "")
    if worker_ward_text is None:
        worker_ward_text = _find_worker_ward_text_with_aliases(deps, ctx, complaint_text)
    if worker_ward_text is None:
        # `find_worker_ward_text` matches a hint that IS (close to) a bare city/ward name -- an
        # exact match, or the city named inside a structured "Ward N -- Locality, City" ward (see
        # that method's own docstring) -- never a full sentence containing one. On a
        # confirmation-reply turn `complaint_text` is the RECOVERED original description (e.g.
        # "Street light not working in Mohali."), not a bare city reply, so try each individual
        # word/token too -- a plain worker ward like "Mohali" is typically a single city-name
        # token that appears standalone once punctuation is stripped, resolving it the same way a
        # dedicated "Mohali." reply already would. Deliberately does not touch
        # `find_worker_ward_text` itself (see this codebase's own instruction to preserve it).
        for token in re.findall(r"[^\s,.;:!?()]+", complaint_text):
            match = _find_worker_ward_text_with_aliases(deps, ctx, token)
            if match is not None:
                worker_ward_text = match
                break
    gps_resolved: ResolvedLocation | None = None
    if worker_ward_text is None and ctx.latitude is not None and ctx.longitude is not None:
        # Same mistake this fix already closed once for the RAG gazetteer -- almost repeated here
        # for GPS: raw coordinate PRESENCE isn't resolution SUCCESS (a real geocoder call can fail
        # or return nothing usable; test_gps_failure_does_not_break_ask_janmitra exists specifically
        # to catch a caller that assumes otherwise). Resolve for real and try to match a worker
        # from whatever city name comes back, exactly like the text-based path above.
        gps_resolved = deps.location_resolver.resolve_coordinates(ctx.latitude, ctx.longitude)
        if gps_resolved.city_name:
            worker_ward_text = _find_worker_ward_text_with_aliases(deps, ctx, gps_resolved.city_name)
    # TARGETED SAFETY FIX: `location_city`/`location_state` are excluded here when their OWN
    # source (set by `location_node`'s `_resolve_location`) is "citizen_home_ward" -- that
    # resolver has its own, separate ctx.user.ward fallback (used for RAG's "answer scoped to
    # where the citizen lives" purpose, unrelated to this function and deliberately left
    # unchanged, see that function's own docstring), and trusting its OUTPUT here would silently
    # reintroduce the exact same "explicit-but-unmatched location gets overridden by the citizen's
    # home ward" bug this function's own docstring documents closing -- just through a side door
    # instead of the tier removed from this function directly. A hint from any OTHER source
    # ("text", "gps", "conversation_history") is still a genuine citizen-given signal and used
    # normally.
    if worker_ward_text is None and state.get("location_source") != "citizen_home_ward":
        for hint in (state.get("location_city"), state.get("location_state")):
            if hint:
                worker_ward_text = _find_worker_ward_text_with_aliases(deps, ctx, hint)
                if worker_ward_text is not None:
                    break
    if worker_ward_text is None:
        # Last resort, and specifically what makes the confirmation-reply turn work when the
        # citizen originally gave the location via a SEPARATE structured signal (`ctx.
        # location_text` on an earlier turn, e.g. a location-picker selection) rather than typing
        # a city name into the complaint text itself: `ctx.location_text` is per-request plumbing
        # (see RequestContext's own docstring), not stored anywhere, so it is simply gone by the
        # time a later confirmation reply arrives with no `location_text` of its own. But
        # Sarthi's OWN confirmation prompt (see `_build_confirmation_prompt`) echoes the resolved
        # ward back to the citizen verbatim ('Your complaint would be about ... in "Mohali"'),
        # which becomes a real turn in `conversation_history` the moment the citizen replies to
        # it -- so scanning every turn (not just user ones) for the same city/ward hint recovers
        # it reliably, without needing a second, separate persistence mechanism for `location_text`.
        for turn in reversed(state.get("conversation_history") or []):
            content = turn.get("content", "")
            if not content:
                continue
            match = _find_worker_ward_text_with_aliases(deps, ctx, content)
            if match is None:
                for token in re.findall(r"[^\s,.;:!?()\"]+", content):
                    match = _find_worker_ward_text_with_aliases(deps, ctx, token)
                    if match is not None:
                        break
            if match is not None:
                worker_ward_text = match
                break
    # `resolved_ward` (the structured `wards`-table row, only ever used further below for its
    # richer state/district/.../locality ID chain) is a BONUS when `worker_ward_text` happens to
    # also be in the "Ward N -- Locality, City" format `resolve_ward_by_text` understands -- NOT a
    # requirement. A real worker match (e.g. a worker registered with a plain `ward="Mohali"`,
    # exactly what this project's own test fixtures use) is already sufficient evidence a real,
    # assignable place was found; requiring `resolved_ward` too rejected that exact case, a real
    # regression caught before shipping the location-honesty fix this function was lifted from.
    resolved_ward = None
    if worker_ward_text is not None:
        resolved_ward = deps.location_resolver.resolve_ward_by_text(ctx.db, worker_ward_text)
    return worker_ward_text, resolved_ward, gps_resolved


def _resolve_own_ward_worker_text(deps: GraphDeps, ctx: RequestContext) -> tuple[str | None, Any]:
    """The citizen's OWN registered ward (see models.User.ward), matched against a real worker --
    TARGETED SAFETY FIX (production-safety review): deliberately kept OUT of
    `_resolve_worker_ward_text` and called separately by `complaint_flow_node`, ONLY when its own
    `known_place` check finds NO location signal anywhere in this exchange (not even one that
    failed to match a real worker).

    An earlier version of this fallback lived INSIDE `_resolve_worker_ward_text`, tried
    unconditionally whenever every other tier came up empty -- including when the citizen had
    given an EXPLICIT `ctx.location_text` (or a location resolvable from the message/GPS/history)
    that simply wasn't a currently-served area. That silently discarded what the citizen actually
    said and refiled the complaint against their own home ward instead, with no indication
    anything had been overridden -- a real correctness bug, not a security one (this is never
    client-spoofable, see below), but capable of misrouting a complaint about a problem seen
    elsewhere, or reported on behalf of someone else, to the wrong worker's queue. The fix:
    `complaint_flow_node` now only reaches this function after confirming NO explicit-or-derived
    location signal exists at all -- an explicit-but-unmatched location instead gets the honest
    "not currently served" answer (see complaint_flow_node's own `known_place` branch), never a
    silent substitution.

    Still closes the original multilingual gap this fallback was added for (see git history):
    once `_localize()` translates a turn, `_resolve_worker_ward_text`'s own conversation_history
    text-scan tier can no longer string-match a worker's Latin-script ward, but `ctx.user.ward` --
    always stored in its original script -- still can. Safe from a security/spoofing standpoint
    either way: `ctx.user.ward` is read from the AUTHENTICATED user's own DB row (see
    `get_current_user()`), never a client-suppliable request field, so it can never be manipulated
    to route a complaint into another citizen's ward."""
    if not ctx.user.ward:
        return None, None
    worker_ward_text = _find_worker_ward_text_with_aliases(deps, ctx, ctx.user.ward)
    if worker_ward_text is None:
        return None, None
    resolved_ward = deps.location_resolver.resolve_ward_by_text(ctx.db, worker_ward_text)
    return worker_ward_text, resolved_ward


def _build_confirmation_prompt(
    state: GraphState, config: RunnableConfig, category: ServiceCategory, worker_ward_text: str, complaint_text: str
) -> dict[str, Any]:
    """P0 SAFETY FIX: the response shown once a category and a real, assignable location have
    both resolved, but BEFORE create_complaint() is ever called -- see complaint_flow_node's own
    docstring for the bug this closes. Shows exactly what would be submitted and asks for an
    explicit, deterministic confirmation (see intent_classifier.py's is_explicit_confirmation).
    The English marker text here is load-bearing: `_awaiting_confirmation` (above) matches
    against it verbatim to recognize a reply to THIS prompt on the next turn."""
    category_label = category.value.replace("_", " ").title()
    summary = (
        f"Your complaint would be about {category_label} in \"{worker_ward_text}\": "
        f"\"{complaint_text.strip()}\". Would you like me to submit this complaint? "
        "Reply \"yes, submit it\" to confirm, or \"no\" to cancel."
    )
    return {
        "response_text": _localize(summary, state, config),
        "follow_up_required": True,
        "follow_up_question": "Would you like me to submit this complaint?",
        "follow_up_options": ["Yes, submit it", "No, cancel"],
        "routed_to": "NONE_AWAITING_CONFIRMATION",
        "sources": [],
        "service_category": category.value,
        "complaint_workflow_state": "AWAITING_CONFIRMATION",
    }


def complaint_flow_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Resolves a complaint's category + real assignable location, then -- P0 SAFETY FIX, see the
    production-safety audit that found this -- shows the citizen exactly what would be filed and
    waits for an explicit, deterministic confirmation reply (never an LLM judgment call, see
    intent_classifier.py's is_explicit_confirmation) before create_complaint() is ever called.
    Previously, the very first message that resolved both a category and a real worker location
    created the complaint immediately -- which is exactly how a purely informational question
    that happened to name a category and a real supported city ("What is the procedure for
    garbage collection complaints in Pune?", "What are the rules for streetlight repair in
    Kanpur?") got silently filed and assigned to a real worker, live-reproduced by that audit.

    This backend has no server-side session/checkpointer (see state.py's own docstring) -- exactly
    as before this fix, "waiting for the confirmation reply" is derived fresh each request from
    `conversation_history`, via `_awaiting_confirmation` (recognizing Sarthi's own confirmation
    prompt as the last turn) and `_recover_complaint_draft_from_history` (recovering the real
    complaint description, never the confirmation word itself, as what gets stored)."""
    deps = _deps(config)
    ctx = _ctx(config)
    root = _trace_root(config)
    text = state.get("normalized_message") or state.get("user_message", "")
    history = state.get("conversation_history") or []

    awaiting_confirmation = _awaiting_confirmation(history)

    if awaiting_confirmation and is_explicit_cancellation(text):
        return {
            "response_text": _localize(
                "Okay, I won't submit that complaint. Let me know if you'd like to report something else.",
                state, config,
            ),
            "routed_to": "NONE_CANCELLED",
            "sources": [],
            "complaint_workflow_state": "CANCELLED",
        }

    confirmed = awaiting_confirmation and is_explicit_confirmation(text)

    # CONVERSATION & REQUEST/RESPONSE ALIGNMENT AUDIT: a reply to Sarthi's own confirmation
    # prompt is normally never itself the complaint description (see the comment on the `else`
    # branch below) -- EXCEPT when the reply is confidently, independently complaint-shaped on
    # its OWN terms, e.g. "I have another complaint." Live-tested gap this closes: previously ANY
    # non-confirm/non-cancel reply here fell straight to `_recover_complaint_draft_from_history`,
    # silently re-showing the STALE pending draft's confirmation prompt even when the citizen had
    # just described a genuinely different, new complaint -- a real request/response mismatch
    # (never a safety issue: the stale draft was never confirmed/created, so there is nothing to
    # lose by abandoning it). Reuses `classify()`'s OWN existing confident tier (state/meta
    # signal, not a question, not the weaker TYPE_A_MAYBE guess -- see intent_classifier.py's own
    # docstring) as the sole signal -- no new keyword list, no new classifier.
    fresh_complaint_signal = classify(text) if awaiting_confirmation and not confirmed else None
    is_fresh_complaint = bool(fresh_complaint_signal and fresh_complaint_signal.intent == QuestionIntent.TYPE_A_COMPLAINT)

    if is_fresh_complaint:
        # Deliberately does NOT fall back to `_recover_category_from_history` when this new
        # statement doesn't itself name a category (e.g. "I have another complaint.") -- that
        # history-recovery fallback exists for building up ONE continuous complaint across turns,
        # not for guessing that a citizen's brand-new issue must share the STALE, unrelated
        # pending draft's category. Honestly asks instead (see the `category is None` branch
        # below), exactly like a genuinely fresh conversation would.
        category, complaint_text = fresh_complaint_signal.service_category, text
    elif awaiting_confirmation:
        # This turn's message is a reply to Sarthi's OWN confirmation prompt -- it is never
        # itself the complaint description (whether it's "yes, submit it", "no", or an ambiguous
        # "okay"). Recover the real description/category from history instead of the OLD
        # single-line `text = state.get("normalized_message")...` this replaces.
        category, complaint_text = _recover_complaint_draft_from_history(state)
    else:
        category_value = state.get("service_category")
        category = ServiceCategory(category_value) if category_value else _recover_category_from_history(state)
        complaint_text = text

    if category is None:
        return {
            "needs_clarification": True,
            "clarification_reason": "category",
            "route": "clarification",
            "complaint_workflow_state": "DRAFT",
        }

    if state.get("location_is_ambiguous"):
        return {
            "needs_clarification": True,
            "clarification_reason": "location_ambiguous",
            "route": "clarification",
            "complaint_workflow_state": "DRAFT",
        }

    # `location_city`/`location_state` only ever come from the RAG knowledge base's narrow 30-city
    # gazetteer (see location_extractor.py) -- unrelated to whether a complaint can actually be
    # ASSIGNED, which depends entirely on `assignment_service.py`'s exact-string match between
    # `complaint.ward` and a real worker's `User.ward` (or a shared `ward_id`, though none of the
    # currently-seeded workers have that backfilled -- see find_worker_ward_text's own docstring).
    # A city the RAG knowledge base has documents for (e.g. Mohali) is not necessarily a city this
    # app has any WORKER for at all -- two separate, unrelated datasets. Two earlier versions of
    # this fix got this wrong in two different ways, both caught live before shipping: (1) accepting
    # ANY citizen-typed text as the ward regardless of whether it matched anything real -- created
    # complaints that could never be assigned; (2) treating a RAG gazetteer match alone as "good
    # enough to proceed" -- let a complaint through for a city (Mohali) with a real name but zero
    # seeded workers, which then sat unassigned exactly like (1) did.
    #
    # Correct fix: only proceed when a REAL, CURRENTLY-STAFFED ward can be found for this
    # complaint -- see `_resolve_worker_ward_text` above. If none of its sources hold, this is
    # honest instead of silently broken: says plainly that this area isn't covered yet, and does
    # NOT create an unassignable complaint or repeat the same question forever.
    worker_ward_text, resolved_ward, gps_resolved = _resolve_worker_ward_text(deps, ctx, state, complaint_text)

    has_real_location = worker_ward_text is not None
    if not has_real_location:
        # A place was already established from ANY source -- explicit `ctx.location_text`, the
        # RAG gazetteer's own city/state match on this or an earlier turn (`location_city`/
        # `location_state`, set upstream by location_node), or a city mentioned earlier in
        # `conversation_history` -- just not one with a real worker. All of these are equally
        # legitimate "the citizen already told us where" signals; gating this on `ctx.location_text`
        # alone (an earlier version of this fix) missed the conversation-history case, a real
        # regression caught by this fix's own test run: a citizen who said "I'm in Mohali." last
        # turn and "Street light not working." this turn got asked for location AGAIN, even though
        # the location was already known -- just not assignable.
        #
        # TARGETED SAFETY FIX: `location_city`/`location_state` are excluded here too when their
        # source is "citizen_home_ward" -- see `_resolve_worker_ward_text`'s own matching comment
        # a few lines up for why (the same value, same reasoning; keeping this "known_place"
        # check and that function's own tier consistent about what counts as a genuine
        # citizen-given signal).
        location_from_gazetteer = (
            None if state.get("location_source") == "citizen_home_ward"
            else (state.get("location_city") or state.get("location_state"))
        )
        known_place = ctx.location_text or location_from_gazetteer
        if known_place:
            # An honest, terminal answer, not the same clarification question again (see this
            # fix's own docstring for why looping was the wrong fallback too). TARGETED SAFETY
            # FIX: deliberately does NOT fall through to `ctx.user.ward` below -- an explicit (or
            # message/GPS/history-derived) location the citizen actually gave, even one that
            # didn't match a real worker, must never be silently swapped for their home ward
            # instead (see `_resolve_own_ward_worker_text`'s own docstring for the correctness bug
            # this closes).
            honest_text = (
                "I'm sorry, Sarthi doesn't currently have workers set up to handle complaints in "
                f"\"{known_place.strip()}\". Please try a nearby city, or contact your local "
                "municipal office directly."
            )
            return {
                "response_text": _localize(honest_text, state, config),
                "routed_to": "NONE_OUT_OF_SCOPE",
                "insufficient_knowledge": True,
                "sources": [],
                "complaint_workflow_state": "CANCELLED",
            }
        # No location signal of ANY kind exists anywhere in this exchange -- ONLY here is the
        # citizen's own registered ward tried, as a true last resort (see
        # `_resolve_own_ward_worker_text`'s own docstring).
        worker_ward_text, own_ward_resolved = _resolve_own_ward_worker_text(deps, ctx)
        if worker_ward_text is not None:
            resolved_ward = own_ward_resolved
            has_real_location = True

    if not has_real_location:
        return {
            "needs_clarification": True,
            "clarification_reason": "location",
            "route": "clarification",
            "complaint_workflow_state": "DRAFT",
        }

    if not confirmed:
        # Either this is the first turn where category + a real location have both resolved
        # (never seen a confirmation prompt yet), or a pending confirmation exists but this reply
        # was neither a clear "yes" nor a clear "no" -- Part 3's own rule: never guess, ask again.
        # Either way, create_complaint() must not run yet -- see this function's own docstring
        # for the P0 bug this gate closes.
        return _build_confirmation_prompt(state, config, category, worker_ward_text, complaint_text)

    # confirmed == True from here on -- an explicit, deterministic "yes" was given in direct
    # reply to Sarthi's own confirmation prompt (see _awaiting_confirmation/is_explicit_
    # confirmation). File the complaint via the EXISTING complaint pipeline -- unchanged from
    # before this fix, except `complaint_text` (the recovered original description) is used
    # instead of this turn's own text (the confirmation word itself). An attached image (see
    # ctx.image_saved, set by ask_janmitra_service.ask_with_image()) is threaded through exactly
    # like the dedicated complaint form does: the legacy single-photo column for backward
    # compatibility, and a real ComplaintEvidence row via the EXISTING evidence_repository -- no
    # second image-storage system (see this module's docstring).
    creation_span = tracing.start_child_run(
        root, "complaint_creation", "tool",
        inputs={"service_category": category.value, "language": state.get("original_language")},
    )
    try:
        complaint = deps.complaint_agent.create_complaint(
            db=ctx.db,
            citizen_id=str(ctx.user.id),
            language_code=state.get("original_language") or "en",
            text=complaint_text,
            audio_chunks=None,
            photo_path=ctx.image_saved.filename if ctx.image_saved else None,
        )
    except (ValueError, AIServiceError) as exc:
        logger.warning("Ask Sarthi complaint_flow: complaint creation failed: %s", exc)
        tracing.end_run(creation_span, error=str(exc))
        error_text = "I couldn't file that complaint right now. Please try again, or use the complaint form directly."
        return {
            "response_text": _localize(error_text, state, config),
            "routed_to": "COMPLAINT_CREATION_FAILED",
            "error": str(exc),
            "sources": [],
            "complaint_workflow_state": "CANCELLED",
        }
    tracing.end_run(creation_span, outputs={"complaint_id": complaint.id, "status": complaint.status})

    if ctx.image_saved is not None:
        # The file is already validated and written to disk (see ask_with_image()) -- this only
        # records the ComplaintEvidence row, the exact same primitive _save_evidence_files()
        # itself calls, so this doesn't re-validate/re-write a file that's already safely saved.
        evidence_repository.add_evidence(
            ctx.db,
            complaint_id=complaint.id,
            update_id=None,
            uploaded_by=ctx.user.id,
            uploader_role="citizen",
            file_name=ctx.image_saved.original_name,
            file_path=ctx.image_saved.filename,
            file_type=ctx.image_saved.content_type,
            file_size=ctx.image_saved.size,
            stage="CITIZEN_COMPLAINT",
        )

    # Resolve ward-level location for assignment -- deliberately the app's OWN LocationResolver
    # (state/district/ULB/WARD hierarchy, what assignment_service.py actually keys on), not the
    # RAG gazetteer's city/state (that's only ever used for RAG's metadata filtering, a separate,
    # coarser-grained concern -- see location_extractor.py's module docstring). Reuses
    # `worker_ward_text`/`resolved_ward`/`gps_resolved` from the gate above -- by the time
    # execution reaches here the gate has already required `worker_ward_text is not None` (or
    # returned early), so it's always set; no need to re-query or branch on GPS separately here.
    #
    # `complaint.ward` is set to the real WORKER's own exact ward string (`worker_ward_text`),
    # never a `wards` table row's bare `.name` -- see find_worker_ward_text's docstring for the
    # real bug this closes (a resolved Ward's bare "Ward 22" never exact-matches a worker
    # registered as "Ward 22 — Kothrud, Pune", so the complaint silently never got assigned even
    # though structured resolution had technically "succeeded").
    try:
        chain = (
            deps.location_resolver.location_chain_for_ward(ctx.db, resolved_ward)
            if resolved_ward is not None
            else None
        )
        complaint_repository.save_complaint_location(
            ctx.db, complaint, ward=worker_ward_text, location_chain=chain,
            formatted_address=gps_resolved.formatted_address if gps_resolved else None,
        )
    except Exception:
        # Best-effort, exactly like routes/complaints.py's own equivalent try/except -- a
        # location-resolution failure must never fail complaint creation, which has already
        # succeeded and committed above.
        logger.exception("Ask Sarthi complaint_flow: ward resolution failed for complaint %s; continuing unassigned by ward.", complaint.id)

    assign_next_worker(ctx.db, complaint)
    ctx.db.refresh(complaint)

    worker_assignment = None
    if complaint.assigned_worker_id is not None:
        worker = complaint_repository.get_user_by_id(ctx.db, complaint.assigned_worker_id)
        worker_assignment = {
            "worker_id": complaint.assigned_worker_id,
            "worker_name": worker.full_name if worker else None,
            "status": complaint.status,
        }

    category_label = category.value.replace("_", " ").title()
    if complaint.status == "assigned":
        response_text = f"Your {category_label} complaint has been filed (complaint #{complaint.id}) and assigned to a worker."
    else:
        response_text = f"Your {category_label} complaint has been filed (complaint #{complaint.id}). It's pending assignment to a worker in your area."

    return {
        "response_text": _localize(response_text, state, config),
        "routed_to": "COMPLAINT_CREATED",
        "complaint_id": complaint.id,
        "complaint_data": {"id": complaint.id, "status": complaint.status, "ward": complaint.ward},
        "worker_assignment": worker_assignment,
        "sources": [],
        # Write back even when recovered from conversation_history (e.g. category came from an
        # earlier turn, not the current message) -- otherwise the API response's own
        # `service_category` field would silently disagree with what was actually just filed,
        # even though the complaint itself was created correctly using the recovered value.
        "service_category": category.value,
        "complaint_workflow_state": "CONFIRMED",
    }


# ------------------------------------------------------------------
# response_generation -- terminal node. Every flow node above already sets response_text/
# routed_to/etc.; this node fills in any field a flow node didn't set (so AskJanMitraResponse
# construction never has to guess), then acts as this graph's FINAL RESPONSE GROUNDING /
# VALIDATION stage (GROUNDING #2 -- see this module's top-of-file docstring for the distinction
# from GROUNDING #1, the RAG/rules context `rag_flow_node`/`location_node` gather BEFORE
# planning/reasoning). PRODUCTION ARCHITECTURE UPGRADE: this node's name/graph-node-id
# (`"response_generation"`) is kept unchanged -- renaming it purely to match an architecture
# diagram's terminology would have broken tests/test_orchestration_graph.py's existing exhaustive
# node-set/routing assertions (real, uncommitted P0-era safety work this phase was explicitly
# told to keep intact) for zero functional benefit; what changed is what this node actually DOES,
# not what it's called.
#
# Every check below is deterministic (no LLM call -- consistent with this whole module's
# established convention, see intent_classifier.py's own docstring for why intent-adjacent
# decisions here are never a model call) and is a BACKSTOP, not the primary defense --
# complaint_flow_node's confirmation gate is what actually prevents an unconfirmed complaint from
# being created; these checks only catch the case where the FINAL text/state still disagrees with
# what actually happened, which should structurally never occur but costs little to verify
# explicitly.
#
# SELF-CHECK -> REPLAN -> RE-VALIDATE, bounded: a real, executed loop (see `_MAX_GROUNDING_
# REPLANS`), not merely "correct once and hope" -- the corrected response is re-run through the
# SAME checks before being accepted, so this stage can prove convergence rather than assume it.
# The "replan" itself is a deterministic recomputation of the correct response DIRECTLY from
# already-verified state -- never a second LLM call/retry against the same state, which could
# simply repeat the same mistake (or a different one) each time; a bounded, deterministic
# correction is the safer choice, and is why one retry is always enough here (every correction
# below is a fixed, already-safe template -- see `_run_grounding_checks`'s own docstring for why
# a corrected response can never itself trip these same checks again).
# ------------------------------------------------------------------


# P0 SAFETY FIX (Part 12): specific phrases ("has been filed"/"has been registered"/"has been
# created"), not bare words like "filed"/"assigned" -- those bare words also appear in perfectly
# legitimate STATUS-CHECK responses about an EXISTING complaint (see status_flow_node's own
# status_text templates, e.g. "assigned to a worker, awaiting their acceptance."), which never
# set `complaint_id` on the response and must not be flagged.
_UNSAFE_COMPLETION_CLAIM_PHRASES = ("has been filed", "has been registered", "has been created")

# PRODUCTION ARCHITECTURE UPGRADE: the one `routed_to` value that legitimately carries a
# `complaint_id` -- every other value (RAG/status/clarification/out-of-scope/capabilities/
# greeting/unclear/awaiting-confirmation/cancelled/creation-failed/...) must never carry one. This
# generalizes the text-phrase check above (which only catches an unsafe CLAIM in the wording) into
# a structural invariant over the actual typed fields the API response is built from (see
# ask_janmitra_service.py's `_run()`, which reads `complaint_id`/`routed_to` straight off this
# graph's final state, never inferring either from `response_text`) -- the two checks catch
# different failure shapes: wording that lies vs. state that disagrees with itself.
_ROUTED_TO_IMPLYING_COMPLAINT_CREATED = "COMPLAINT_CREATED"

# One replan attempt is always sufficient here (see the module-level comment above for why), but
# this is still a real, enforced bound -- not an assumption -- so a future new check that DOESN'T
# converge in one correction fails loudly (via the "still failing" branch below) instead of
# looping forever.
_MAX_GROUNDING_REPLANS = 1


def _run_grounding_checks(response_text: str, routed_to: str, sources: list, complaint_id: Any, insufficient_knowledge: bool) -> list[str]:
    """Pure check pass -- no mutation, no side effects. Returns the list of failed check names
    (empty means PASS). Called once per attempt by `response_generation_node`'s bounded replan
    loop; also what proves a replanned response actually converged, by being called again on the
    corrected values."""
    failed: list[str] = []

    claims_completion = any(phrase in response_text.lower() for phrase in _UNSAFE_COMPLETION_CLAIM_PHRASES)
    if claims_completion and not complaint_id:
        failed.append("unsafe_completion_claim")

    if routed_to == "RAG" and not sources and not insufficient_knowledge:
        failed.append("rag_zero_sources_not_flagged")

    if complaint_id and routed_to != _ROUTED_TO_IMPLYING_COMPLAINT_CREATED:
        failed.append("complaint_id_without_created_routing")

    return failed


def _replan_response(checks_failed: list[str], state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """REPLAN step: deterministically recomputes the fields needed to fix every check named in
    `checks_failed` -- never a re-generation of the SAME response, always a fixed, already-safe
    honest fallback derived directly from what's actually verified (see this module's own
    docstring for why this is safer than a second LLM attempt)."""
    correction: dict[str, Any] = {}
    if "unsafe_completion_claim" in checks_failed:
        logger.error("Ask Sarthi response_generation: response text claimed complaint completion with no complaint_id -- replanning.")
        correction["response_text"] = _localize(
            "I couldn't complete that request. Please try again, or use the complaint form directly.", state, config,
        )
        correction["routed_to"] = "COMPLAINT_CREATION_FAILED"
    if "rag_zero_sources_not_flagged" in checks_failed:
        logger.error("Ask Sarthi response_generation: RAG response has zero sources and is not flagged insufficient_knowledge -- replanning.")
        correction["response_text"] = _localize(
            "I couldn't find reliable information for that. Please try again, or contact your local municipal office directly.",
            state, config,
        )
        correction["insufficient_knowledge"] = True
    if "complaint_id_without_created_routing" in checks_failed:
        logger.error("Ask Sarthi response_generation: complaint_id present without COMPLAINT_CREATED routing -- replanning.")
        correction["response_text"] = _localize(
            "I couldn't complete that request. Please try again, or use the complaint form directly.", state, config,
        )
        correction["routed_to"] = "COMPLAINT_CREATION_FAILED"
        correction["complaint_id"] = None
    return correction


def response_generation_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    root = _trace_root(config)
    defaults: dict[str, Any] = {}
    if "response_text" not in state:
        defaults["response_text"] = _localize("I'm not sure how to help with that yet.", state, config)
    if "sources" not in state:
        defaults["sources"] = []
    if "routed_to" not in state:
        defaults["routed_to"] = "NONE_OUT_OF_SCOPE"

    response_text = state.get("response_text") or defaults.get("response_text") or ""
    routed_to = state.get("routed_to") or defaults.get("routed_to")
    sources = state["sources"] if "sources" in state else defaults.get("sources")
    complaint_id = state.get("complaint_id")
    insufficient_knowledge = bool(state.get("insufficient_knowledge"))

    grounding_span = tracing.start_child_run(
        root, "final_response_grounding", "chain",
        inputs={"routed_to": routed_to, "has_complaint_id": bool(complaint_id), "source_count": len(sources or [])},
    )

    checks_failed = _run_grounding_checks(response_text, routed_to, sources, complaint_id, insufficient_knowledge)
    replans = 0
    while checks_failed and replans < _MAX_GROUNDING_REPLANS:
        correction = _replan_response(checks_failed, state, config)
        defaults.update(correction)
        response_text = correction.get("response_text", response_text)
        routed_to = correction.get("routed_to", routed_to)
        complaint_id = correction.get("complaint_id", complaint_id) if "complaint_id" in correction else complaint_id
        insufficient_knowledge = correction.get("insufficient_knowledge", insufficient_knowledge)
        replans += 1
        # RE-VALIDATE: the corrected response must actually pass before being accepted -- this is
        # what makes this a real "replan then re-check" cycle, not "correct once and assume".
        checks_failed = _run_grounding_checks(response_text, routed_to, sources, complaint_id, insufficient_knowledge)

    if checks_failed:
        # Bounded-retry circuit breaker: should be unreachable (every correction above is a fixed
        # template that can never itself trip these same checks -- see `_replan_response`'s own
        # docstring), but if a future check is added that doesn't converge in one replan, fail
        # loudly and safely here rather than looping or shipping an ungrounded response.
        logger.critical(
            "Ask Sarthi response_generation: final response STILL failed grounding after %d replan(s): %s -- "
            "forcing safest possible fallback.", replans, checks_failed,
        )
        defaults["response_text"] = _localize("I couldn't complete that request. Please try again.", state, config)
        defaults["routed_to"] = "COMPLAINT_CREATION_FAILED"
        defaults["complaint_id"] = None

    defaults["grounding_passed"] = not checks_failed
    defaults["grounding_checks_failed"] = checks_failed
    defaults["grounding_replan_count"] = replans
    tracing.end_run(
        grounding_span,
        outputs={"grounding_passed": not checks_failed, "checks_failed": checks_failed, "replan_count": replans},
    )

    return defaults
