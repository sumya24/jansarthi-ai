"""LangGraph node functions for the Ask Sarthi orchestrator.

Every node is a thin adapter: it reads what it needs from `GraphState`/the injected deps and
calls into an EXISTING service (`classify()`, `LocationExtractor`, `LocationResolver`,
`RagRetriever`, `AnswerGenerationService`, `ComplaintAgent`, `assign_next_worker`, `Complaint` DB
queries) -- no node contains business logic that didn't already exist somewhere in this codebase
before this phase. See `graph.py`'s module docstring for the full node/edge list, and
`docs/ask_sarthi_orchestration.md` for the architectural reasoning.

**Deliberate behavior change, confirmed with the user before implementing (see git history /
session transcript)**: previously, a TYPE_A_COMPLAINT-classified question ("Street light not
working near me") was answered by RAG, the same as a TYPE_B question -- Ask Sarthi could not
yet act on a complaint-shaped message, only describe relevant civic-service information about it.
This phase closes that gap: TYPE_A_COMPLAINT now routes to `complaint_flow_node`, which files a
real complaint via the existing `ComplaintAgent`/`assign_next_worker` services and returns a
complaint ID -- matching this phase's own spec, whose worked complaint-flow example ("Streetlight
near my home is not working.") is itself a TYPE_A-shaped sentence. TYPE_B_SERVICE_INFO ("who do I
contact for X", "how do I apply for Y") still routes to `rag_flow_node`, unchanged. This
necessarily changes several previously-tested TYPE_A cases -- see tests/test_ask_sarthi.py's
updated assertions and the final report for the full list.

**"Your saved city" feature (confirmed with the user before implementing)**: `complaint_flow_node`
no longer files a complaint against ANY real, currently-staffed ward it happens to resolve, with
no regard for whether that ward is even in the citizen's own registered city -- previously, typing
"...in Pune" filed the complaint for Pune even for a citizen whose own Settings-saved city was,
say, Bengaluru, with no acknowledgment of the mismatch, making the citizen's own saved location
setting meaningless to this flow. The confirmation prompt now offers a third option, "Change
location" (`_CONFIRMATION_OPTIONS`), which asks which ward/area to use instead; a REPLACEMENT
that's in a genuinely different city than the citizen's own saved one is refused with an
actionable message ("update your location in Settings first"), never silently filed. A
replacement that's simply a DIFFERENT WARD in the SAME city is always allowed with no extra
check -- this only guards against a genuine cross-city mismatch, mirroring the Report an Issue
form's own ward-dropdown scoping (defaults to the citizen's own city, never forbids picking
another ward within it). See `_wards_same_city`/`_ward_city` and the `awaiting_location_change`
handling inside `complaint_flow_node` for the implementation.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy.orm import Session

from backend.config import settings
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
    detect_multiple_categories,
    is_explicit_cancellation,
    is_explicit_confirmation,
    is_explicit_location_change_request,
    looks_like_an_attempted_yes_or_no,
)
from backend.services.location_extractor import (
    LocationExtractor,
    LocationResolution,
    known_aliases_for_city,
    looks_like_it_names_an_unrecognized_place,
)
from backend.services.location_resolver import LocationResolver, ResolvedLocation
from backend.services.observability import tracing
from backend.services.orchestration.state import GraphState
from backend.services.rag_answer_cache import get_cached_answer, store_answer
from backend.services.rag_retriever import RagRetriever, chunk_context_label
from backend.services.sarvam_client import AIServiceError
from backend.services.translation_service import TranslationService

logger = logging.getLogger(__name__)

_COMPLAINT_NUMBER_PATTERN = re.compile(r"(?:complaint|complain)\s*#?\s*(\d+)|#(\d+)|\b(\d{2,6})\b", re.IGNORECASE)

# Real Sarvam pricing for its translation models (confirmed against docs.sarvam.ai/api/
# getting-started/pricing, checked 2026-08-26): Rs20/10K characters, billed per character, same
# rate for both sarvam-translate:v1 and mayura:v1 (`_localize` below only ever uses the former --
# it always translates FROM English, never auto-detects a source). Reported in real Indian Rupees,
# not converted to USD -- same reasoning as answer_generation_service.py's per-token rate (see that
# module's `_SARVAM_INPUT_COST_PER_TOKEN_INR` comment): this app's real Sarvam bill is in Rupees,
# and Phoenix's own dashboard hardcodes a "$" prefix regardless of what currency the number
# actually represents, so a USD conversion would only add a second, needless approximation. Billed
# character count isn't spelled out beyond "per character" on that page, so this uses the input
# text length (the one number known before the call is made) -- the one real approximation left.
_SARVAM_TRANSLATE_COST_PER_CHAR_INR = 20 / 10_000

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
_IMAGE_NO_TEXT_OPTIONS = ["Report an issue", "What is this?"]
_INTENT_AMBIGUOUS_OPTIONS = ["Report a problem", "What is the procedure?"]
_CONFIRMATION_OPTIONS = ["Yes, submit it", "No, cancel", "Change location"]

# LIVE-REPORTED BUG: every quick-reply button's clicked VALUE is always one of these fixed,
# hardcoded English strings (see `_localize_options`'s own docstring for why) -- never organic
# typed text, regardless of its length or whether it happens to be phrased as a question. A
# citizen clicking a Hindi-labeled "प्रक्रिया क्या है?" button sends back "What is the procedure?"
# -- a real, complete English QUESTION, so `language_node`'s existing short-reply skip (scoped to
# short, non-question replies) never applied to it at all: real detection ran on that literal
# English text, correctly identified it as English on its own narrow terms, and silently flipped
# an entire Hindi conversation back to English on this one button click. `language_node` treats an
# EXACT match against any of these as the same "known, not organic" signal regardless of shape.
_ALL_QUICK_REPLY_OPTIONS = frozenset(
    _CATEGORY_CLARIFICATION_OPTIONS
    + _LOCATION_CLARIFICATION_OPTIONS
    + _IMAGE_NO_TEXT_OPTIONS
    + _INTENT_AMBIGUOUS_OPTIONS
    + _CONFIRMATION_OPTIONS
)


@dataclass
class GraphDeps:
    """Static, shared-across-requests dependencies -- built once (expensive: the embedding model
    load, the Chroma collection open) and reused for every graph invocation, matching this
    codebase's existing "construct once in the service constructor" pattern (see
    ask_sarthi_service.py, which this class's fields were lifted from unchanged)."""

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
    # Set only by ask_sarthi_service.ask_with_image() -- the already-validated-and-written
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
    (same service `complaint_agent.py`/`ask_sarthi_service.py` already use for worker-facing
    complaint translation -- no new client, no new logic). Unlike `rag_flow_node`'s answers,
    these strings never went through an LLM prompted to answer in the target language in the
    first place, so without this they stay in English even in a fully Marathi/Hindi/etc.
    conversation -- confirmed via a live Marathi voice-assistant session where the transcript and
    every UI label were in Marathi but this exact clarification text came back in English.

    English is a no-op (skips a needless network call on the common case). A translation failure
    degrades to the original English text -- same honest fallback `AnswerGenerationService` and
    the voice flow's own TTS step already use -- never blocks the response.

    LIVE-REPORTED GAP, closed here: this is a real Sarvam API call (`sarvam-translate:v1`), just
    on a completely different endpoint from `AnswerGenerationService.generate()`'s chat-completion
    call -- it used to run with no span at all, so real, billable translation usage was invisible
    in both LangSmith and Phoenix (a citizen conversing in Marathi/Hindi/etc. triggers this on
    every single non-RAG reply -- greetings, clarifications, out-of-scope notices -- yet none of
    that spend ever showed up anywhere). Traced the same way `answer_generation`'s span is: a
    `model_name` + `total_cost_inr` pair in `outputs`, which `tracing._phoenix_end_span()` already
    knows how to promote to Phoenix's dedicated LLM attributes -- no changes needed there. No
    token-count attributes are set (translation is billed per character, not per token; setting
    them would misrepresent this span's cost basis on Phoenix's Metrics view)."""
    language = state.get("response_language") or "en"
    if language == "en":
        return text
    deps = _deps(config)
    if deps.translation_service is None:
        return text
    root = _trace_root(config)
    span = tracing.start_child_run(root, "response_translation", "llm", inputs={"target_language": language, "input_char_count": len(text)})
    try:
        translated = deps.translation_service.to_language(text, language)
    except AIServiceError as exc:
        logger.warning("Ask Sarthi: localizing response text to %s failed, keeping English: %s", language, exc)
        tracing.end_run(span, error=str(exc))
        return text
    tracing.end_run(
        span,
        outputs={
            "model_name": "sarvam-translate:v1",
            "output_char_count": len(translated),
            "total_cost_inr": len(text) * _SARVAM_TRANSLATE_COST_PER_CHAR_INR,
            # LIVE-REPORTED GAP: Phoenix's own "Top models by cost/tokens" dashboard widgets only
            # ever read a registered model's per-token price times a token-count attribute -- they
            # never read `total_cost_inr` above directly (that only feeds each trace's OWN
            # Attributes tab, already correct on its own). Translation is billed per CHARACTER, not
            # per token, so there is no real "token count" to give it -- this reuses Phoenix's
            # token-count slot to carry the real character count instead (the same quantity
            # `total_cost_inr` above was computed from), with a matching per-"million-characters"
            # price registered for this model name in Phoenix's own Settings/Models (see
            # PHOENIX_TRACING_PLAN.md). The real cost is unaffected either way; only Phoenix's own
            # axis label says "tokens" when it means characters here -- a cosmetic mismatch, same
            # spirit as the "$" prefix meaning Rupees everywhere else in this app's Phoenix setup.
            "prompt_tokens": len(text),
            "total_tokens": len(text),
        },
    )
    return translated


def _localize_options(options: list[str], state: GraphState, config: RunnableConfig) -> list[str]:
    """LIVE-REPORTED GAP: `_localize` (above) already translates every clarification/confirmation
    question's own free-text sentence into `response_language`, but the CLICKABLE quick-reply
    buttons underneath it (`follow_up_options` -- "Yes, submit it"/"No, cancel", the category
    words, "Use current location", ...) stayed hardcoded English regardless -- a citizen
    conversing entirely in Marathi still saw the surrounding sentence translated but had to read
    an English button to answer it.

    Deliberately does NOT touch what actually gets SENT when a button is clicked (see
    AskSarthiResponse.follow_up_options_labels's own docstring for the display-vs-value split
    this requires) -- `is_explicit_confirmation`/`is_explicit_cancellation` and every category/
    location keyword match in this file are tested against the exact, canonical English strings in
    `follow_up_options` itself; only ever-so-slightly-different translator output for the SAME
    button could otherwise silently fail to register as a real "yes" and leave a citizen stuck
    re-confirming forever. This only produces the parallel, display-only label list -- one
    `_localize` call per option, same graceful English-on-failure fallback as every other call to
    it."""
    return [_localize(option, state, config) for option in options]


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
    `_route_after_language` in graph.py and `clarification_flow_node` below).

    Also where `photo_evidence` gets set (see state.py's own field docstring and schemas/
    ask_sarthi.py's PhotoEvidenceRef) -- ctx.image_saved is already validated-and-written-to-disk
    by the time this node runs (ask_sarthi_service.py's `_process_image()`), regardless of
    whether this turn ever becomes a complaint; recording the reference here, in the ONE place
    every image-carrying request already passes through, means every later node (and the eventual
    AskSarthiResponse) sees it without each caller needing its own copy of this logic."""
    message = state.get("user_message", "").strip()
    has_image = bool(state.get("has_image"))
    image_description = state.get("image_description")
    # `config.get(...)`, not `_ctx(config)` -- unlike every other node in this file, this one is
    # exercised directly with a bare `config={}` by unit tests that predate `photo_evidence`
    # (no `configurable`/`ctx` at all), so `ctx.image_saved` must degrade to "no photo" rather
    # than raising.
    ctx = config.get("configurable", {}).get("ctx")
    image_saved = getattr(ctx, "image_saved", None)
    photo_evidence = (
        {
            "filename": image_saved.filename,
            "original_name": image_saved.original_name,
            "content_type": image_saved.content_type,
            "size": image_saved.size,
        }
        if image_saved is not None
        else None
    )

    if not message and has_image:
        return {
            "normalized_message": "",
            "input_type": state.get("input_type") or "text",
            "clarification_reason": "image_no_text",
            "photo_evidence": photo_evidence,
        }

    if image_description:
        message = f"{message}\n\n[Attached photo shows: {image_description}]"

    return {
        "normalized_message": message,
        "input_type": state.get("input_type") or "text",
        "photo_evidence": photo_evidence,
    }


def language_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Auto-detects the citizen's ACTUAL input language from this turn's own text and uses that
    for `response_language`, rather than blindly trusting `original_language` (the client-supplied
    `AskSarthiRequest.language` -- in practice, the UI's language-toggle setting).

    Live-reported gap this fixes: a citizen's UI language toggle reflects whatever they picked
    once, in general -- it does NOT reliably track what language any ONE message actually is,
    since real bilingual citizens routinely type/speak in a different language than their toggle
    without changing it first. Before this fix, the app would answer in the TOGGLE's language
    regardless of what was actually asked -- unlike ChatGPT/Claude, which answer in whatever
    language the message itself is in. Applies uniformly to text AND voice turns (a voice turn's
    `normalized_message` is its STT transcript by the time this node runs) and to typed AND
    spoken input alike -- one detection mechanism, not a per-input-mode special case.

    Falls back to `original_language` whenever there's nothing to detect from (no text at all --
    an image-only turn) or detection itself doesn't yield a usable answer (service
    unavailable/failed, or it correctly detected a real language this app has no
    SUPPORTED_LANGUAGES/TTS coverage for -- see `TranslationService.detect_language`'s own
    docstring) -- a best-effort upgrade, never a hard dependency that can block or fail a turn.

    ALSO falls back (deliberately skips detection entirely) for a short, non-question, PLAIN-ASCII
    reply -- same `_MAX_CONTINUATION_REPLY_WORDS`/`_looks_like_question` heuristic
    intent_classifier.py's own continuation-detection already uses, and for the identical
    underlying reason: a bare "yes"/"ok"/a single ward name typed in English is not a reliable
    statement of what language the citizen is communicating in, and mid-conversation is exactly
    the wrong moment to silently flip response_language on such a low-signal turn -- e.g. a
    citizen who has been asking entirely in Marathi replying with the plain English confirmation
    word "yes" (see intent_classifier.py's `_CONFIRMATION_EXACT_WORDS["en"]`) must not suddenly
    get the rest of that turn's response back in English.

    LIVE-REPORTED BUG: the plain-ASCII condition (`text.isascii()`) was added after this skip
    fired on a citizen's very FIRST message of a brand-new conversation -- "माझ्या घराजवळ कचरा
    साचला आहे, कोलकातामध्ये." ("Garbage has piled up near my house, in Kolkata.", a genuine,
    complete complaint, not a reply to anything) happens to be exactly 6 words and isn't phrased
    as a question, so detection was skipped entirely and every reply for the rest of that
    complaint (clarification AND confirmation prompts) came back in English instead of Marathi.
    Unlike "yes"/"ok", real Devanagari/Bengali/Gujarati/Oriya script is NEVER ambiguous about not
    being English, regardless of word count -- the short-reply ambiguity this heuristic guards
    against is specifically an English-vs-something-else guess on plain Latin script text, so
    non-ASCII text should always go through real detection instead of being skipped.

    SECOND LIVE-REPORTED BUG, found immediately after the multilingual quick-reply buttons shipped
    (see `_localize_options`'s own docstring): a button's clicked VALUE always stays canonical
    English regardless of what language its LABEL was shown in (a safety requirement -- see that
    docstring), which means this skip condition fires on essentially EVERY quick-reply click, not
    a rare edge case. Falling back straight to `original_language` (the client-supplied UI toggle)
    here is exactly the unreliable signal this whole node exists to bypass -- a citizen conversing
    entirely in Hindi who clicks a Hindi-labeled "Report a problem" button had every following
    reply silently flip back to English. Fixed by preferring the conversation's own ESTABLISHED
    language instead: `_established_conversation_language` detects it from the last ASSISTANT
    turn's own text (a real, full sentence -- far more reliable to detect from than this turn's
    short reply), falling back to `original_language` only when there's no assistant turn yet (a
    genuinely first message).

    THIRD LIVE-REPORTED BUG, found immediately after the second fix above shipped: the short/non-
    question/ASCII skip above still didn't cover "What is the procedure?" -- one of the exact same
    fixed quick-reply VALUES the second bug is about, just one that happens to be phrased as a real
    English question. Clicking its Hindi-labeled counterpart ("प्रक्रिया क्या है?") sent back that
    literal English text, which real `_looks_like_question` correctly identifies as a question, so
    THIS skip never applied to it -- real detection ran on the literal English sentence, correctly
    identified it as English on its own narrow terms, and flipped an established Hindi conversation
    back to English on this one button click. Every quick-reply value is equally "not organic typed
    text" regardless of its own shape (see `_ALL_QUICK_REPLY_OPTIONS`'s own docstring) -- checked
    as its own, unconditional first branch, before the length/question-shape heuristic even runs."""
    fallback = state.get("original_language") or "en"
    text = (state.get("normalized_message") or "").strip()
    if not text:
        return {"response_language": fallback}
    if text in _ALL_QUICK_REPLY_OPTIONS:
        established = _established_conversation_language(state, config)
        return {"response_language": established or fallback}
    if (
        text.isascii()
        and len(text.split()) <= _MAX_CONTINUATION_REPLY_WORDS
        and not _looks_like_question(text)
    ):
        established = _established_conversation_language(state, config)
        return {"response_language": established or fallback}
    try:
        deps = _deps(config)
    except (KeyError, TypeError):
        return {"response_language": fallback}
    if deps.translation_service is None:
        return {"response_language": fallback}
    detected = deps.translation_service.detect_language(text)
    if detected:
        return {"response_language": detected}
    # LIVE-REPORTED BUG (voice input): real detection was genuinely ATTEMPTED here, not skipped --
    # Sarvam's own text-lid returned language_code=null (confirmed directly against the live API)
    # for a real case: "हो दासल तर.", a short, slightly garbled voice-transcribed reply -- not
    # enough signal for Sarvam's own model to identify ANY language at all, not even a wrong one.
    # Falling straight to the stale client-supplied `original_language` here is the exact same
    # unreliable-signal problem `_established_conversation_language` already exists to avoid for
    # the skipped-detection cases above -- this unifies all THREE "no real per-message detection
    # result" cases (skipped for a known quick-reply value, skipped for a short plain-ASCII reply,
    # or genuinely failed/unavailable) behind the same fallback preference, instead of only the
    # first two.
    return {"response_language": _established_conversation_language(state, config) or fallback}


def _established_conversation_language(state: GraphState, config: RunnableConfig) -> str | None:
    """The fallback `language_node` uses for a short, low-signal reply instead of trusting the
    stale client-supplied `original_language` (see that node's own SECOND live-reported bug) --
    detects the language of the most recent ASSISTANT turn's own text, a real, full sentence that
    is far more reliable to detect language from than the short reply itself. None (the caller
    then falls back further, to `original_language`) when there's no assistant turn yet, detection
    is unavailable, or it fails -- never raises, matching every other best-effort call to
    `detect_language` in this file."""
    history = state.get("conversation_history") or []
    last_assistant_text = next(
        (turn.get("content") for turn in reversed(history) if turn.get("role") == "assistant" and turn.get("content")),
        None,
    )
    if not last_assistant_text:
        return None
    try:
        deps = _deps(config)
    except (KeyError, TypeError):
        return None
    if deps.translation_service is None:
        return None
    return deps.translation_service.detect_language(last_assistant_text)


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
# English only for now (no real Sarvam-translated fragments captured yet, unlike every other
# group above -- see this module's own note on how those were verified) -- the explicit
# `complaint_workflow_state` fast-path in `_last_assistant_turn_state`/`_last_turn_invites_
# complaint_reply` is the PRIMARY signal for this one (every current caller echoes that field),
# so a citizen replying in a non-English UI language is unaffected even before this marker text
# has real translations; this is only the same safe fallback the other groups keep for an
# older/non-compliant caller that predates the state field.
_LOCATION_CHANGE_PROMPT_MARKERS = ("which ward or area would you like to use instead",)
_COMPLAINT_FLOW_PROMPT_MARKERS = (
    _CATEGORY_CLARIFICATION_MARKERS
    + _LOCATION_CLARIFICATION_MARKERS
    + _CONFIRMATION_PROMPT_MARKERS
    + _INTENT_AMBIGUOUS_CLARIFICATION_MARKERS
    + _LOCATION_CHANGE_PROMPT_MARKERS
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
    and `AskSarthiResponse.complaint_workflow_state`'s own docstrings for the full round-trip).
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
        # confirmation prompt itself. AWAITING_LOCATION_CHANGE: the citizen picked "Change
        # location" on that confirmation prompt and is now being asked which ward/area to use
        # instead (see complaint_flow_node's own handling). All three invite a reply that
        # continues the SAME complaint flow; CONFIRMED/CANCELLED are terminal -- the next message
        # starts fresh (see GraphState.complaint_workflow_state's own docstring for the full value
        # list).
        return explicit_state in ("DRAFT", "AWAITING_CONFIRMATION", "AWAITING_LOCATION_CHANGE")
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
    # LIVE-REPORTED BUG (voice input): a citizen's own perfectly clear "Yes, can you submit
    # please?" -- a natural, SPOKEN way to confirm, unlike the terser typed/button "yes, submit
    # it" -- ends in "?", so `is_explicit_confirmation` correctly declines to auto-confirm it
    # (see that function's own docstring: a "yes" that's part of a further QUESTION is
    # deliberately never auto-confirmed, e.g. "yes, what are the rules?" must not silently file
    # anything). That safety boundary is right and untouched here -- the actual gap is
    # downstream: `is_continuation_reply` being False meant this whole message fell through to
    # UNCLEAR -> unclear_flow_node's generic "I'm not sure I understood that... what would you
    # like help with?" -- which doesn't even acknowledge a complaint confirmation was pending,
    # reading as if the whole draft had been silently abandoned (it hadn't -- nothing here risks
    # actually losing it, see below). `complaint_flow_node` ALREADY handles exactly this shape
    # correctly (see its own `elif awaiting_confirmation:` branch below in this file): neither
    # confirms nor cancels, safely re-recovers the draft from history, and re-asks the SAME
    # confirmation question again -- the honest, correct behavior.
    #
    # FIRST VERSION of this fix routed on `_awaiting_confirmation(history)` ALONE, regardless of
    # this message's own content -- live-caught as too broad by this project's own regression
    # suite: "What is the current time?", asked mid an unrelated pending confirmation, ALSO got
    # routed to complaint_flow_node and re-shown the stale confirmation, reading as a bizarre
    # non-answer to a real, unrelated question instead of the honest "I don't understand"
    # unclear_flow_node correctly gave it before. `looks_like_an_attempted_yes_or_no` (see its own
    # docstring) narrows this to only fire when the reply's own FIRST WORD is a recognized yes/no
    # word -- "Yes, can you submit please?" qualifies (fixing the original bug); "What is the
    # current time?" does not (fixing the regression this second pass caught), so it correctly
    # falls through to UNCLEAR exactly as before either fix.
    awaiting_confirmation = _awaiting_confirmation(history)
    if intent == QuestionIntent.UNCLEAR and (
        (last_turn_invites_reply and is_continuation_reply) or state.get("has_image") or has_gps
        or (awaiting_confirmation and looks_like_an_attempted_yes_or_no(text))
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


def _should_skip_home_ward_fallback(ctx: RequestContext, text: str) -> bool:
    """True whenever the citizen already gave SOME location signal this turn that a fallback to
    their own registered ward (or a real worker match recovered from conversation history) must
    never silently override.

    LIVE-REPORTED BUG ("Pune fallback"), a FIFTH instance: the original version of this gate only
    checked `looks_like_it_names_an_unrecognized_place` -- a heuristic that only recognizes text
    SHAPED like a real place name (a preposition + a capitalized word). That correctly covers a
    message like "...in Pune?", but not an EXPLICIT `ctx.location_text` reply that's pure
    gibberish (e.g. typing "asdkjhaskjdh" in answer to "what is the location?") -- gibberish
    doesn't look like a place name at all, so the heuristic alone let the gate pass, and the
    citizen's home ward got silently substituted for an answer they never gave. But by the time
    any caller of this function is even reached, `ctx.location_text` -- if set -- has ALREADY been
    tried and failed to resolve by an earlier tier (see `_resolve_location`'s own first check): its
    mere presence here already means "the citizen gave a signal AND it didn't resolve", the exact
    situation `location_explicit_signal_unresolved` was built to recognize and never paper over
    with a substitution. Checking it directly, before ever falling back to the heuristic, closes
    that gap without touching the heuristic itself (still needed for the plain-message-text case,
    where there is no separate explicit field to check)."""
    return bool(ctx.location_text) or looks_like_it_names_an_unrecognized_place(text)


def _resolve_location(state: GraphState, config: RunnableConfig) -> LocationResolution:
    """Same priority order as the pre-graph `AskSarthiService._resolve_location()` this
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

    # LIVE-REPORTED BUG: this scan used to have no stopping point at all -- it kept looking
    # backward through the ENTIRE conversation history for anything resolvable, with no check for
    # whether the conversation had already moved on to something else. Live-reproduced: several
    # turns about Kolkata (streetlights, road repairs), fully answered, then a citizen asked "What
    # is the process for a new water connection in Pune?" -- Pune isn't in this app's knowledge
    # base at all, so the CURRENT text correctly resolves to nothing, but this scan then reached
    # straight past the unrelated, already-closed Kolkata exchanges and answered as if the
    # question had been about Kolkata -- "I don't currently have reliable information for this in
    # Kolkata," silently substituting a city the citizen never named for this question. Exactly
    # the failure mode this app's own test script explicitly calls out as unacceptable ("NOT an
    # answer borrowed from another city"). Fixed the same way as the identical gap in
    # `_recover_category_from_history` -- stop the instant the scan crosses a USER turn whose OWN
    # classification is a confident, complete, standalone, different-topic intent (see
    # `_TOPIC_BOUNDARY_INTENTS`'s own docstring for why this is keyed off the user's message, not
    # the assistant's reply -- an early version of this fix, keyed off the assistant's turn
    # instead, broke a real passing test: "I'm in Mohali." -> "Got it, Mohali." -> "Street light
    # not working." must still recover Mohali, but "Got it, Mohali." is a plain acknowledgment
    # that matches no clarification marker, so that version stopped too early). A genuinely
    # continuing conversation ("I'm in Mohali." -> ... -> "Street light not working.") is
    # unaffected, since "I'm in Mohali." classifies as UNCLEAR, not a boundary intent.
    #
    # SECOND boundary, found live after the above shipped: also stop at an assistant turn that is
    # itself the success confirmation for an already-FILED complaint (see
    # `_turn_closes_a_filed_complaint`'s own docstring) -- a citizen who successfully files a
    # streetlight complaint in Ahmedabad, then starts a brand-new, different complaint elsewhere,
    # must never have the NEW one silently resolve to the OLD complaint's city.
    #
    # THIRD boundary, found live after THAT shipped, and the most insidious: this scan matches an
    # EARLIER user turn's resolvable city just as readily as it matches a genuine one -- including
    # when THIS turn's own text already names a place, just one the gazetteer doesn't cover. A
    # citizen asked about "Zzz Nonexistent Place" (got the honest "I don't have information for
    # this area yet" -- the fix below this loop working correctly), then typed "MUMBAI" as an
    # explicit follow-up (a genuinely different, real question, resolved to Mumbai correctly) --
    # then asked about "Zzz Nonexistent Place" a SECOND time, and got Mumbai's answer again, because
    # this scan reached straight past the current turn's own (unrecognized) place name to the
    # PRIOR turn's real one. Gated the same way as the citizen_home_ward tier just below (see
    # location_extractor.py's `looks_like_it_names_an_unrecognized_place`), and further broadened
    # by `_should_skip_home_ward_fallback`'s own docstring (a FIFTH instance of this same bug
    # class): skip this scan entirely whenever the citizen already gave ANY location signal this
    # turn -- an explicit one that failed to resolve, or a place-shaped phrase in the message text
    # -- so a turn that already carries its own (unresolved) signal can't go fishing through
    # history for a different, real one instead.
    if not _should_skip_home_ward_fallback(ctx, text):
        for turn in reversed(state.get("conversation_history", [])):
            if turn.get("role") == "assistant":
                if _turn_closes_a_filed_complaint(turn):
                    break
                continue
            if turn.get("role") != "user":
                continue
            content = turn.get("content", "")
            if classify(content).intent in _TOPIC_BOUNDARY_INTENTS:
                break
            resolved = extractor.resolve_from_text(content)
            if resolved.city or resolved.state or resolved.is_ambiguous:
                resolved.source = "conversation_history"
                return resolved

    # LIVE-REPORTED BUG ("Pune fallback"): this tier used to fire unconditionally whenever nothing
    # else resolved -- including when the citizen's OWN text named a real, specific place ("...in
    # Pune?") that just isn't in this app's gazetteer, not only when they named no place at all.
    # Silently substituting the citizen's home city there answers a question about one city as if
    # it had been about another, with no indication anything was substituted -- see
    # `looks_like_it_names_an_unrecognized_place`'s own module-level comment in
    # location_extractor.py for the heuristic's own reasoning -- broadened by
    # `_should_skip_home_ward_fallback`'s own docstring (a FIFTH instance of this bug: gibberish
    # typed directly into the explicit location field doesn't "look like a place name" either, so
    # the heuristic alone let it through) to also skip whenever `ctx.location_text` was used at all
    # this turn, resolved or not.
    if ctx.user.ward and not _should_skip_home_ward_fallback(ctx, text):
        resolved = extractor.resolve_from_text(ctx.user.ward)
        if resolved.city or resolved.state or resolved.is_ambiguous:
            resolved.source = "citizen_home_ward"
            return resolved

    return LocationResolution(source="none")


def location_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    resolution = _resolve_location(state, config)
    ctx = _ctx(config)
    nothing_resolved = not (resolution.city or resolution.state or resolution.is_ambiguous)
    # True only when the citizen gave an EXPLICIT location signal (the "Use current location"/
    # "Select location" UI, or typed free text passed as ctx.location_text) that still didn't
    # resolve to anywhere recognizable -- distinct from never having given one at all. See
    # clarification_flow_node's default branch for why this distinction has its own message.
    explicit_signal_unresolved = nothing_resolved and bool(ctx.location_text)
    # DISTINCT from the above: the citizen never used the explicit location field at all, but
    # their MESSAGE TEXT names a real-sounding place this app simply doesn't cover (the "Pune
    # fallback" bug -- see location_extractor.py's own `looks_like_it_names_an_unrecognized_place`
    # comment). Live-reported confusion this closes: reusing the SAME "I couldn't recognize that as
    # a location" wording for this case too technically isn't wrong (the gazetteer genuinely has no
    # entry for it), but reads oddly for a real, well-known place (a citizen asking about Pune
    # doesn't want to be told Pune "wasn't recognized as a location") -- kept as its OWN flag so
    # clarification_flow_node can give it separate, more accurate wording, while row 7-11-style
    # explicit gibberish location replies (see the manual test script's Section 8) keep their
    # existing, already-correct "couldn't recognize" wording untouched.
    text = state.get("normalized_message") or state.get("user_message", "")
    message_names_unresolved_place = (
        nothing_resolved and not ctx.location_text and looks_like_it_names_an_unrecognized_place(text)
    )
    return {
        "location_city": resolution.city,
        "location_state": resolution.state,
        "location_source": resolution.source,
        "location_is_ambiguous": resolution.is_ambiguous,
        "location_ambiguous_candidates": resolution.ambiguous_candidates,
        "location_explicit_signal_unresolved": explicit_signal_unresolved,
        "location_message_names_unresolved_place": message_names_unresolved_place,
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
    already ran, see ask_sarthi_service.py's `_process_image()`). Real, not padding: says
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
        options = _IMAGE_NO_TEXT_OPTIONS
        return {
            "response_text": _localize(question, state, config),
            "follow_up_required": True,
            "follow_up_question": question,
            "follow_up_options": options,
            "follow_up_options_labels": _localize_options(options, state, config),
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
        options = _INTENT_AMBIGUOUS_OPTIONS
        return {
            "response_text": _localize(_image_context_prefix(state) + question, state, config),
            "follow_up_required": True,
            "follow_up_question": question,
            "follow_up_options": options,
            "follow_up_options_labels": _localize_options(options, state, config),
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
            "follow_up_options_labels": _localize_options(_CATEGORY_CLARIFICATION_OPTIONS, state, config),
            "routed_to": "NONE_CLARIFICATION_NEEDED",
            "sources": [],
        }

    # PRE-EXISTING BUG FIX (found via a broader location-clarification test matrix): checking
    # `state.get("location_is_ambiguous")` directly, not just `reason == "location_ambiguous"`.
    # complaint_flow_node sets that reason itself before routing here (see its own
    # "location_ambiguous" branch), but graph.py's `_route_after_location` routes the civic-info
    # (TYPE_B) path straight here on an ambiguous match WITHOUT ever setting `clarification_reason`
    # first -- a conditional-edge function in LangGraph only picks the next node, it can't also
    # update state. Without this, a civic-info question with a genuinely ambiguous location (e.g.
    # "Maharashtra" -- both Mumbai and Nagpur have real data) silently fell through to the generic
    # "What is the location?" question below, discarding the candidate list `location_node` had
    # already resolved, instead of asking "Which city -- Mumbai, Nagpur?" as intended.
    if reason == "location_ambiguous" or state.get("location_is_ambiguous"):
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

    # Location missing entirely -- THREE different situations collapsed into one branch:
    # (a) the citizen never gave a location signal of any kind, (b) they explicitly picked one
    # (via "Use current location"/"Select location", or typed free text) and it simply didn't
    # resolve to anywhere recognizable, or (c) their message itself named a real-sounding place
    # (e.g. "...in Pune?") that this app just doesn't cover (see location_node's own comment on
    # `location_message_names_unresolved_place` for why this needs separate wording from (b) --
    # "couldn't recognize" reads oddly for a real, well-known place). Without distinguishing (a)
    # from (b)/(c), a citizen who just gave SOME location signal sees the EXACT SAME "What is the
    # location?" question again with no acknowledgment anything happened -- indistinguishable from
    # the assistant ignoring their answer, live-reported as feeling like a stuck loop.
    if state.get("location_explicit_signal_unresolved"):
        question = "I couldn't recognize that as a location. Please try typing a city or area name, or use \"Use current location\"."
    elif state.get("location_message_names_unresolved_place"):
        question = "I don't have information for this area yet. Please try a nearby city, or a different area name."
    else:
        question = "What is the location? This helps me give you the correct local information."
    return {
        "response_text": _localize(_image_context_prefix(state) + question, state, config),
        "follow_up_required": True,
        "follow_up_question": "What is the location?",
        "follow_up_options": _LOCATION_CLARIFICATION_OPTIONS,
        "follow_up_options_labels": _localize_options(_LOCATION_CLARIFICATION_OPTIONS, state, config),
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
# RAG flow -- unchanged retrieval pipeline (see docs/ask_sarthi_rag_architecture.md), this node
# only orchestrates the existing RagRetriever + AnswerGenerationService calls.
# ------------------------------------------------------------------

_LANGUAGE_NAMES = {"en": "English", "hi": "Hindi", "mr": "Marathi", "or": "Odia", "gu": "Gujarati", "bn": "Bengali"}


def rag_flow_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    deps = _deps(config)
    root = _trace_root(config)
    text = state.get("normalized_message") or state.get("user_message", "")
    category = state.get("service_category")
    # LIVE-REPORTED BUG: a citizen's own complaint-shaped message ("...garbage has piled up...")
    # got asked "Are you reporting a problem with Waste Sanitation, or would you like information
    # about it?" -- clicking the INFO option ("What is the procedure?") carries no category of
    # its own (that bare phrase names no service), and unlike the "Report a problem" side of this
    # exact same fork (see `_recover_text_before_intent_ambiguous_turn`'s own docstring), this RAG/
    # info path had NO equivalent recovery at all -- the retrieval ran with category=None, matched
    # whatever chunk best fit the generic phrase "What is the procedure?" plus the resolved
    # location, and answered about a COMPLETELY DIFFERENT service (water supply, when the citizen
    # asked about garbage). Reuses the SAME `_recover_category_from_history` the complaint flow
    # already relies on (already scoped to TYPE_A_COMPLAINT/TYPE_A_MAYBE turns only, and already
    # stops at a topic/complaint boundary -- see that function's own docstring) -- just extended to
    # this second call site, since the citizen's original message satisfies its exact contract
    # either way.
    if category is None:
        recovered_category = _recover_category_from_history(state)
        category = recovered_category.value if recovered_category else None
    category_enum = ServiceCategory(category) if category else None
    # `response_language` (auto-detected by language_node, see its own docstring), NOT
    # `original_language` (the raw, possibly-stale client-supplied value) -- this is the exact
    # bug the auto-detection fix would otherwise be silently undone by: rag_flow_node is the RAG
    # answer path every TYPE_B civic-info question takes, so reading the un-detected field here
    # would mean the LLM keeps answering in the UI toggle's language regardless of what the
    # citizen actually asked in, no matter how correct language_node's own detection is upstream.
    language_name = _LANGUAGE_NAMES.get(state.get("response_language") or "en", "English")

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
    # Same fix as `language_name` above: the cache key must be keyed on the language actually
    # answered in (response_language), not the raw client-supplied one -- otherwise two turns
    # detected into the SAME real language, but with different stale `original_language` values,
    # would wrongly miss each other's cache entry (or worse, a cache HIT could serve an answer
    # generated for a different language than this turn was just detected into).
    language_code = state.get("response_language") or "en"
    location_city = state.get("location_city")
    location_state = state.get("location_state")
    cached_answer = get_cached_answer(ctx.db, text, language_code, category, location_city, location_state)
    if cached_answer is not None:
        answer_text, was_llm_generated = cached_answer, True
        token_usage = None
        tracing.end_run(answer_span, outputs={"answer_was_llm_generated": True, "cache_hit": True})
    else:
        answer_text, was_llm_generated, token_usage = deps.answer_service.generate(text, context_chunks, language_name, context_labels)
        answer_outputs = {"answer_was_llm_generated": was_llm_generated, "cache_hit": False, "answer": tracing.redact_text(answer_text)}
        if token_usage:
            # Only tagged when a real LLM call actually happened this turn (token_usage is only
            # non-None on that path, see AnswerGenerationService.generate()'s own docstring) --
            # a fallback/cache-hit answer wasn't produced by any model just now, so it gets no
            # model tag, same reasoning as why it gets no token counts either. Lets Phoenix's
            # Metrics view group "top model by cost/tokens" -- otherwise those charts have
            # nothing to group by, even though the raw token counts are present.
            answer_outputs.update(token_usage)
            answer_outputs["model_name"] = settings.LLM_MODEL
        tracing.end_run(answer_span, outputs=answer_outputs)
        # Never cache the no-LLM-available fallback (raw chunk echo) -- only a genuinely
        # LLM-generated answer, so a temporary credits/network outage can never freeze a
        # degraded answer into the cache for everyone else who asks the same thing later.
        if was_llm_generated:
            store_answer(ctx.db, text, language_code, category, location_city, location_state, answer_text)
        else:
            # LIVE-REPORTED BUG: the raw chunk echoed above is the knowledge base's own stored
            # text, always English -- unlike `answer_service.generate()`'s own LLM path (prompted
            # to answer `in {language_name}`), this fallback was never translated at all. A
            # Hindi/Marathi asker who hit this path (confirmed live: a genuine 45s Sarvam
            # reasoning-model timeout on answer_generation_service.generate()) saw an English
            # excerpt glued to the one Hindi sentence `in_app_note` below adds separately --
            # reading as broken, not just "degraded". `_localize` is the same fast
            # translate-only call already used for every other hardcoded/fallback string in this
            # module (never the slow reasoning model), so this doesn't reintroduce the timeout
            # risk that caused the fallback in the first place.
            answer_text = _localize(answer_text, state, config)

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

    # PRODUCT GAP FIX (live-reported): `answer_text` is grounded ONLY in the retrieved civic-info
    # documents (see this function's own system-prompt rule), so it only ever describes the
    # traditional municipal channel -- it never mentions this app's OWN in-app complaint-filing
    # feature, since that's not something any retrieved document could ever say. The "Report
    # Issue"/"Track Complaint" buttons are added separately by the frontend (AskSarthi.tsx), but
    # a citizen who only hears the spoken/TTS answer (no buttons at all, see ask_voice()) or who
    # doesn't notice the buttons never learns the option exists. Appended here, deterministically
    # (never left to the LLM to phrase/decide whether to mention) and localized like every other
    # hardcoded string in this module. Skipped for a "new connection" question -- applying for a
    # new connection isn't a "problem" this app's Report Issue flow (built for existing-service
    # complaints) handles, so suggesting it there would be actively wrong, not just unhelpful.
    if not state.get("requests_new_connection"):
        in_app_note = "You can also report this directly through JanSarthi AI using the \"Report Issue\" option."
        answer_text = f"{answer_text}\n\n{_localize(in_app_note, state, config)}"

    return {
        "response_text": answer_text,
        "sources": sources,
        "verification_status": overall_status,
        "routed_to": "RAG",
        "answer_was_llm_generated": was_llm_generated,
        # See GraphState's own docstring for these three -- only non-None when a real LLM call
        # happened THIS turn (never on a cache hit or the no-LLM-available fallback).
        "ai_cost_inr": token_usage.get("total_cost_inr") if token_usage else None,
        "ai_model_name": settings.LLM_MODEL if token_usage else None,
        "ai_total_tokens": token_usage.get("total_tokens") if token_usage else None,
    }


# ------------------------------------------------------------------
# agent flow -- the supervisor/multi-agent node for a genuinely multi-category question (see
# docs/ask_sarthi_orchestration.md §17 for the future-agent integration point this fills, and
# intent_classifier.py's detect_multiple_categories() for the deterministic gate that routes here
# instead of rag_flow -- see graph.py's _route_after_location).
#
# Deliberately NOT an autonomous/reasoning agent: no LLM decides which categories to query, how
# many times, or in what order -- the category list is already decided (by
# detect_multiple_categories(), before this node ever runs) via the same deterministic keyword
# matching every other routing decision in this graph already uses (see graph.py §7's "no LLM
# performs simple routing" discipline). What's genuinely new here is calling RagRetriever/
# AnswerGenerationService more than once for a single request and combining the results into one
# response -- not any new tool-selection capability. Deliberately simpler than rag_flow_node in
# two ways, both documented rather than silently dropped: no RagAnswerCache integration (each
# category's answer is a fresh generate() call every time) and no "new connection" post-filter
# (out of scope for a genuinely multi-category message) -- both easy to add later if a real need
# for them shows up here specifically.
#
# BUG FIX (code review, efficiency): each category's retrieve()+generate() pair is completely
# independent of every other category's -- nothing in one iteration depends on a prior one's
# result -- so running them one after another made a 3-category question take roughly 3x a
# single-category question's wall-clock time (each category pays its own real Sarvam network
# round-trip). Now run concurrently via a thread pool. This is NOT a new class of risk for this
# codebase: FastAPI already serves multiple SIMULTANEOUS citizen requests by running this exact
# same shared RagRetriever/AnswerGenerationService singletons concurrently across its own request
# threadpool -- `_process_one_category()` below just does, within one request, the identical
# concurrent-call shape this app's services already have to tolerate across requests every day.
# ------------------------------------------------------------------


def _process_one_category(
    deps: GraphDeps,
    text: str,
    category: ServiceCategory,
    location_city: str | None,
    location_state: str | None,
    language_name: str,
    root: object | None,
) -> dict[str, Any]:
    """One category's full retrieve+generate step for `agent_flow_node`, factored out so it can
    run on its own thread pool worker -- returns a plain, self-contained result dict (never
    mutates anything shared with other categories), so the caller can safely run several of these
    concurrently and merge the results afterward."""
    category_span = tracing.start_child_run(
        root, f"agent_rag_{category.value.lower()}", "retriever", inputs={"category": category.value},
    )
    outcome = deps.retriever.retrieve(text, category, location_city, location_state)
    tracing.end_run(
        category_span,
        outputs={"result_count": len(outcome.results), "insufficient_knowledge": outcome.insufficient_knowledge},
    )
    category_label = category.value.replace("_", " ").title()
    if outcome.insufficient_knowledge or not outcome.results:
        return {
            "section": f"**{category_label}**: I don't currently have reliable information for this.",
            "sources": [], "was_llm_generated": False, "token_usage": None,
        }

    context_chunks = [r.metadata.get("content", "") for r in outcome.results]
    context_labels = [chunk_context_label(r) for r in outcome.results]
    answer_text, was_llm_generated, token_usage = deps.answer_service.generate(
        text, context_chunks, language_name, context_labels
    )
    sources = [
        {
            "source_id": r.metadata.get("source_id"),
            "source_title": r.metadata.get("source_title"),
            "source_organization": r.metadata.get("source_organization"),
            "source_url": r.metadata.get("source_url"),
            "source_type": r.metadata.get("source_type"),
            "verification_status": r.metadata.get("verification_status"),
            "geographic_scope": r.metadata.get("geographic_scope"),
        }
        for r in outcome.results
    ]
    return {
        "section": f"**{category_label}**: {answer_text}",
        "sources": sources,
        "was_llm_generated": was_llm_generated,
        "token_usage": token_usage,
    }


def agent_flow_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    deps = _deps(config)
    root = _trace_root(config)
    text = state.get("normalized_message") or state.get("user_message", "")
    categories = detect_multiple_categories(text)
    language_name = _LANGUAGE_NAMES.get(state.get("response_language") or "en", "English")
    location_city = state.get("location_city")
    location_state = state.get("location_state")

    agent_span = tracing.start_child_run(
        root, "agent_flow", "chain",
        inputs={"query": tracing.redact_text(text), "categories": [c.value for c in categories]},
    )

    # ThreadPoolExecutor.map() preserves input order in its results -- sections/source-dedup below
    # stay in the same deterministic category order as before, even though the work itself now
    # runs concurrently. max_workers is capped at a small, fixed ceiling (not literally
    # len(categories)) purely as a sane upper bound -- detect_multiple_categories() never returns
    # more than a handful of categories in practice.
    with ThreadPoolExecutor(max_workers=min(len(categories), 8) or 1) as executor:
        results = list(executor.map(
            lambda category: _process_one_category(
                deps, text, category, location_city, location_state, language_name, root,
            ),
            categories,
        ))

    sections: list[str] = []
    all_sources: list[dict[str, Any]] = []
    seen_source_ids: set[str] = set()
    any_llm_generated = False
    any_cost_known = False
    total_cost_inr = 0.0
    total_tokens = 0

    for result in results:
        sections.append(result["section"])
        any_llm_generated = any_llm_generated or result["was_llm_generated"]
        token_usage = result["token_usage"]
        if token_usage:
            any_cost_known = True
            total_cost_inr += token_usage.get("total_cost_inr") or 0.0
            total_tokens += token_usage.get("total_tokens") or 0
        for source in result["sources"]:
            source_id = source["source_id"]
            if source_id in seen_source_ids:
                continue
            seen_source_ids.add(source_id)
            all_sources.append(source)

    # Only "insufficient" if EVERY category came back with nothing usable -- a citizen who
    # reported 3 issues and got 2 real answers plus one honest "don't have that" section still
    # got real, useful help, unlike rag_flow_node's single-category all-or-nothing case.
    insufficient_knowledge = not all_sources
    statuses = {s["verification_status"] for s in all_sources}
    overall_status = "MIXED" if len(statuses) > 1 else next(iter(statuses), None)

    response_text = "\n\n".join(sections)
    # BUG FIX (code review): this node was missing the same "Report Issue" in-app note
    # rag_flow_node appends (see that node's own "PRODUCT GAP FIX" comment) -- a real, live
    # inconsistency where a multi-category question got a lower-quality answer than the
    # identical sub-question asked alone. Same two gates rag_flow_node uses: skipped for a "new
    # connection" question (not a "problem" this app's Report Issue flow handles), and skipped
    # when every category came back insufficient (nothing to "also report" if nothing was
    # actually answered) -- appended once for the whole combined answer, not per-section.
    if not state.get("requests_new_connection") and not insufficient_knowledge:
        in_app_note = "You can also report this directly through JanSarthi AI using the \"Report Issue\" option."
        response_text = f"{response_text}\n\n{_localize(in_app_note, state, config)}"

    tracing.end_run(agent_span, outputs={"category_count": len(categories), "source_count": len(all_sources)})

    return {
        "response_text": response_text,
        "sources": all_sources,
        "verification_status": overall_status,
        "routed_to": "RAG_MULTI_CATEGORY",
        "insufficient_knowledge": insufficient_knowledge,
        "answer_was_llm_generated": any_llm_generated,
        "ai_cost_inr": total_cost_inr if any_cost_known else None,
        "ai_model_name": settings.LLM_MODEL if any_cost_known else None,
        "ai_total_tokens": total_tokens if any_cost_known else None,
    }


# ------------------------------------------------------------------
# complaint flow -- NEW this phase: actually files a complaint via the existing
# ComplaintAgent/assign_next_worker services (see this module's docstring for the confirmed
# behavior-change rationale).
# ------------------------------------------------------------------


_RECOVERABLE_INTENTS = (QuestionIntent.TYPE_A_COMPLAINT, QuestionIntent.TYPE_A_MAYBE)

# Stopping boundary for the category/location history-recovery scans below -- a USER turn whose
# OWN classification is one of these represents a confident, complete, standalone exchange on a
# topic separate from any ongoing complaint (a civic-info question that got its own full RAG
# answer, a status lookup, a capabilities question). Once a backward scan crosses one of these, it
# must stop -- everything before it belongs to an earlier, disconnected conversation.
#
# FIRST ATTEMPT AT THIS BOUNDARY (kept here as a documented dead end, not silently deleted):
# checking whether the LAST ASSISTANT turn was itself a clarification-question/DRAFT state. That
# broke a real, existing, passing test the moment it shipped
# (test_conversation_follow_up_uses_prior_location) -- "I'm in Mohali." -> "Got it, Mohali." ->
# "Street light not working." must still recover Mohali, but "Got it, Mohali." is a plain
# acknowledgment that matches NONE of this file's own clarification markers and carries no
# `complaint_workflow_state` either, so that version stopped the scan immediately and lost a
# genuinely still-open conversation. Keying off the USER's own message instead fixes both cases at
# once: "I'm in Mohali." classifies as UNCLEAR (compatible with "still building the same
# complaint", scan continues), while "How do I report a broken streetlight in Kolkata?" (the
# live-reported bug's actual boundary) classifies as TYPE_B_SERVICE_INFO (a real stop).
_TOPIC_BOUNDARY_INTENTS = (QuestionIntent.TYPE_B_SERVICE_INFO, QuestionIntent.TYPE_C_STATUS, QuestionIntent.CAPABILITIES)

# SECOND, INDEPENDENT boundary, found live AFTER the fix above shipped: a citizen filed a real
# streetlight complaint (category+location resolved, confirmed, complaint #19 actually created),
# then started a completely NEW, unrelated complaint with "I want to file a complaint." (no
# category named at all) -- and the scan still answered "Streetlights, in the same ward" again,
# because "I want to file a complaint." is itself TYPE_A_MAYBE, not one of the TYPE_B/TYPE_C/
# CAPABILITIES intents above, so the USER-message boundary never fired. A complaint attempt
# reaching ANY terminal outcome is its own, separate kind of "the conversation moved on" -- even
# though every turn in it (the description, "yes") is complaint-shaped start to finish, once that
# attempt is DONE (whichever way), anything after it is unambiguously a fresh start, never a
# continuation of the same one.
#
# THIRD, INDEPENDENT boundary, found live AFTER *that* fix shipped too, this time in a completely
# different scan (`_resolve_worker_ward_text`'s own "last resort" tier below, not the
# category/location ones above): a citizen filed a real streetlight complaint in Ahmedabad,
# started and then CANCELLED a second one, then asked about garbage in Mohali (a real place, but
# with no staffed worker there). The honest answer should have been "Sarthi has no workers in
# Mohali yet" -- instead, the ward-recovery scan reached past the CANCELLED attempt's own
# confirmation prompt (which still names Ahmedabad, from the FIRST, already-filed complaint) and
# silently reused that ward. A cancelled/rejected/failed attempt is JUST as terminal as a
# successful one -- "has been filed" alone missed this because a cancellation was never a
# completion claim in the first place, so it was never in that marker set.
#
# All FOUR outcomes below are the complete set of terminal `complaint_workflow_state` values this
# file's own complaint_flow_node ever returns (CONFIRMED success, and three different CANCELLED
# paths: explicit "no", no-workers-available, and a create_complaint() failure) -- see each
# marker's own originating `response_text` a few hundred lines above this file. Reused here for the
# same reason `_UNSAFE_COMPLETION_CLAIM_PHRASES` already treats "has been filed" as a hard signal
# elsewhere in this file: these are the deterministic text markers a caller that (like today's
# actual frontend) never echoes back `complaint_workflow_state` can still recognize reliably.
_COMPLAINT_TERMINAL_MARKERS = (
    "has been filed", "has been registered", "has been created",  # CONFIRMED (success)
    "i won't submit that complaint",  # CANCELLED (explicit "no")
    "doesn't currently have workers set up",  # CANCELLED (honest no-coverage rejection)
    "i couldn't file that complaint",  # CANCELLED (create_complaint() failure)
)


def _turn_closes_a_filed_complaint(turn: dict) -> bool:
    """True when this ASSISTANT turn is complaint_flow_node's own response for a complaint attempt
    that just reached ANY terminal outcome -- filed successfully, explicitly cancelled, honestly
    rejected for having no coverage, or failed to create -- a definitive boundary for the
    category/location/ward history-recovery scans in this file. Checked ALONGSIDE
    `_TOPIC_BOUNDARY_INTENTS` (which is keyed off user turns), not instead of it -- the two catch
    different shapes of "the conversation moved on"."""
    if turn.get("role") != "assistant":
        return False
    explicit_state = turn.get("complaint_workflow_state")
    if explicit_state is not None:
        return explicit_state in ("CONFIRMED", "CANCELLED")
    content = (turn.get("content") or "").lower()
    return any(marker in content for marker in _COMPLAINT_TERMINAL_MARKERS)


def _recover_photo_evidence_from_history(state: GraphState) -> dict[str, Any] | None:
    """LIVE-REPORTED REQUEST: a citizen who attaches a photo, then answers a follow-up
    clarification, then confirms with plain text -- three separate requests -- expects the SAME
    photo on the eventual complaint, matching how the dedicated "Report an Issue" form behaves
    (one request, so it never has this problem). The file is already validated and saved to disk
    the moment it's first uploaded (see PhotoEvidenceRef's own docstring); what's missing on a
    LATER, photo-less turn is just a way to re-find that reference. Scans conversation_history,
    most recent first, for the last assistant turn's own echoed `photo_evidence` (see
    ConversationTurn.photo_evidence) -- same two stopping points as every other history-recovery
    scan in this file (`_TOPIC_BOUNDARY_INTENTS` on a user turn, `_turn_closes_a_filed_complaint`
    on an assistant turn), so a photo from an unrelated, already-closed exchange is never reused
    for a brand-new complaint."""
    for turn in reversed(state.get("conversation_history") or []):
        if turn.get("role") == "assistant":
            if _turn_closes_a_filed_complaint(turn):
                break
            photo_evidence = turn.get("photo_evidence")
            if photo_evidence:
                return photo_evidence
            continue
        if turn.get("role") != "user":
            continue
        if classify(turn.get("content", "")).intent in _TOPIC_BOUNDARY_INTENTS:
            break
    return None


# Reverses the exact `category.value.replace("_", " ").title()` formatting the `intent_ambiguous`
# branch of clarification_flow_node uses to build its own question text (see
# `_recover_photo_context_from_intent_ambiguous_turn` below) -- e.g. ServiceCategory.ROADS_POTHOLES
# ("ROADS_POTHOLES") -> "Roads Potholes".
_CATEGORY_LABEL_TO_ENUM = {cat.value.replace("_", " ").title(): cat for cat in ServiceCategory}

# Matches the internal "[Attached photo shows: ...]" marker wherever it appears in
# `complaint_text` (see input_processing_node's own docstring for where it's first added, and
# `_recover_photo_context_from_intent_ambiguous_turn`/`_recover_from_confirmation_prompt_text` for
# where it's reconstructed on a later turn).
_PHOTO_CAPTION_MARKER_PATTERN = re.compile(r"\[Attached photo shows:\s*(?P<caption>.+?)\]", re.IGNORECASE | re.DOTALL)


def _humanize_complaint_text_for_storage(text: str) -> str:
    """LIVE-REPORTED BUG: a complaint filed from a photo (with no separate typed description, or
    recovered across turns via the "Report a problem" shortcut) stored its bracketed, code-shaped
    "[Attached photo shows: ...]" marker VERBATIM as the complaint's title/summary -- shown as-is
    on CitizenDashboard/CitizenComplaintDetail, right next to the actual photo the persistence fix
    now also attaches, so the citizen saw the same sentence twice: once in brackets as a
    heading, once again as the "summary" line below it. That marker is load-bearing everywhere
    UPSTREAM of complaint creation (`_awaiting_confirmation`'s prompt-matching, the cancellation
    note, the "wasn't attached" note just above this function's call site all key off its exact
    text) -- so it's left untouched everywhere else, and only unwrapped into plain prose here,
    right at the point `complaint_text` is about to become permanent, citizen-facing storage."""
    return _PHOTO_CAPTION_MARKER_PATTERN.sub(lambda m: m.group("caption").strip(), text).strip()

# Matches the EXACT combined text `_image_context_prefix` + clarification_flow_node's own
# `intent_ambiguous` branch produce together -- see both of those for where each half comes from.
_INTENT_AMBIGUOUS_PHOTO_TURN_PATTERN = re.compile(
    r"I can see the photo you attached \(it looks like: (?P<caption>.+?)\)\.\s*"
    r"Are you reporting a problem with (?P<category_label>.+?), or would you like information about it\?",
    re.IGNORECASE | re.DOTALL,
)


def _recover_photo_context_from_intent_ambiguous_turn(state: GraphState) -> tuple[ServiceCategory | None, str | None]:
    """LIVE-REPORTED REQUEST ("more ChatGPT/Claude-like reasoning"): a citizen who attached a photo
    with ambiguous text got asked "Are you reporting a problem with Roads Potholes, or would you
    like information about it?" (see clarification_flow_node's `intent_ambiguous` branch) --
    clicking "Report a problem" used to start category resolution completely fresh, because the
    photo's own caption only ever lived in THAT assistant turn's text, never anywhere
    `_recover_category_from_history` re-checks (it only scans USER turns). The citizen had to
    re-answer a category the system had, in effect, already worked out from the photo, and even
    after picking one, the stored complaint description was a bare button-click word ("Road")
    instead of anything describing the actual photo.

    Looks at the LAST assistant turn specifically for that exact combined message and, if found,
    recovers BOTH the category (reversing `_CATEGORY_LABEL_TO_ENUM`'s own formatting) and the
    photo's caption -- reformatted with the SAME "[Attached photo shows: ...]" marker
    `input_processing_node` uses, so every later consumer of `complaint_text` (RAG grounding,
    ComplaintAgent, ...) sees the identical shape it already expects, not a one-off variant.
    Deliberately narrow: only the immediately preceding turn, and only this one, precise, known
    message shape -- never a general "guess a caption from anywhere in history" heuristic."""
    history = state.get("conversation_history") or []
    if not history:
        return None, None
    last = history[-1]
    if last.get("role") != "assistant":
        return None, None
    match = _INTENT_AMBIGUOUS_PHOTO_TURN_PATTERN.search(last.get("content") or "")
    if not match:
        return None, None
    category = _CATEGORY_LABEL_TO_ENUM.get(match.group("category_label").strip())
    caption = match.group("caption").strip()
    return category, (f"[Attached photo shows: {caption}]" if caption else None)


def _recover_text_before_intent_ambiguous_turn(state: GraphState) -> str | None:
    """LIVE-REPORTED BUG, found live in Hindi: `_recover_photo_context_from_intent_ambiguous_turn`
    above only ever recovers a complaint's description when the intent_ambiguous turn was PHOTO-
    driven (its regex requires the "I can see the photo you attached..." prefix, English only) --
    the far more common TEXT-ONLY case (a citizen's own complaint-shaped message, e.g. "...कचरा
    जमा हो गया है, कोलकाता में", got asked "Are you reporting a problem with Waste Sanitation, or
    would you like information about it?") had NO recovery at all. Clicking "Report a problem"
    correctly routed into the complaint flow and correctly recovered the CATEGORY (both
    `_last_turn_invites_complaint_reply` and `_recover_category_from_history` already work
    correctly here, language-independent by construction) -- but the stored complaint DESCRIPTION
    was left as the bare button label itself ("Report a problem"), overwriting the citizen's own
    words entirely.

    Unlike the photo-caption recovery above, this needs no per-language regex/capture group at
    all: it reuses the SAME multi-language `_INTENT_AMBIGUOUS_CLARIFICATION_MARKERS` list every
    other language-aware check in this file already relies on just to CONFIRM the last assistant
    turn was this clarification (in any of the 6 supported languages), then simply takes the USER
    turn immediately before it -- whatever language that happens to be in, verbatim, exactly like
    a citizen who'd re-typed their own original message instead of clicking the button."""
    history = state.get("conversation_history") or []
    if len(history) < 2:
        return None
    last = history[-1]
    if last.get("role") != "assistant":
        return None
    content = (last.get("content") or "").lower()
    if not any(marker in content for marker in _INTENT_AMBIGUOUS_CLARIFICATION_MARKERS):
        return None
    prior = history[-2]
    if prior.get("role") != "user":
        return None
    text = (prior.get("content") or "").strip()
    return text or None


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
    complaint.

    LIVE-REPORTED FOLLOW-UP: the scan above ALSO had no stopping point at all -- it kept scanning
    backward through the ENTIRE history, with no check for whether the conversation had already
    moved on to something else in between. Live-reproduced: a citizen's complaint draft ("Garbage"
    -> Waste Sanitation) was left dangling (never confirmed or cancelled); a completely unrelated
    exchange happened afterward (a Nagpur streetlight question, fully answered); the citizen then
    asked a vague, unrelated message again ("is this report is true") -- and this scan reached
    RIGHT PAST the unrelated streetlight exchange to reattach the old, abandoned "Garbage"
    category to the brand-new message. Fixed by stopping the scan the moment it crosses a USER
    turn whose OWN classification is a confident, complete, standalone, different-topic intent
    (see `_TOPIC_BOUNDARY_INTENTS`'s own docstring for why this is keyed off the user's message,
    not the assistant's reply) -- anything before that turn belongs to an earlier, now-closed
    topic and must never be reached. A genuinely continuing complaint (category asked in turn 1,
    "I'm in Mohali."/"Use my current location." in a later turn) is unaffected: neither classifies
    as one of the boundary intents, so the scan never stops early for that case.

    THIRD boundary, live-reported after both fixes above: a citizen filed a real streetlight
    complaint (confirmed, complaint #19 created), then started a brand-new, different complaint
    with "I want to file a complaint." (no category at all) -- and this scan STILL answered
    "Streetlights" again, because "I want to file a complaint." is TYPE_A_MAYBE, not one of
    `_TOPIC_BOUNDARY_INTENTS`, so that boundary never fired for it. A successfully filed complaint
    is its own kind of "moved on", even when every turn in it was complaint-shaped start to
    finish -- see `_turn_closes_a_filed_complaint`, now also checked here."""
    for turn in reversed(state.get("conversation_history", [])):
        if turn.get("role") == "assistant":
            if _turn_closes_a_filed_complaint(turn):
                break
            continue
        if turn.get("role") != "user":
            continue
        result = classify(turn.get("content", ""))
        if result.intent in _TOPIC_BOUNDARY_INTENTS:
            break
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


def _awaiting_location_change(conversation_history: list[dict]) -> bool:
    """Same two-tier shape as `_awaiting_confirmation` just above (explicit state first, marker-
    text fallback second) -- true when the LAST turn is Sarthi's own "which ward or area would you
    like to use instead?" prompt, so THIS turn's message should be read as the citizen's
    REPLACEMENT location, not a fresh complaint description or a confirmation/cancellation reply."""
    explicit_state = _last_assistant_turn_state(conversation_history)
    if explicit_state is not None:
        return explicit_state == "AWAITING_LOCATION_CHANGE"
    return _last_assistant_turn_matches(conversation_history, _LOCATION_CHANGE_PROMPT_MARKERS)


# Matches `_build_confirmation_prompt`'s own EXACT summary text (see that function) -- DOTALL
# because `complaint_text` can itself contain a literal newline (the "[Attached photo shows: ...]"
# suffix `input_processing_node` appends is on its own blank-line-separated line).
_CONFIRMATION_PROMPT_TEXT_PATTERN = re.compile(
    r'Your complaint would be about (?P<category_label>.+?) in "(?P<ward>.*?)":\s*"(?P<text>.+?)"\.\s*'
    r"Would you like me to submit this complaint\?",
    re.DOTALL,
)


def _recover_from_confirmation_prompt_text(state: GraphState) -> tuple[ServiceCategory | None, str | None]:
    """LIVE-REPORTED BUG, found right after `_recover_photo_context_from_intent_ambiguous_turn`
    shipped: that fix lets "Report a problem" recover a category straight from the photo
    clarification, skipping the "What issue would you like to report?" round-trip entirely -- but
    `_recover_complaint_draft_from_history` below (used on the VERY NEXT turn, the actual "yes,
    submit it" reply) only ever re-classifies EARLIER USER turns' own raw text, never anything an
    assistant turn established -- so it found nothing, and confirming silently fell back to asking
    for a category all over again. The category/description recovered via the photo shortcut
    never existed as a classify()-able USER turn to re-find.

    Cuts out the middleman: the confirmation prompt about to be replied to is ALREADY the most
    authoritative record of "what complaint is this," in Sarthi's own words -- parses it directly
    (reversing the same category-label formatting `_CATEGORY_LABEL_TO_ENUM` reverses) instead of
    re-deriving it from history a second, less reliable way. Checked as a FALLBACK only, after the
    existing user-turn scan below finds nothing -- adds coverage for this gap without touching
    the already-working, already-tested path every other confirmation reply still uses."""
    history = state.get("conversation_history") or []
    if not history:
        return None, None
    last = history[-1]
    if last.get("role") != "assistant":
        return None, None
    match = _CONFIRMATION_PROMPT_TEXT_PATTERN.search(last.get("content") or "")
    if not match:
        return None, None
    category = _CATEGORY_LABEL_TO_ENUM.get(match.group("category_label").strip())
    return category, match.group("text").strip()


def _recover_ward_from_confirmation_prompt_text(state: GraphState) -> str | None:
    """LOCATION-CONFIRMATION FEATURE: the ward this complaint would ACTUALLY be filed under is
    whatever the last confirmation prompt itself echoed back and asked the citizen to confirm --
    the single most authoritative source there is, since it's literally the text the citizen just
    said "yes" to. Needed specifically for a citizen who used "Change location" to switch wards:
    without this, re-deriving the ward from scratch via `_resolve_worker_ward_text`'s own
    conversation-history scan tier can pick the WRONG one when two real workers share the same
    city -- that scan matches on CITY name substring per worker (see LocationResolver.
    find_worker_ward_text's own docstring; deliberately left untouched, see this codebase's
    instruction to preserve it), so it can return an EARLIER, no-longer-relevant same-city ward
    instead of the one actually just confirmed. Checked FIRST, before that broader scan ever runs,
    whenever a confirmation prompt is what's being replied to -- not just for the location-change
    case, since the same worked example ("does the confirmed ward always match what was shown")
    should hold true unconditionally, not just for this new feature."""
    history = state.get("conversation_history") or []
    if not history:
        return None
    last = history[-1]
    if last.get("role") != "assistant":
        return None
    match = _CONFIRMATION_PROMPT_TEXT_PATTERN.search(last.get("content") or "")
    if not match:
        return None
    ward = match.group("ward").strip()
    return ward or None


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
    as if it were a real complaint). Same stopping-point fixes as that function too (see
    `_TOPIC_BOUNDARY_INTENTS` and `_turn_closes_a_filed_complaint`) -- this scan must not reach
    past an unrelated, already-closed exchange -- or past an already-successfully-filed complaint
    -- into an even older, disconnected complaint attempt either."""
    for turn in reversed(state.get("conversation_history", [])):
        if turn.get("role") == "assistant":
            if _turn_closes_a_filed_complaint(turn):
                break
            continue
        if turn.get("role") != "user":
            continue
        content = turn.get("content", "")
        if is_explicit_confirmation(content) or is_explicit_cancellation(content):
            continue
        result = classify(content)
        if result.intent in _TOPIC_BOUNDARY_INTENTS:
            break
        if result.service_category is not None and result.intent in _RECOVERABLE_INTENTS:
            return result.service_category, content
    # Fallback -- see `_recover_from_confirmation_prompt_text`'s own docstring for the exact gap
    # this closes (a category recovered via the photo-clarification shortcut has no classify()-able
    # USER turn for the scan above to find).
    category, recovered_text = _recover_from_confirmation_prompt_text(state)
    if category is not None:
        return category, recovered_text or (state.get("normalized_message") or state.get("user_message", ""))
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
        # or return nothing usable; test_gps_failure_does_not_break_ask_sarthi exists specifically
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
    # LIVE-REPORTED BUG ("Pune fallback"), a THIRD instance found here, and the most insidious: the
    # scan below matches an assistant turn's OWN echoed ward name ('Your complaint would be about
    # ... in "Bengaluru"') just as readily as a citizen-given one -- including when that echoed
    # ward was ITSELF a wrong substitution from an earlier turn (e.g. this exact Pune-fallback bug
    # firing once for "Street light problem in Atlantis."). Once that happens, the WRONG ward is
    # now sitting in `conversation_history` in Sarthi's own words, and repeating the identical
    # message finds it again here -- a self-reinforcing loop, live-reproduced: every resend of the
    # same "Atlantis" message kept re-offering the same wrong city, because each wrong reply became
    # the next request's own history. Gated the same way as `_resolve_location`'s citizen_home_ward
    # tier and `_resolve_own_ward_worker_text`'s call site above (see location_extractor.py's
    # `looks_like_it_names_an_unrecognized_place`), further broadened by
    # `_should_skip_home_ward_fallback`'s own docstring (a FIFTH instance: gibberish typed directly
    # as an explicit location reply doesn't "look like" a place either): skip this ENTIRE
    # last-resort scan too whenever the citizen already gave ANY location signal this turn, so a
    # turn that already carries its own (unresolved) signal can't go fishing through history for a
    # stand-in city instead. Does not affect the legitimate case this tier exists for (see its own
    # comment below): a bare confirmation reply like "yes, submit it" carries no `ctx.location_text`
    # of its own and doesn't match the heuristic either, so recovering an earlier genuine
    # location_text pick from the echoed confirmation prompt is unaffected.
    if worker_ward_text is None and not _should_skip_home_ward_fallback(ctx, complaint_text):
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
        #
        # LIVE-REPORTED BUG: this scan had no stopping point either -- see
        # `_turn_closes_a_filed_complaint`'s own docstring for the exact live-reported sequence
        # (an Ahmedabad complaint filed, a second one started and CANCELLED, then a citizen asked
        # about Mohali -- which has no staffed worker) that this closes. Without a boundary, this
        # scan reached straight past the cancelled attempt's own confirmation prompt (which still
        # named Ahmedabad, from the FIRST, unrelated, already-filed complaint) and silently reused
        # that ward instead of honestly saying Mohali has no coverage yet.
        for turn in reversed(state.get("conversation_history") or []):
            if turn.get("role") == "assistant" and _turn_closes_a_filed_complaint(turn):
                break
            content = turn.get("content", "")
            if not content:
                continue
            if turn.get("role") == "user" and classify(content).intent in _TOPIC_BOUNDARY_INTENTS:
                break
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


def _ward_city(ward_text: str | None) -> str | None:
    """Extracts the city from a real ward string's "{ward} — {locality}, {city}" convention
    (composeWard() in HomeLocationPicker.tsx/WorkerLocationPicker.tsx, and this same backend's own
    create_worker()/update_worker()) -- the text after the LAST comma, same as frontend
    lib/wardFilter.ts's own parsing. Returns None for text with no comma (a ward strings without
    this convention, e.g. a placeholder test fixture) -- "can't tell" is a real, honest answer
    here, not an error."""
    if not ward_text or "," not in ward_text:
        return None
    city = ward_text.rsplit(",", 1)[-1].strip()
    return city or None


def _wards_same_city(a: str, b: str) -> bool:
    """City-aware match between two ward strings, used only by complaint_flow_node's location-
    change handling to decide whether a citizen-picked replacement ward belongs to their own
    saved city. Same case-insensitive PREFIX match as frontend lib/wardFilter.ts's citiesMatch --
    kept in sync with that file; if one changes, update the other -- specifically to handle
    Karnataka's real "Bengaluru Urban" (a citizen's own registered city, from the Settings
    cascade) vs. plain "Bengaluru" (every real ward string's own suffix) both meaning the same
    city. Deliberately returns True (i.e. "allow it, don't block") whenever EITHER side's city
    can't be parsed at all -- see `_ward_city` -- rather than treating "we couldn't tell" as
    grounds to block a citizen from changing their location; only a CONFIRMED mismatch between two
    known cities is treated as one."""
    city_a, city_b = _ward_city(a), _ward_city(b)
    if not city_a or not city_b:
        return True
    na, nb = city_a.lower(), city_b.lower()
    return na == nb or na.startswith(nb) or nb.startswith(na)


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
    options = _CONFIRMATION_OPTIONS
    return {
        "response_text": _localize(summary, state, config),
        "follow_up_required": True,
        "follow_up_question": "Would you like me to submit this complaint?",
        "follow_up_options": options,
        "follow_up_options_labels": _localize_options(options, state, config),
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
    # LOCATION-CONFIRMATION FEATURE: true when the LAST turn was this function's own "which ward
    # or area would you like to use instead?" prompt (see the "Change location" handling below) --
    # this turn's text is therefore the citizen's REPLACEMENT location, never a fresh complaint
    # description. Uses the same two-tier check `_awaiting_confirmation` does (see
    # `_awaiting_location_change`'s own docstring).
    awaiting_location_change = _awaiting_location_change(history)

    if awaiting_confirmation and is_explicit_cancellation(text):
        # LIVE-REPORTED REQUEST: a citizen who cancelled a photo-attached complaint, then
        # described a genuinely new complaint afterward, got no photo on the new one with no
        # explanation why -- confusing, since nothing said the photo was gone. This backend has no
        # server-side session (see this function's own docstring) and never persists an uploaded
        # photo's actual bytes unless a complaint is genuinely CREATED (see complaint_creation
        # below) -- a cancelled attempt's photo is truly gone, not recoverable, so the honest fix
        # is telling the citizen that up front, right when they cancel, rather than silently
        # reusing a real (but riskier -- see this fix's own scoping conversation) mechanism to
        # smuggle the same photo into a later complaint. Detected via the SAME embedded
        # "[Attached photo shows: ...]" marker `input_processing_node` folds into `normalized_
        # message` for any captioned image (see that node's own docstring) -- which is what
        # `_build_confirmation_prompt` below echoes verbatim into the confirmation prompt just
        # cancelled, so its presence there is a reliable, already-available signal, no new state
        # needed.
        last_turn = history[-1] if history else None
        cancelled_had_photo = bool(
            last_turn and last_turn.get("role") == "assistant"
            and "[attached photo shows:" in (last_turn.get("content") or "").lower()
        )
        cancel_text = "Okay, I won't submit that complaint. Let me know if you'd like to report something else."
        if cancelled_had_photo:
            cancel_text += " Note: the photo you attached isn't kept once a complaint is cancelled -- if you report this again and want a photo included, please attach it again."
        return {
            "response_text": _localize(cancel_text, state, config),
            "routed_to": "NONE_CANCELLED",
            "sources": [],
            "complaint_workflow_state": "CANCELLED",
        }

    confirmed = awaiting_confirmation and is_explicit_confirmation(text)

    # LOCATION-CONFIRMATION FEATURE: a citizen who picks "Change location" on the confirmation
    # prompt is neither confirming nor cancelling -- they want to keep the same complaint but file
    # it somewhere else. Checked BEFORE the fresh-complaint-signal override just below (so a bare
    # "Change location" click, which itself isn't complaint-shaped text, doesn't fall through to
    # the harmless-but-wrong "ask for a category all over again" path) and only when this isn't
    # already a confirm (a "yes" is always unambiguous and takes priority).
    if awaiting_confirmation and not confirmed and is_explicit_location_change_request(text):
        question = "Which ward or area would you like to use instead?"
        return {
            "response_text": _localize(question, state, config),
            "follow_up_required": True,
            "follow_up_question": question,
            "follow_up_options": _LOCATION_CLARIFICATION_OPTIONS,
            "follow_up_options_labels": _localize_options(_LOCATION_CLARIFICATION_OPTIONS, state, config),
            "routed_to": "NONE_CLARIFICATION_NEEDED",
            "sources": [],
            "complaint_workflow_state": "AWAITING_LOCATION_CHANGE",
        }

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
    elif awaiting_location_change:
        # Same reasoning as the `awaiting_confirmation` branch just above: this turn's text is the
        # citizen's REPLACEMENT location (e.g. "Ward 5, Kothrud, Pune"), never the complaint
        # description -- recover the real one the same way. `_recover_complaint_draft_from_history`
        # already handles this correctly with no changes needed: it scans backward through USER
        # turns for one that `classify()`s as a real complaint, and a bare location string never
        # does, so it's silently skipped over on the way to the genuine original turn further back
        # -- the exact same behavior that already lets a plain "yes, submit it" reply skip past
        # itself to find the real description.
        category, complaint_text = _recover_complaint_draft_from_history(state)
    else:
        category_value = state.get("service_category")
        category = ServiceCategory(category_value) if category_value else _recover_category_from_history(state)
        complaint_text = text
        # LIVE-REPORTED REQUEST ("more ChatGPT/Claude-like reasoning") -- see
        # `_recover_photo_context_from_intent_ambiguous_turn`'s own docstring. A bare button-click
        # reply to the intent_ambiguous photo clarification ("Report a problem", or one of the
        # category options below it) carries no meaningful description of its own -- if the
        # immediately preceding turn was that exact photo clarification, recover the category (only
        # when this turn's own text didn't already supply one, e.g. "Report a problem" alone -- a
        # category button like "Road" already resolves its own category above and keeps it) and a
        # real, photo-derived description to use as the stored complaint text instead of the bare
        # button label.
        if text.strip() in (_INTENT_AMBIGUOUS_OPTIONS[0], *_CATEGORY_CLARIFICATION_OPTIONS):
            recovered_category, recovered_text = _recover_photo_context_from_intent_ambiguous_turn(state)
            # LIVE-REPORTED BUG (Hindi): the photo-specific recovery above only ever fires for a
            # PHOTO-driven intent_ambiguous turn -- the far more common text-only case (no photo at
            # all) fell through with `recovered_text` still None, leaving `complaint_text` as the
            # bare button label itself. See `_recover_text_before_intent_ambiguous_turn`'s own
            # docstring.
            if recovered_text is None:
                recovered_text = _recover_text_before_intent_ambiguous_turn(state)
            if category is None:
                category = recovered_category
            if recovered_text:
                complaint_text = recovered_text

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
    # LOCATION-CONFIRMATION FEATURE: a reply to the "which ward or area would you like to use
    # instead?" prompt is handled entirely separately from the normal resolution below -- `text`
    # here is the citizen's REPLACEMENT location, so it (or an explicit `ctx.location_text` from
    # the "Use current location"/"Select location" UI, checked first, same priority order as
    # everywhere else in this file) is what gets resolved, not `complaint_text` (the ORIGINAL
    # complaint description, recovered above). A citizen's own saved city -- `ctx.user.ward`, from
    # their AUTHENTICATED account row, never client-suppliable -- is what a genuine city SWITCH
    # gets checked against here. See this module's docstring section on the "your saved city"
    # feature this closes: filing for a DIFFERENT ward inside the SAME city is always allowed (no
    # check needed -- the citizen's own city is the default, not the only option, exactly like the
    # Report an Issue form's own ward dropdown scoping); a REAL, currently-staffed ward that
    # resolves to a genuinely DIFFERENT city is refused, with an honest, actionable message,
    # instead of silently filing under a city the citizen's own profile disagrees with.
    if awaiting_location_change:
        location_hint = ctx.location_text or text
        new_ward_text, new_resolved_ward, _ = _resolve_worker_ward_text(deps, ctx, state, location_hint)
        if new_ward_text is None:
            # Same honest "not currently served" wording the rest of this function already uses
            # for an unresolvable location -- re-asks rather than silently falling back to
            # anything, since a citizen actively trying to CHANGE their location must never have
            # that attempt silently ignored.
            honest_text = (
                "I'm sorry, Sarthi doesn't currently have workers set up to handle complaints in "
                f"\"{location_hint.strip()}\". Please try a different ward or area."
            )
            question = "Which ward or area would you like to use instead?"
            return {
                "response_text": _localize(honest_text + " " + question, state, config),
                "follow_up_required": True,
                "follow_up_question": question,
                "follow_up_options": _LOCATION_CLARIFICATION_OPTIONS,
                "follow_up_options_labels": _localize_options(_LOCATION_CLARIFICATION_OPTIONS, state, config),
                "routed_to": "NONE_CLARIFICATION_NEEDED",
                "sources": [],
                "complaint_workflow_state": "AWAITING_LOCATION_CHANGE",
            }
        if ctx.user.ward and not _wards_same_city(new_ward_text, ctx.user.ward):
            own_city = _ward_city(ctx.user.ward) or ctx.user.ward
            honest_text = (
                f"\"{new_ward_text}\" is not in your saved location ({own_city}). To report an "
                "issue in a different city, please update your location in Settings first."
            )
            return {
                "response_text": _localize(honest_text, state, config),
                "routed_to": "NONE_OUT_OF_SCOPE",
                "sources": [],
                "complaint_workflow_state": "CANCELLED",
            }
        worker_ward_text, resolved_ward, gps_resolved = new_ward_text, new_resolved_ward, None
        has_real_location = True
    else:
        # LOCATION-CONFIRMATION FEATURE: when the last turn WAS a confirmation prompt (about to be
        # replied to with "yes"/"no"/something else), the ward it already echoed is the single
        # most authoritative source -- see `_recover_ward_from_confirmation_prompt_text`'s own
        # docstring for the real bug this closes (re-deriving the ward from scratch via the scan
        # below can pick the WRONG same-city worker once a citizen has used "Change location").
        # Tried first; falls through to the normal resolution unchanged for every OTHER case
        # (no prior confirmation prompt at all, or its text didn't match for some reason).
        worker_ward_text = _recover_ward_from_confirmation_prompt_text(state) if awaiting_confirmation else None
        if worker_ward_text is not None:
            resolved_ward = deps.location_resolver.resolve_ward_by_text(ctx.db, worker_ward_text)
            gps_resolved = None
        else:
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
        #
        # LIVE-REPORTED BUG ("Pune fallback"), found here too: `known_place` above only catches a
        # place that resolved to something (an explicit `ctx.location_text`, or a real RAG
        # gazetteer city/state) -- it says nothing about a place NAMED in the text that resolved to
        # NOTHING at all, e.g. "Street light problem in Atlantis." from a citizen whose own home
        # ward happens to be a real, staffed city (Bengaluru). Live-reproduced: that citizen got a
        # confirmation prompt offering to file the complaint in Bengaluru -- their home city, which
        # they never mentioned -- instead of being asked to clarify the location, purely because
        # their OWN ward happened to resolve to a real worker where "Test Ward"-style placeholder
        # wards do not. Gated the same way as `_resolve_location`'s own citizen_home_ward tier (see
        # `_should_skip_home_ward_fallback`'s own docstring): `ctx.location_text` is always falsy by
        # the time this line runs (the `known_place` check just above already returns early
        # whenever it's set), so in practice this only ever gates on the message-text heuristic --
        # shared helper used anyway, for one obvious place to read the full reasoning.
        if not _should_skip_home_ward_fallback(ctx, complaint_text):
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
    # ctx.image_saved, set by ask_sarthi_service.ask_with_image()) is threaded through exactly
    # like the dedicated complaint form does: the legacy single-photo column for backward
    # compatibility, and a real ComplaintEvidence row via the EXISTING evidence_repository -- no
    # second image-storage system (see this module's docstring).
    #
    # LIVE-REPORTED REQUEST: a fresh photo attached THIS turn (`ctx.image_saved`) always wins when
    # present; otherwise, recover a photo attached on an EARLIER turn of this same exchange (see
    # `_recover_photo_evidence_from_history`'s own docstring) -- the file itself was already
    # validated and saved to disk back when it was first uploaded, so this only needs its
    # reference, never a re-upload.
    photo_ref = (
        {
            "filename": ctx.image_saved.filename,
            "original_name": ctx.image_saved.original_name,
            "content_type": ctx.image_saved.content_type,
            "size": ctx.image_saved.size,
        }
        if ctx.image_saved is not None
        else _recover_photo_evidence_from_history(state)
    )
    creation_span = tracing.start_child_run(
        root, "complaint_creation", "tool",
        inputs={"service_category": category.value, "language": state.get("response_language")},
    )
    try:
        # response_language (auto-detected, see language_node), not original_language -- this is
        # what `complaint_text` is ACTUALLY written in, which is what ComplaintAgent needs to
        # translate it correctly. Using the stale client-supplied language here previously risked
        # a real data-integrity bug: a citizen whose UI toggle disagreed with what they actually
        # typed would get their complaint's `translated_text` stored WRONG (treated as needing no
        # translation, or translated FROM the wrong source language) -- silently corrupting the
        # exact field workers rely on to read complaints in their own language.
        complaint = deps.complaint_agent.create_complaint(
            db=ctx.db,
            citizen_id=str(ctx.user.id),
            language_code=state.get("response_language") or "en",
            text=_humanize_complaint_text_for_storage(complaint_text),
            audio_chunks=None,
            photo_path=photo_ref["filename"] if photo_ref else None,
            category=category,
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

    if photo_ref is not None:
        # The file is already validated and written to disk (either just now, by ask_with_image(),
        # or on an earlier turn of this same exchange -- see `_recover_photo_evidence_from_history`)
        # -- this only records the ComplaintEvidence row, the exact same primitive
        # _save_evidence_files() itself calls, so this doesn't re-validate/re-write a file that's
        # already safely saved.
        evidence_repository.add_evidence(
            ctx.db,
            complaint_id=complaint.id,
            update_id=None,
            uploaded_by=ctx.user.id,
            uploader_role="citizen",
            file_name=photo_ref["original_name"],
            file_path=photo_ref["filename"],
            file_type=photo_ref["content_type"],
            file_size=photo_ref["size"],
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

    # `complaint_text` here can be a RECOVERED description that references a photo (via
    # `_recover_photo_context_from_intent_ambiguous_turn`/`_recover_from_confirmation_prompt_text`'s
    # own "[Attached photo shows: ...]" marker), but `photo_ref` above already recovers the actual
    # SAVED FILE from anywhere earlier in this same exchange too -- so this note now only fires in
    # the genuinely unrecoverable case: the conversation crossed a topic/complaint boundary between
    # the photo and this confirmation (see `_recover_photo_evidence_from_history`'s own stopping
    # points), so the photo's own file reference is truly gone, even though the recovered text
    # still describes it. Rare in practice, but still worth being honest about instead of silently
    # filing with no photo and no explanation.
    if photo_ref is None and "[attached photo shows:" in complaint_text.lower():
        response_text += " Note: since your photo wasn't included with this exact reply, it wasn't attached to the complaint."

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
# routed_to/etc.; this node fills in any field a flow node didn't set (so AskSarthiResponse
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
# ask_sarthi_service.py's `_run()`, which reads `complaint_id`/`routed_to` straight off this
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
