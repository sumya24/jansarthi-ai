"""LangSmith observability for the Ask Sarthi LangGraph pipeline.

**Manual, curated spans -- not automatic autotracing.** LangGraph's compiled graph is a
LangChain `Runnable`, so simply setting `LANGSMITH_TRACING=true`/`LANGCHAIN_TRACING_V2=true` as
environment variables would make LangChain's own callback-based tracer capture and upload the
*entire* `GraphState` (see `orchestration/state.py`) at every node transition -- including
`user_message`/`normalized_message`/`conversation_history`, i.e. the citizen's raw complaint
text, which can contain names, phone numbers, or addresses. That's exactly the "unnecessary
citizen PII" this integration was asked not to send. So this module never enables that global
callback tracer; instead, `orchestration/graph.py` and `orchestration/nodes.py` call the small
API below to create explicit spans around specific points (the whole graph run, RAG retrieval,
LLM answer generation, complaint creation) with a hand-picked, redacted payload -- the same
"log field NAMES/categorical values, not raw content" philosophy `run_graph()`'s own pre-existing
per-node log line already used (see that function's docstring), just extended to LangSmith.

**Non-blocking, fail-open.** Every public function here returns `None`/no-ops instead of raising
if: the `langsmith` package is missing, tracing is disabled or unconfigured (no API key), the
LangSmith client can't be constructed, or any individual call to it fails (auth error, network
error, timeout). Callers (graph.py, nodes.py) never need their own try/except around these calls
-- a LangSmith outage or misconfiguration must never break RAG, complaint creation, complaint
status, or the LangGraph pipeline itself (see docs/ask_sarthi_langsmith_observability.md's
"failure behavior" section). The `langsmith` SDK itself also batches/uploads runs on a background
thread rather than blocking the caller on network I/O -- this module's own try/except is
defense-in-depth on top of that, not a replacement for it.

**Redaction.** `redact_text()` masks email addresses and long digit runs (phone numbers, most
ID-like numbers) and caps length before any free text is attached to a span. This is a
best-effort regex filter, not a guarantee -- a citizen's message that includes a name or street
address in prose won't be caught by it. Documented as a known limitation, not hidden (see the
docs file's privacy section). The stronger control is architectural: callers of this module
choose exactly which fields to pass in the first place (see graph.py/nodes.py), so most
inherently-safe fields (intent, routed_to, service_category, location city/state, verification
status, latency) are sent as-is with no redaction needed, and only the handful of genuinely
free-text fields (the citizen's question, the generated answer) are routed through
`redact_text()` at all.

**Arize Phoenix (second, self-hosted backend).** Purely additive: Phoenix spans are keyed off the
SAME `RunTree.id` LangSmith already assigns, so nodes.py/graph.py/ask_sarthi_service.py keep
passing that one `RunTree` around unchanged -- this module alone tracks which Phoenix span belongs
to which run, in a private `_phoenix_spans` dict. Known scope limit: because Phoenix piggybacks on
the RunTree as its id-carrier, a Phoenix span is only produced when `start_root_run()` actually
returns a RunTree -- i.e. when LangSmith itself is enabled (even if its own network call later
fails). A "Phoenix only, LangSmith fully unconfigured" setup isn't supported by this minimal
design; solving that would need a separate lightweight id-carrier independent of LangSmith, out of
scope for this pass since both are meant to run together.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

try:
    from langsmith import Client as _LangSmithClient
    from langsmith.run_trees import RunTree as _RunTree
except Exception:  # pragma: no cover -- defensive: package genuinely missing/broken
    _LangSmithClient = None  # type: ignore[assignment,misc]
    _RunTree = None  # type: ignore[assignment,misc]

try:
    from opentelemetry import trace as _otel_trace
    from opentelemetry.trace import Span as _OTelSpan, Status as _OTelStatus, StatusCode as _OTelStatusCode
    from openinference.semconv.trace import SpanAttributes as _OISpanAttributes, OpenInferenceSpanKindValues as _OISpanKind
    from phoenix.otel import register as _phoenix_register
except Exception:  # pragma: no cover -- defensive: package genuinely missing/broken
    _otel_trace = None  # type: ignore[assignment]
    _OTelSpan = None  # type: ignore[assignment,misc]
    _OTelStatus = None  # type: ignore[assignment,misc]
    _OTelStatusCode = None  # type: ignore[assignment,misc]
    _OISpanAttributes = None  # type: ignore[assignment,misc]
    _OISpanKind = None  # type: ignore[assignment,misc]
    _phoenix_register = None  # type: ignore[assignment]

try:
    # Auto-instruments LangGraph/LangChain itself -- every internal graph node this app's
    # StateGraph passes through (input_processing, language_detection, intent_classification,
    # location_resolution, clarification_flow, response_generation, the _route_after_* conditional
    # edges) becomes its own Phoenix span automatically, the same fine-grained trace LangSmith
    # already shows via its own separate, built-in LangChain integration -- LIVE-REPORTED gap:
    # Phoenix previously only ever received this module's own hand-built spans below
    # (ask_sarthi_graph/rag_retrieval/answer_generation/...), never this framework-level detail.
    from openinference.instrumentation.langchain import LangChainInstrumentor as _LangChainInstrumentor
except Exception:  # pragma: no cover -- defensive: package genuinely missing/broken
    _LangChainInstrumentor = None  # type: ignore[assignment,misc]

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_LONG_DIGIT_RUN_RE = re.compile(r"\d{7,}")  # phone numbers, most long ID-like numbers
_MAX_TEXT_LEN = 2000

_client: "_LangSmithClient | None" = None
_client_unavailable = False
_review_queue_id: "uuid.UUID | None" = None
_review_queue_unavailable = False

# Arize Phoenix -- a second, self-hosted tracing backend, purely additive alongside everything
# above (see this module's docstring for why). Keyed by the SAME id LangSmith's RunTree already
# uses, so nodes.py/graph.py/ask_sarthi_service.py keep passing that one RunTree around exactly
# as before -- this module alone knows a Phoenix span exists for a given run id.
_phoenix_tracer: Any = None
_phoenix_tracer_unavailable = False
# The TracerProvider itself (not just the tracer `_phoenix_tracer` above gets from it) -- kept so
# `_phoenix_enqueue_for_review()` can call `force_flush()` on it (see that function's own docstring
# for why this is needed: spans are batched, not exported the instant `span.end()` is called).
_phoenix_provider: Any = None
_phoenix_spans: dict[uuid.UUID, Any] = {}
# Phoenix's own (OTel) trace id is a different id space from the RunTree uuid used as the dict
# key above -- OTel assigns it internally, it can't be forced to match. Kept in its own dict,
# NOT cleared when a span ends (unlike `_phoenix_spans`), since callers (ask_sarthi_service.py)
# need to read it for AiRequestLog.phoenix_trace_id after the request has already finished.
# Negligible memory footprint at this app's real scale (~50 bytes/request, matches the same
# scale reasoning ai_request_log_repository.py already uses for its own in-memory aggregation).
_phoenix_trace_ids: dict[uuid.UUID, str] = {}
# The root span's own raw OTel span id (hex), same lifetime/memory-footprint reasoning as
# `_phoenix_trace_ids` above -- kept so `enqueue_for_review()` can find and annotate the exact
# right span in Phoenix (via context.traceId/context.spanId) after the request has already
# finished, without having to guess which of a project's many spans is the one that just completed.
_phoenix_span_ids: dict[uuid.UUID, str] = {}
_RUN_TYPE_TO_OI_KIND = {
    "chain": "CHAIN",
    "retriever": "RETRIEVER",
    "llm": "LLM",
    "tool": "TOOL",
}


def redact_text(text: str | None) -> str | None:
    """Best-effort PII scrubbing for free text before it's attached to a trace: masks email
    addresses and 7+ digit runs, and caps length. See this module's docstring for what this
    does and does not catch."""
    if not text:
        return text
    redacted = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    redacted = _LONG_DIGIT_RUN_RE.sub("[REDACTED_NUMBER]", redacted)
    if len(redacted) > _MAX_TEXT_LEN:
        redacted = redacted[:_MAX_TEXT_LEN] + "...[TRUNCATED]"
    return redacted


def is_enabled() -> bool:
    """True only when the `langsmith` package imported successfully AND the operator has both
    turned tracing on and provided an API key -- matches SarvamClient's own "warn, don't crash"
    treatment of optional external services (see that module)."""
    return bool(_RunTree is not None and settings.LANGSMITH_TRACING and settings.LANGSMITH_API_KEY)


def _phoenix_enabled() -> bool:
    """True only when the Phoenix/OpenTelemetry packages imported successfully AND the operator
    has turned it on -- same shape as `is_enabled()` above, kept separate since Phoenix and
    LangSmith are independent, either can be on/off/broken without affecting the other."""
    return bool(_phoenix_register is not None and settings.PHOENIX_TRACING)


def _get_phoenix_tracer() -> Any:
    """Lazily builds (and caches) the Phoenix OTel tracer for this process -- same
    cache-the-failure shape as `_get_client()`."""
    global _phoenix_tracer, _phoenix_tracer_unavailable, _phoenix_provider
    if not _phoenix_enabled() or _phoenix_tracer_unavailable:
        return None
    if _phoenix_tracer is not None:
        return _phoenix_tracer
    try:
        provider = _phoenix_register(
            endpoint=settings.PHOENIX_COLLECTOR_ENDPOINT,
            project_name=settings.PHOENIX_PROJECT_NAME,
            auto_instrument=False,
            batch=True,
            verbose=False,
            set_global_tracer_provider=False,
        )
        _phoenix_provider = provider
        _phoenix_tracer = provider.get_tracer(__name__)
    except Exception:
        logger.warning("Phoenix tracer could not be initialized; Phoenix tracing disabled for this process.", exc_info=True)
        _phoenix_tracer_unavailable = True
        return None
    # Separate try/except, deliberately non-fatal to the tracer itself: this only ADDS the
    # framework-level LangGraph node spans (see the import above) on top of the hand-built spans
    # this module already sends -- a failure here should never take down Phoenix tracing entirely,
    # same "one broken piece costs only itself" shape as everywhere else in this module.
    if _LangChainInstrumentor is not None:
        try:
            _LangChainInstrumentor().instrument(tracer_provider=provider)
        except Exception:
            logger.warning("Phoenix LangChain auto-instrumentation could not be enabled; Phoenix still gets this module's own hand-built spans.", exc_info=True)
    return _phoenix_tracer


def _phoenix_json_attrs(payload: dict[str, Any] | None, *, is_output: bool) -> dict[str, Any]:
    """Flattens an `inputs`/`outputs` dict into the OpenInference attribute shape Phoenix's UI
    renders as a proper "Input"/"Output" panel -- OTel span attributes only accept flat
    scalars/sequences, unlike LangSmith's `RunTree` which takes arbitrary nested JSON directly."""
    if not payload:
        return {}
    value_key = _OISpanAttributes.OUTPUT_VALUE if is_output else _OISpanAttributes.INPUT_VALUE
    mime_key = _OISpanAttributes.OUTPUT_MIME_TYPE if is_output else _OISpanAttributes.INPUT_MIME_TYPE
    try:
        return {value_key: json.dumps(payload, default=str), mime_key: "application/json"}
    except Exception:
        return {}


def _phoenix_start_span(
    run_id: uuid.UUID | None,
    name: str,
    run_type: str,
    parent_id: uuid.UUID | None,
    inputs: dict[str, Any] | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Starts a Phoenix span mirroring the LangSmith run identified by `run_id`, nested under
    `parent_id`'s Phoenix span if one exists. Wrapped in its own try/except -- a Phoenix outage or
    misconfiguration must never affect the LangSmith path or the request itself.

    `metadata`'s `conversation_id`, when present, is set as the OpenInference session id -- only
    ever passed by `start_root_run()` (see its own call site), never by `start_child_run()`: a
    child span doesn't need it set again, Phoenix groups the whole trace under whatever session id
    the ROOT span carries."""
    if not _phoenix_enabled() or run_id is None:
        return
    tracer = _get_phoenix_tracer()
    if tracer is None:
        return
    try:
        parent_span = _phoenix_spans.get(parent_id) if parent_id is not None else None
        context = _otel_trace.set_span_in_context(parent_span) if parent_span is not None else None
        attributes: dict[str, Any] = {
            _OISpanAttributes.OPENINFERENCE_SPAN_KIND: _RUN_TYPE_TO_OI_KIND.get(run_type, "CHAIN"),
            **_phoenix_json_attrs(inputs, is_output=False),
        }
        conversation_id = (metadata or {}).get("conversation_id")
        if conversation_id:
            attributes[_OISpanAttributes.SESSION_ID] = str(conversation_id)
        span = tracer.start_span(name, context=context, attributes=attributes)
        _phoenix_spans[run_id] = span
        span_context = span.get_span_context()
        _phoenix_trace_ids[run_id] = format(span_context.trace_id, "032x")
        _phoenix_span_ids[run_id] = format(span_context.span_id, "016x")
    except Exception:
        logger.warning("Phoenix: failed to start span %r", name, exc_info=True)


def get_phoenix_trace_id(run_id: uuid.UUID | None) -> str | None:
    """Returns the real Phoenix (OTel) trace id for a run started via `start_root_run()`/
    `start_child_run()`, or `None` if Phoenix tracing wasn't active for it. Callers pass this to
    `record_ai_request()` as `phoenix_trace_id` -- deliberately NOT the same uuid used as
    LangSmith's run id (see this module's docstring for why the two id spaces differ)."""
    if run_id is None:
        return None
    return _phoenix_trace_ids.get(run_id)


def _phoenix_end_span(run_id: uuid.UUID | None, *, outputs: dict[str, Any] | None = None, error: str | None = None) -> None:
    """Ends the Phoenix span for `run_id`, if one was started. Same fail-open shape as everything
    else in this module."""
    if not _phoenix_enabled():
        return
    span = _phoenix_spans.pop(run_id, None)
    if span is None:
        return
    try:
        for key, value in _phoenix_json_attrs(outputs, is_output=True).items():
            span.set_attribute(key, value)
        # Real Sarvam token counts (see answer_generation_service.generate()'s token_usage return
        # value), when present, ALSO get promoted to the dedicated OpenInference token-count
        # attributes -- that's the specific shape Phoenix's Metrics view actually reads; they stay
        # in the generic JSON blob above too, which is harmless duplication, not a conflict.
        if outputs:
            if "prompt_tokens" in outputs:
                span.set_attribute(_OISpanAttributes.LLM_TOKEN_COUNT_PROMPT, outputs["prompt_tokens"])
            if "completion_tokens" in outputs:
                span.set_attribute(_OISpanAttributes.LLM_TOKEN_COUNT_COMPLETION, outputs["completion_tokens"])
            if "total_tokens" in outputs:
                span.set_attribute(_OISpanAttributes.LLM_TOKEN_COUNT_TOTAL, outputs["total_tokens"])
            # Real cost, computed from Sarvam's own published per-token/per-character price (see
            # answer_generation_service.py's _SARVAM_INPUT_COST_PER_TOKEN_INR/
            # _SARVAM_OUTPUT_COST_PER_TOKEN_INR and nodes.py's
            # _SARVAM_TRANSLATE_COST_PER_CHAR_INR) -- this is what actually fills in Phoenix's
            # "Total Cost" / "Top model by cost" views, previously always $0 since nothing set it.
            # Reported in real Indian Rupees, NOT USD -- Phoenix's dashboard hardcodes a literal
            # "$" prefix on these attributes regardless of what currency the number represents (it
            # has no INR/₹ display mode); that mislabeled "$" is a known, accepted cosmetic quirk
            # of Phoenix's own UI, not something this app can fix -- the number itself is the real,
            # correct Rupee amount, which is what actually matters here.
            if "prompt_cost_inr" in outputs:
                span.set_attribute(_OISpanAttributes.LLM_COST_PROMPT, outputs["prompt_cost_inr"])
            if "completion_cost_inr" in outputs:
                span.set_attribute(_OISpanAttributes.LLM_COST_COMPLETION, outputs["completion_cost_inr"])
            if "total_cost_inr" in outputs:
                span.set_attribute(_OISpanAttributes.LLM_COST_TOTAL, outputs["total_cost_inr"])
            # Which model actually answered -- lets Phoenix's Metrics view group "top model by
            # cost/tokens" (see nodes.py's answer_generation span, the only caller that sets this).
            # Without it those two specific charts have nothing to group by, even though the raw
            # token counts above are already present and correct on their own.
            if "model_name" in outputs:
                span.set_attribute(_OISpanAttributes.LLM_MODEL_NAME, outputs["model_name"])
        if error:
            span.record_exception(Exception(error))
            span.set_status(_OTelStatus(_OTelStatusCode.ERROR, error))
        else:
            # Explicit OK, not just "no error" -- an unset status is otherwise indistinguishable
            # in Phoenix's UI from a span nobody ever finished ending properly.
            span.set_status(_OTelStatus(_OTelStatusCode.OK))
        span.end()
    except Exception:
        logger.warning("Phoenix: failed to end span for run %s", run_id, exc_info=True)


def _get_client() -> "_LangSmithClient | None":
    global _client, _client_unavailable
    if not is_enabled() or _client_unavailable:
        return None
    if _client is not None:
        return _client
    try:
        _client = _LangSmithClient(api_key=settings.LANGSMITH_API_KEY, api_url=settings.LANGSMITH_ENDPOINT)
    except Exception:
        logger.warning("LangSmith client could not be initialized; tracing disabled for this process.", exc_info=True)
        _client_unavailable = True
        return None
    return _client


class _PhoenixOnlyRun:
    """Stand-in returned by `start_root_run()`/`start_child_run()` when Phoenix tracing is active
    but there's no real LangSmith run backing it (LangSmith disabled/unconfigured, or its own call
    failed) -- carries just the `id`/`name` every caller in this module and `graph.py`/`nodes.py`
    actually touches on a run object, so Phoenix's own span lifecycle is never silently skipped
    just because LangSmith happens to be off.

    LIVE-REPORTED gap this fixes (2026-08-28): with local dev intentionally running Phoenix-only
    (LangSmith off by default, see `.env`), `start_root_run()` returned `None` whenever
    `_get_client()` was `None` -- and every downstream call (`end_run()`/`start_child_run()`/
    `enqueue_for_review()`) treated a `None` run as "tracing is off entirely" and no-opped. The
    Phoenix span this module had ALREADY started (`_phoenix_start_span()` runs unconditionally,
    before the LangSmith branch) was consequently never ended/exported -- confirmed directly: a
    real RAG request sent with `LANGSMITH_TRACING=false` produced zero new `answer_generation`
    span in Phoenix, even though `PHOENIX_TRACING=true` the whole time. Every span this module
    hand-builds (`answer_generation`/`rag_retrieval`/`response_translation`/`text_to_speech`/
    `speech_to_text`/`vision_processing`/etc, with all their cost/token/model attributes -- see
    `_phoenix_end_span()`) was silently missing from Phoenix on this exact machine since LangSmith
    was switched off, independent of the separate LangGraph auto-instrumentation added earlier."""

    __slots__ = ("id", "name")

    def __init__(self, id: uuid.UUID, name: str) -> None:
        self.id = id
        self.name = name


def _is_real_langsmith_run(run: Any) -> bool:
    """True for anything that ISN'T our own `_PhoenixOnlyRun` sentinel -- deliberately the
    negative check (not `isinstance(run, _RunTree)`) so this keeps working for a test double
    standing in for a real LangSmith run (a plain `Mock(id=...)`, which duck-types fine but isn't
    literally a `_RunTree` instance), not just the real SDK class."""
    return not isinstance(run, _PhoenixOnlyRun)


def start_root_run(
    name: str,
    *,
    run_id: uuid.UUID | None = None,
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> "_RunTree | _PhoenixOnlyRun | None":
    """Starts (and immediately posts the "run started" event for) a new root trace.

    Returns `None` -- never raises -- only when NEITHER backend is doing anything for this run.
    When Phoenix is active but LangSmith isn't (or LangSmith's own call fails), returns a
    `_PhoenixOnlyRun` stand-in instead of `None` -- see that class's docstring for why this
    matters. Callers pass the returned value straight into `start_child_run()`/`end_run()`/
    `enqueue_for_review()`, all of which handle every case (`_RunTree`, `_PhoenixOnlyRun`, `None`)
    without needing to branch on which backend is actually active.
    """
    resolved_run_id = run_id or uuid.uuid4()
    _phoenix_start_span(resolved_run_id, name, "chain", None, inputs, metadata)

    client = _get_client()
    if client is None:
        return _PhoenixOnlyRun(resolved_run_id, name) if _phoenix_enabled() else None
    try:
        run = _RunTree(
            name=name,
            run_type="chain",
            inputs=inputs or {},
            id=resolved_run_id,
            ls_client=client,
            session_name=settings.LANGSMITH_PROJECT,
            tags=tags or [],
            extra={"metadata": metadata or {}},
        )
        run.post()
        return run
    except Exception:
        logger.warning("LangSmith: failed to start trace %r", name, exc_info=True)
        return _PhoenixOnlyRun(resolved_run_id, name) if _phoenix_enabled() else None


def start_child_run(
    parent: "_RunTree | _PhoenixOnlyRun | None",
    name: str,
    run_type: str = "chain",
    *,
    inputs: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> "_RunTree | _PhoenixOnlyRun | None":
    """Starts a child span under `parent` (e.g. RAG retrieval, LLM answer generation, complaint
    creation). A no-op returning `None` if `parent` is `None` (tracing was disabled/unavailable
    when the parent trace would have started). When `parent` is a real LangSmith run, mirrors it
    there too; when `parent` is a `_PhoenixOnlyRun` (LangSmith wasn't available for the ROOT run
    either), the Phoenix child span below still gets created and returned as its own
    `_PhoenixOnlyRun` -- see that class's docstring."""
    if parent is None:
        return None

    child_id = uuid.uuid4()
    _phoenix_start_span(child_id, name, run_type, getattr(parent, "id", None), inputs)

    if not _is_real_langsmith_run(parent):
        # No real LangSmith parent to attach a child run to -- the Phoenix child span above was
        # already created regardless, so hand back its id the same way start_root_run() does.
        return _PhoenixOnlyRun(child_id, name) if _phoenix_enabled() else None
    try:
        child = parent.create_child(
            name=name,
            run_type=run_type,  # type: ignore[arg-type]
            inputs=inputs or {},
            tags=tags,
            extra={"metadata": metadata} if metadata else None,
            run_id=child_id,
        )
        child.post()
        return child
    except Exception:
        logger.warning("LangSmith: failed to start span %r", name, exc_info=True)
        return _PhoenixOnlyRun(child_id, name) if _phoenix_enabled() else None


def end_run(run: "_RunTree | _PhoenixOnlyRun | None", *, outputs: dict[str, Any] | None = None, error: str | None = None) -> None:
    """Finishes a run started by `start_root_run()`/`start_child_run()`. A no-op if `run` is
    `None`. For a `_PhoenixOnlyRun`, only the Phoenix span is ended (there's no real LangSmith run
    to finish); for a real LangSmith run, both happen, same as before -- never raises either way."""
    if run is None:
        return
    _phoenix_end_span(getattr(run, "id", None), outputs=outputs, error=error)
    if not _is_real_langsmith_run(run):
        return
    try:
        run.end(outputs=outputs, error=error)
        run.patch()
    except Exception:
        logger.warning("LangSmith: failed to finish run %r", getattr(run, "name", "?"), exc_info=True)


def _get_review_queue_id() -> "uuid.UUID | None":
    """Looks up (and caches for the life of this process) the id of the Annotation Queue named
    `settings.LANGSMITH_REVIEW_QUEUE_NAME`, creating it if it doesn't exist yet. `None` -- never
    raises -- if tracing is disabled or the lookup/creation fails."""
    global _review_queue_id, _review_queue_unavailable
    if _review_queue_id is not None:
        return _review_queue_id
    if _review_queue_unavailable:
        return None
    client = _get_client()
    if client is None:
        return None
    try:
        existing = next(iter(client.list_annotation_queues(name=settings.LANGSMITH_REVIEW_QUEUE_NAME)), None)
        if existing is not None:
            _review_queue_id = existing.id
            return _review_queue_id
        created = client.create_annotation_queue(
            name=settings.LANGSMITH_REVIEW_QUEUE_NAME,
            description=(
                "Ask Sarthi requests where the knowledge base couldn't answer -- either "
                "insufficient_knowledge or an out-of-scope service. Each one is a real citizen "
                "question the KB should potentially cover. See docs/"
                "ask_sarthi_langsmith_observability.md's Annotation Queue section."
            ),
        )
        _review_queue_id = created.id
        return _review_queue_id
    except Exception:
        logger.warning("LangSmith: could not look up/create the review annotation queue.", exc_info=True)
        _review_queue_unavailable = True
        return None


def enqueue_for_review(run: "_RunTree | _PhoenixOnlyRun | None", reason: str) -> None:
    """Flags `run` (the Ask Sarthi request's root span) for review -- LangSmith's Annotation Queue
    when a real LangSmith run exists (see `_get_review_queue_id()`), and/or a Phoenix span
    annotation (see `_phoenix_enqueue_for_review()`) whenever Phoenix has a span for this run,
    independent of whether LangSmith does. A no-op if `run` is `None`; never raises. `reason` is
    logged locally only (not sent to LangSmith -- the run itself already carries its own
    `routed_to`/`insufficient_knowledge` outputs, see graph.py's run_graph())."""
    if run is None:
        return
    _phoenix_enqueue_for_review(getattr(run, "id", None), reason)
    if not _is_real_langsmith_run(run):
        return
    queue_id = _get_review_queue_id()
    if queue_id is None:
        return
    try:
        client = _get_client()
        if client is None:
            return
        client.add_runs_to_annotation_queue(queue_id, run_ids=[run.id])
    except Exception:
        logger.warning("LangSmith: failed to enqueue run %s for review (%s)", getattr(run, "id", "?"), reason, exc_info=True)


def get_trace_url(trace_id: str | None) -> str | None:
    """Builds an Admin-dashboard deep link to a trace from `LANGSMITH_TRACE_URL_TEMPLATE` (see
    config.py), or `None` if that's not configured or `trace_id` is falsy. Deliberately does not
    call the LangSmith API to resolve a link (that would put a network call on the admin
    dashboard's read path) -- the template is a one-time manual copy from the LangSmith UI (see
    docs/ask_sarthi_langsmith_observability.md's setup section)."""
    if not trace_id or not settings.LANGSMITH_TRACE_URL_TEMPLATE:
        return None
    try:
        return settings.LANGSMITH_TRACE_URL_TEMPLATE.format(trace_id=trace_id)
    except Exception:
        logger.warning("LANGSMITH_TRACE_URL_TEMPLATE is misconfigured (expected a {trace_id} placeholder).")
        return None


def get_phoenix_trace_url(trace_id: str | None) -> str | None:
    """Same pure-string-templating shape as `get_trace_url()` above, for Phoenix's own dashboard
    link (`PHOENIX_TRACE_URL_TEMPLATE`, see config.py)."""
    if not trace_id or not settings.PHOENIX_TRACE_URL_TEMPLATE:
        return None
    try:
        return settings.PHOENIX_TRACE_URL_TEMPLATE.format(trace_id=trace_id)
    except Exception:
        logger.warning("PHOENIX_TRACE_URL_TEMPLATE is misconfigured (expected a {trace_id} placeholder).")
        return None


# --- Per-model cost summary for the Admin AI Monitoring page -- see get_model_cost_summary()'s
# own docstring for why this reads spans directly instead of Phoenix's own "Top models" widgets. ---

# One entry per real span this app's own tracing creates that can carry a real `llm.model_name`
# (see nodes.py/ask_sarthi_service.py's own tracing call sites) -- a fixed, known list, not
# discovered dynamically, since discovering it would need the same "top N" queries this function
# exists to route around.
_MODEL_COST_SPAN_NAMES = ["answer_generation", "response_translation", "text_to_speech", "speech_to_text", "vision_processing"]

# Static display info for each real model this app currently calls -- kept here (not duplicated in
# the frontend) so there is exactly one place that knows what each model is used for and whether
# its cost is genuinely billed or free. `label`/`vendor` are plain, citizen-free-language strings
# (an admin reading this page, not Phoenix's own audience) -- see PHOENIX_TRACING_PLAN.md for the
# real pricing research behind each one.
_MODEL_DISPLAY_INFO: dict[str, dict[str, Any]] = {
    "sarvam-105b": {"label": "Answer Generation", "vendor": "Sarvam AI", "is_free": False},
    "sarvam-translate:v1": {"label": "Reply Translation", "vendor": "Sarvam AI", "is_free": False},
    "bulbul:v3": {"label": "Text-to-Speech", "vendor": "Sarvam AI", "is_free": False},
    "saaras:v3": {"label": "Speech-to-Text", "vendor": "Sarvam AI", "is_free": False},
    "gemini-3.5-flash-lite": {"label": "Photo Captioning", "vendor": "Google Gemini (free tier)", "is_free": True},
    "vikhyatk/moondream2": {"label": "Photo Captioning (fallback)", "vendor": "Local model", "is_free": True},
}


def _phoenix_graphql_base_url() -> str | None:
    """Derives Phoenix's GraphQL endpoint from `PHOENIX_COLLECTOR_ENDPOINT` (the OTLP traces
    ingestion URL, e.g. "http://localhost:6006/v1/traces") -- both live on the same host/port,
    just different paths, so no separate setting is needed for this. `None` if the configured
    endpoint doesn't have the expected "/v1/traces" suffix to strip."""
    endpoint = settings.PHOENIX_COLLECTOR_ENDPOINT
    suffix = "/v1/traces"
    if not endpoint.endswith(suffix):
        return None
    return endpoint[: -len(suffix)] + "/graphql"


def _phoenix_enqueue_for_review(run_id: uuid.UUID | None, reason: str) -> None:
    """Phoenix's own counterpart to `enqueue_for_review()`'s LangSmith Annotation Queue --
    Phoenix has no equivalent "queue" concept, so this tags the matching span directly with a
    `needs_review` span annotation instead (Phoenix's own closest capability, confirmed via its
    GraphQL schema's `createSpanAnnotations` mutation). Fail-open like everything else in this
    module: a no-op if Phoenix is disabled, this run never got a Phoenix span (see
    `_phoenix_trace_ids`/`_phoenix_span_ids`), or any part of the lookup/mutation fails.

    Root spans are always named "ask_sarthi_graph" (see graph.py's `start_root_run()` call
    site) -- filtering Phoenix's spans by that name, then matching the exact trace/span id
    client-side from the (small) result set, avoids needing to guess Phoenix's filter-expression
    syntax for raw OTel ids directly.

    LIVE-REPORTED gap this fixes: called right after `end_run()` in the SAME request, so the
    root span's own `.end()` call happened only moments earlier -- Phoenix's exporter batches
    spans rather than sending them the instant `.end()` is called (`_get_phoenix_tracer()` uses
    `batch=True`), so querying immediately found nothing and this silently no-opped every time
    (confirmed directly: 2 of the 3 expected GraphQL calls fired, the 3rd -- the actual
    annotation mutation -- never did, because the span lookup came back empty). Forcing a flush
    of the just-ended span before querying closes that gap; capped at 2s so a slow/unresponsive
    Phoenix can't add real latency to a citizen's request."""
    if run_id is None or not _phoenix_enabled():
        return
    trace_id = _phoenix_trace_ids.get(run_id)
    span_id = _phoenix_span_ids.get(run_id)
    if not trace_id or not span_id:
        return
    base_url = _phoenix_graphql_base_url()
    if base_url is None:
        return
    if _phoenix_provider is not None:
        try:
            _phoenix_provider.force_flush(timeout_millis=2000)
        except Exception:
            logger.warning("Phoenix: force_flush before review-annotation lookup failed; continuing anyway.", exc_info=True)
    try:
        with httpx.Client(timeout=10.0) as client:
            project_resp = client.post(
                base_url,
                json={
                    "query": "query($name: String!) { getProjectByName(name: $name) { id } }",
                    "variables": {"name": settings.PHOENIX_PROJECT_NAME},
                },
            )
            project_resp.raise_for_status()
            project = (project_resp.json().get("data") or {}).get("getProjectByName")
            if not project:
                return

            spans_query = """
                query($id: ID!, $filter: String!) {
                  node(id: $id) {
                    ... on Project {
                      spans(first: 20, sort: {col: startTime, dir: desc}, filterCondition: $filter) {
                        edges { node { id context { traceId spanId } } }
                      }
                    }
                  }
                }
            """
            spans_resp = client.post(
                base_url,
                json={"query": spans_query, "variables": {"id": project["id"], "filter": 'name == "ask_sarthi_graph"'}},
            )
            spans_resp.raise_for_status()
            edges = (((spans_resp.json().get("data") or {}).get("node") or {}).get("spans") or {}).get("edges", [])
            span_graphql_id = next(
                (
                    e["node"]["id"]
                    for e in edges
                    if e["node"]["context"]["traceId"] == trace_id and e["node"]["context"]["spanId"] == span_id
                ),
                None,
            )
            if span_graphql_id is None:
                return

            annotate_mutation = """
                mutation($input: [CreateSpanAnnotationInput!]!) {
                  createSpanAnnotations(input: $input) { spanAnnotations { id } }
                }
            """
            client.post(
                base_url,
                json={
                    "query": annotate_mutation,
                    "variables": {
                        "input": [
                            {
                                "spanId": span_graphql_id,
                                "name": "needs_review",
                                "annotatorKind": "CODE",
                                "label": reason,
                                "metadata": {},
                                "source": "API",
                            }
                        ]
                    },
                },
            )
    except Exception:
        logger.warning("Phoenix: failed to annotate span for review (run_id=%s, reason=%s)", run_id, reason, exc_info=True)


def get_model_cost_summary(days: int = 30) -> list[dict[str, Any]]:
    """Real per-model cost/token totals over the last `days` days, aggregated directly from
    Phoenix's own spans -- deliberately NOT from Phoenix's own "Top models by cost/tokens"
    dashboard widgets, which are hard-capped at showing only 4 models at a time (confirmed
    directly against Phoenix's own GraphQL schema: `topModelsByCost`/`topModelsByTokenCount` take
    no limit/count argument at all -- there is no way to raise that cap) and would silently drop
    whichever models currently have the smallest volume -- today, that's exactly the Gemini/local
    vision models this function exists to make visible again.

    Powers the Admin AI Monitoring page's "Cost by model" panel: always shows every real model
    this app calls, regardless of Phoenix's own ranking, by querying each model's own known span
    NAME directly (see `_MODEL_COST_SPAN_NAMES`) and summing the real `llm.cost.total`/
    `llm.token_count.total` attributes already on every such span (see nodes.py's/
    ask_sarthi_service.py's own tracing call sites -- this reads the exact same numbers, not a
    separate computation).

    Fail-open, like everything else in this module: never raises. But "fail-open" used to mean
    "one slow/failed GraphQL call blanks the ENTIRE panel" -- LIVE-REPORTED on the shared,
    CPU-constrained production VM: this function used to make up to 6 sequential HTTP calls to
    Phoenix (1 project lookup + 1 per `_MODEL_COST_SPAN_NAMES` entry), all inside one try/except.
    Confirmed directly in production logs: when the backend's own CPU is briefly pinned by
    something unrelated (e.g. loading the vision-captioning model), Phoenix -- a single process
    sharing the same constrained CPU -- occasionally can't answer within the timeout
    (`httpcore.ReadTimeout`), and the old code threw away every model's numbers because of that one
    slow call, even models whose query had already succeeded moments earlier. That's the exact
    "shows a model, refresh, shows nothing" flapping reported live. Now each piece degrades
    independently: the project lookup gets one quick retry (cheap, and the one call every other
    query depends on), and each span-name query is caught on its own -- a single timed-out model
    just doesn't have a row this refresh, instead of taking every other model down with it.

    LIVE-REPORTED, round 2 -- found via /code-review after the panel was STILL reported stuck on
    its loading skeleton even after the fix above and a VM upsize: the per-model resilience fix
    didn't bound how long a SLOW (not failed) Phoenix could make the whole batch take. The 5
    span-name queries still ran strictly sequentially, and httpx's own `timeout=` only bounds the
    gap BETWEEN chunks, not a call's total duration -- a Phoenix that's merely slow (scanning up to
    500 spans per model, 5 models, one connection) could legitimately take far longer than any
    admin would wait, with nothing in the chain (Caddy's reverse_proxy, this function, or the old
    frontend fetch with no timeout of its own -- see api.ts's aiMonitoringModelCosts) ever cutting
    it short. Now the 5 queries run concurrently (ThreadPoolExecutor) with a real wall-clock cap
    per query (`future.result(timeout=...)`, which the underlying httpx `timeout=` alone can't
    provide) -- the whole batch takes roughly as long as the single slowest query, not the sum of
    all 5, and nothing waits past that cap regardless of how the network behaves.
    """
    if not _phoenix_enabled():
        return []
    base_url = _phoenix_graphql_base_url()
    if base_url is None:
        return []

    try:
        return _fetch_model_cost_summary(base_url, days)
    except Exception:
        # Ultimate safety net: the project-lookup and per-model loops below already handle their
        # own known failure modes granularly (see docstring); this only catches something
        # genuinely unexpected, so this admin-only observability panel degrades to empty instead
        # of ever bubbling into a 500.
        logger.warning("Could not fetch Phoenix model cost summary.", exc_info=True)
        return []


def _fetch_model_cost_summary(base_url: str, days: int) -> list[dict[str, Any]]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    totals: dict[str, dict[str, float]] = {}

    with httpx.Client(timeout=20.0) as client:
        project_id = None
        # One retry for the project lookup specifically -- every span query below depends on it,
        # so it's worth a second, cheap attempt before giving up on the whole panel (unlike the
        # per-model queries below, there's no "partial" result possible if this one never succeeds).
        for attempt in range(2):
            try:
                project_resp = client.post(
                    base_url,
                    json={
                        "query": "query($name: String!) { getProjectByName(name: $name) { id } }",
                        "variables": {"name": settings.PHOENIX_PROJECT_NAME},
                    },
                )
                project_resp.raise_for_status()
                project = (project_resp.json().get("data") or {}).get("getProjectByName")
                if not project:
                    logger.warning("Phoenix project %r not found for model cost summary.", settings.PHOENIX_PROJECT_NAME)
                    return []
                project_id = project["id"]
                break
            except Exception:
                if attempt == 0:
                    logger.info("Phoenix project lookup failed once for model cost summary; retrying.", exc_info=True)
                    continue
                logger.warning("Could not fetch Phoenix model cost summary (project lookup failed twice).", exc_info=True)
                return []

        spans_query = """
            query($id: ID!, $start: DateTime!, $end: DateTime!, $filter: String!) {
              node(id: $id) {
                ... on Project {
                  spans(first: 500, timeRange: {start: $start, end: $end}, filterCondition: $filter) {
                    edges { node { attributes } }
                  }
                }
              }
            }
        """

        def _fetch_span_edges(span_name: str) -> list[dict[str, Any]]:
            resp = client.post(
                base_url,
                json={
                    "query": spans_query,
                    "variables": {
                        "id": project_id,
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "filter": f'name == "{span_name}"',
                    },
                },
            )
            resp.raise_for_status()
            return (((resp.json().get("data") or {}).get("node") or {}).get("spans") or {}).get("edges") or []

        # LIVE-REPORTED, found via /code-review: these 5 queries used to run strictly sequentially
        # on one connection -- worst case, up to 5x this single query's own latency stacked on top
        # of the project lookup above, easily exceeding what an admin (or the frontend's own 15s
        # timeout, see api.ts's aiMonitoringModelCosts) will wait, even with Phoenix merely slow
        # rather than down. Running them concurrently means the whole batch takes roughly as long
        # as the SLOWEST single query, not the sum of all 5. `future.result(timeout=...)` also
        # enforces a real wall-clock cap per query -- unlike httpx's own `timeout=`, which only
        # bounds the gap BETWEEN chunks, not the call's total duration (a response trickling in
        # just under that gap could otherwise run far longer than the configured timeout).
        with ThreadPoolExecutor(max_workers=len(_MODEL_COST_SPAN_NAMES)) as pool:
            future_to_span = {pool.submit(_fetch_span_edges, name): name for name in _MODEL_COST_SPAN_NAMES}
            for future in future_to_span:
                span_name = future_to_span[future]
                try:
                    edges = future.result(timeout=10.0)
                except Exception:
                    # Fail-open PER MODEL: this one span name's numbers are missing this refresh,
                    # but whatever the loop already accumulated for other models stays intact below.
                    logger.info("Phoenix span query for %r failed; skipping just that model this refresh.", span_name, exc_info=True)
                    continue
                for edge in edges:
                    try:
                        attrs = json.loads(edge["node"]["attributes"])
                    except Exception:
                        continue
                    llm = attrs.get("llm") or {}
                    model_name = llm.get("model_name")
                    if not model_name:
                        continue
                    cost = (llm.get("cost") or {}).get("total") or 0.0
                    tokens = (llm.get("token_count") or {}).get("total") or 0
                    bucket = totals.setdefault(model_name, {"cost": 0.0, "tokens": 0.0, "count": 0.0})
                    bucket["cost"] += cost
                    bucket["tokens"] += tokens
                    bucket["count"] += 1

    results = []
    for model_name, bucket in totals.items():
        info = _MODEL_DISPLAY_INFO.get(model_name, {"label": model_name, "vendor": "Unknown", "is_free": False})
        results.append({
            "model_name": model_name,
            "label": info["label"],
            "vendor": info["vendor"],
            "is_free": info["is_free"],
            "total_cost_inr": bucket["cost"],
            "total_tokens": int(bucket["tokens"]),
            "request_count": int(bucket["count"]),
        })
    # Fixed pipeline order (not sorted by cost/volume -- that's exactly the ranking Phoenix's
    # own widget already does, and hides smaller models under) -- an admin scans the same 5
    # rows in the same place every time, regardless of which model was busiest recently.
    order = {name: i for i, name in enumerate(_MODEL_DISPLAY_INFO)}
    results.sort(key=lambda r: order.get(r["model_name"], len(order)))
    return results
