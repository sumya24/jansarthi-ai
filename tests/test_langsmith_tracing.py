"""Tests for backend/services/observability/tracing.py: LangSmith configuration, trace/span
initialization, PII redaction, and the non-blocking/fail-open guarantee (a LangSmith outage or
misconfiguration must never raise out of any function in this module).

See tests/test_ask_sarthi_tracing.py for tracing exercised through the real LangGraph/RAG
pipeline, and tests/test_ai_monitoring.py for the Admin dashboard side (AiRequestLog).
"""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest

from backend.config import settings
from backend.services.observability import tracing


@pytest.fixture(autouse=True)
def _reset_tracing_state():
    """tracing.py caches its LangSmith Client and review-queue id at module scope (see
    _get_client()/_get_review_queue_id()) -- reset before and after every test so one test's
    monkeypatched settings/client never leaks into another."""
    tracing._client = None
    tracing._client_unavailable = False
    tracing._review_queue_id = None
    tracing._review_queue_unavailable = False
    yield
    tracing._client = None
    tracing._client_unavailable = False
    tracing._review_queue_id = None
    tracing._review_queue_unavailable = False


def _enable(monkeypatch, client=None):
    """Turns tracing on with a fake API key and (optionally) a fake LangSmith Client factory."""
    monkeypatch.setattr(settings, "LANGSMITH_TRACING", True)
    monkeypatch.setattr(settings, "LANGSMITH_API_KEY", "ls-test-key")
    fake_client = client if client is not None else Mock()
    monkeypatch.setattr(tracing, "_LangSmithClient", Mock(return_value=fake_client))
    # enqueue_for_review() now hands its real work off to a background thread (see that
    # function's own docstring on why) -- run it synchronously here instead, so this module's
    # tests can assert on its effects immediately after calling it, with no real race to win.
    monkeypatch.setattr(tracing, "_run_in_background", lambda target, args: target(*args))
    return fake_client


# --- 1: LangSmith configuration ---


def test_is_enabled_false_by_default(monkeypatch):
    monkeypatch.setattr(settings, "LANGSMITH_TRACING", False)
    monkeypatch.setattr(settings, "LANGSMITH_API_KEY", "")
    assert tracing.is_enabled() is False


def test_is_enabled_false_when_tracing_on_but_no_api_key(monkeypatch):
    monkeypatch.setattr(settings, "LANGSMITH_TRACING", True)
    monkeypatch.setattr(settings, "LANGSMITH_API_KEY", "")
    assert tracing.is_enabled() is False


def test_is_enabled_false_when_api_key_set_but_tracing_off(monkeypatch):
    monkeypatch.setattr(settings, "LANGSMITH_TRACING", False)
    monkeypatch.setattr(settings, "LANGSMITH_API_KEY", "ls-test-key")
    assert tracing.is_enabled() is False


def test_is_enabled_true_when_both_set(monkeypatch):
    monkeypatch.setattr(settings, "LANGSMITH_TRACING", True)
    monkeypatch.setattr(settings, "LANGSMITH_API_KEY", "ls-test-key")
    assert tracing.is_enabled() is True


def test_is_enabled_false_when_langsmith_package_unavailable(monkeypatch):
    """Simulates the `langsmith` package genuinely missing/broken at import time."""
    monkeypatch.setattr(settings, "LANGSMITH_TRACING", True)
    monkeypatch.setattr(settings, "LANGSMITH_API_KEY", "ls-test-key")
    monkeypatch.setattr(tracing, "_RunTree", None)
    assert tracing.is_enabled() is False


# --- 2/3: trace/span initialization ---


def test_start_root_run_returns_none_when_both_backends_disabled(monkeypatch):
    monkeypatch.setattr(settings, "LANGSMITH_TRACING", False)
    monkeypatch.setattr(settings, "PHOENIX_TRACING", False)
    assert tracing.start_root_run("ask_sarthi_graph", inputs={"question": "hi"}) is None


def test_start_root_run_returns_phoenix_only_run_when_langsmith_disabled_but_phoenix_enabled(monkeypatch):
    """LIVE-REPORTED gap this covers: local dev running Phoenix-only (LangSmith off) must still
    get a real, endable Phoenix span -- previously `start_root_run()` returned `None` here,
    silently losing every span this module hand-builds (see `_PhoenixOnlyRun`'s own docstring)."""
    monkeypatch.setattr(settings, "LANGSMITH_TRACING", False)
    monkeypatch.setattr(settings, "PHOENIX_TRACING", True)
    monkeypatch.setattr(tracing, "_phoenix_register", Mock())

    run = tracing.start_root_run("ask_sarthi_graph", inputs={"question": "hi"})

    assert isinstance(run, tracing._PhoenixOnlyRun)
    assert run.name == "ask_sarthi_graph"


def test_start_root_run_posts_and_returns_run_when_enabled(monkeypatch):
    fake_client = _enable(monkeypatch)
    run_id = uuid.uuid4()

    run = tracing.start_root_run(
        "ask_sarthi_graph", run_id=run_id, inputs={"question": "hi"}, tags=["ask_sarthi"], metadata={"request_id": "abc"}
    )

    assert run is not None
    assert run.id == run_id
    assert run.name == "ask_sarthi_graph"
    assert run.inputs == {"question": "hi"}
    fake_client.create_run.assert_called_once()  # the "run started" event was posted


def test_start_child_run_returns_none_when_parent_none():
    assert tracing.start_child_run(None, "rag_retrieval", "retriever", inputs={"query": "x"}) is None


def test_start_child_run_nests_under_parent(monkeypatch):
    _enable(monkeypatch)
    root = tracing.start_root_run("ask_sarthi_graph", inputs={})

    child = tracing.start_child_run(root, "rag_retrieval", "retriever", inputs={"query": "x"})

    assert child is not None
    assert child.parent_run_id == root.id
    assert child.trace_id == root.trace_id
    assert child.run_type == "retriever"


def test_end_run_is_noop_when_run_none():
    tracing.end_run(None, outputs={"a": 1})  # must not raise


def test_end_run_sets_outputs_and_sends_update(monkeypatch):
    fake_client = _enable(monkeypatch)
    run = tracing.start_root_run("ask_sarthi_graph", inputs={})

    tracing.end_run(run, outputs={"routed_to": "RAG"})

    assert run.outputs == {"routed_to": "RAG"}
    fake_client.update_run.assert_called_once()  # the "run finished" event was sent


def test_end_run_records_error(monkeypatch):
    _enable(monkeypatch)
    run = tracing.start_root_run("ask_sarthi_graph", inputs={})

    tracing.end_run(run, error="AIServiceError")

    assert run.error == "AIServiceError"


# --- 6/7: missing configuration / LangSmith unavailable must never break the caller ---


def test_client_init_failure_is_swallowed_not_raised(monkeypatch):
    monkeypatch.setattr(settings, "LANGSMITH_TRACING", True)
    monkeypatch.setattr(settings, "LANGSMITH_API_KEY", "ls-test-key")
    monkeypatch.setattr(settings, "PHOENIX_TRACING", False)
    monkeypatch.setattr(tracing, "_LangSmithClient", Mock(side_effect=RuntimeError("network unreachable")))

    assert tracing.start_root_run("ask_sarthi_graph", inputs={}) is None


def test_client_init_failure_is_cached_not_retried_every_call(monkeypatch):
    """Once the client fails to construct, subsequent calls in the same process short-circuit
    without hammering a broken endpoint again."""
    failing_factory = Mock(side_effect=RuntimeError("network unreachable"))
    monkeypatch.setattr(settings, "LANGSMITH_TRACING", True)
    monkeypatch.setattr(settings, "LANGSMITH_API_KEY", "ls-test-key")
    monkeypatch.setattr(settings, "PHOENIX_TRACING", False)
    monkeypatch.setattr(tracing, "_LangSmithClient", failing_factory)

    assert tracing.start_root_run("run_1", inputs={}) is None
    assert tracing.start_root_run("run_2", inputs={}) is None
    assert failing_factory.call_count == 1


def test_start_root_run_swallows_post_failure(monkeypatch):
    fake_client = _enable(monkeypatch)
    monkeypatch.setattr(settings, "PHOENIX_TRACING", False)
    fake_client.create_run.side_effect = RuntimeError("LangSmith API unreachable")

    assert tracing.start_root_run("ask_sarthi_graph", inputs={}) is None


def test_start_root_run_returns_phoenix_only_run_when_langsmith_post_fails_but_phoenix_enabled(monkeypatch):
    """Same LangSmith failure as above, but Phoenix still gets its span -- one backend's outage
    must never take the other down with it."""
    fake_client = _enable(monkeypatch)
    monkeypatch.setattr(settings, "PHOENIX_TRACING", True)
    monkeypatch.setattr(tracing, "_phoenix_register", Mock())
    fake_client.create_run.side_effect = RuntimeError("LangSmith API unreachable")

    run = tracing.start_root_run("ask_sarthi_graph", inputs={})

    assert isinstance(run, tracing._PhoenixOnlyRun)


def test_start_child_run_swallows_post_failure(monkeypatch):
    fake_client = _enable(monkeypatch)
    monkeypatch.setattr(settings, "PHOENIX_TRACING", False)
    root = tracing.start_root_run("ask_sarthi_graph", inputs={})
    fake_client.create_run.side_effect = RuntimeError("LangSmith API unreachable")

    assert tracing.start_child_run(root, "rag_retrieval", "retriever") is None


def test_end_run_swallows_update_failure(monkeypatch):
    fake_client = _enable(monkeypatch)
    run = tracing.start_root_run("ask_sarthi_graph", inputs={})
    fake_client.update_run.side_effect = RuntimeError("LangSmith API unreachable")

    tracing.end_run(run, outputs={"a": 1})  # must not raise despite update_run failing


# --- 8: redaction ---


def test_redact_text_masks_email():
    assert tracing.redact_text("contact me at citizen@example.com please") == "contact me at [REDACTED_EMAIL] please"


def test_redact_text_masks_long_digit_runs():
    redacted = tracing.redact_text("call me on 9876543210 about this")
    assert "9876543210" not in redacted
    assert "[REDACTED_NUMBER]" in redacted


def test_redact_text_keeps_short_numbers():
    # A 6-digit pincode/ward-number-shaped run is NOT redacted -- only 7+ digit runs are (see
    # this module's docstring on the redaction boundary chosen).
    assert tracing.redact_text("pincode 160055") == "pincode 160055"


def test_redact_text_truncates_long_text():
    redacted = tracing.redact_text("a" * 5000)
    assert len(redacted) < 5000
    assert redacted.endswith("[TRUNCATED]")


def test_redact_text_handles_none_and_empty():
    assert tracing.redact_text(None) is None
    assert tracing.redact_text("") == ""


def test_redact_text_leaves_ordinary_text_untouched():
    assert tracing.redact_text("Street light not working near the park") == "Street light not working near the park"


# --- get_trace_url() ---


def test_get_trace_url_none_when_template_unset(monkeypatch):
    monkeypatch.setattr(settings, "LANGSMITH_TRACE_URL_TEMPLATE", "")
    assert tracing.get_trace_url("abc-123") is None


def test_get_trace_url_none_when_trace_id_falsy(monkeypatch):
    monkeypatch.setattr(settings, "LANGSMITH_TRACE_URL_TEMPLATE", "https://smith.langchain.com/r/{trace_id}")
    assert tracing.get_trace_url(None) is None


def test_get_trace_url_builds_url_from_template(monkeypatch):
    monkeypatch.setattr(
        settings, "LANGSMITH_TRACE_URL_TEMPLATE", "https://smith.langchain.com/o/org/projects/p/proj/r/{trace_id}"
    )
    assert tracing.get_trace_url("abc-123") == "https://smith.langchain.com/o/org/projects/p/proj/r/abc-123"


def test_get_trace_url_returns_none_on_malformed_template(monkeypatch):
    monkeypatch.setattr(settings, "LANGSMITH_TRACE_URL_TEMPLATE", "https://example.com/{not_a_valid_placeholder}")
    assert tracing.get_trace_url("abc-123") is None


# --- Annotation Queue: enqueue_for_review() / _get_review_queue_id() ---


def test_enqueue_for_review_is_noop_when_run_none(monkeypatch):
    fake_client = _enable(monkeypatch)
    tracing.enqueue_for_review(None, reason="insufficient_knowledge")
    fake_client.add_runs_to_annotation_queue.assert_not_called()


def test_enqueue_for_review_is_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "LANGSMITH_TRACING", False)
    run = Mock(id="run-1")
    tracing.enqueue_for_review(run, reason="insufficient_knowledge")  # must not raise


def test_enqueue_for_review_creates_queue_if_missing_then_adds_run(monkeypatch):
    fake_client = _enable(monkeypatch)
    fake_client.list_annotation_queues.return_value = iter([])  # no existing queue
    fake_client.create_annotation_queue.return_value = Mock(id="queue-123")

    run = Mock(id="run-1")
    tracing.enqueue_for_review(run, reason="insufficient_knowledge")

    fake_client.create_annotation_queue.assert_called_once()
    fake_client.add_runs_to_annotation_queue.assert_called_once_with("queue-123", run_ids=["run-1"])


def test_enqueue_for_review_reuses_existing_queue(monkeypatch):
    fake_client = _enable(monkeypatch)
    fake_client.list_annotation_queues.return_value = iter([Mock(id="existing-queue")])

    run = Mock(id="run-1")
    tracing.enqueue_for_review(run, reason="NONE_OUT_OF_SCOPE")

    fake_client.create_annotation_queue.assert_not_called()
    fake_client.add_runs_to_annotation_queue.assert_called_once_with("existing-queue", run_ids=["run-1"])


def test_enqueue_for_review_caches_queue_id_across_calls(monkeypatch):
    fake_client = _enable(monkeypatch)
    fake_client.list_annotation_queues.return_value = iter([Mock(id="existing-queue")])

    tracing.enqueue_for_review(Mock(id="run-1"), reason="a")
    tracing.enqueue_for_review(Mock(id="run-2"), reason="b")

    fake_client.list_annotation_queues.assert_called_once()  # looked up once, cached after


def test_enqueue_for_review_swallows_lookup_failure(monkeypatch):
    fake_client = _enable(monkeypatch)
    fake_client.list_annotation_queues.side_effect = RuntimeError("LangSmith API down")

    tracing.enqueue_for_review(Mock(id="run-1"), reason="insufficient_knowledge")  # must not raise


def test_enqueue_for_review_swallows_add_failure(monkeypatch):
    fake_client = _enable(monkeypatch)
    fake_client.list_annotation_queues.return_value = iter([Mock(id="existing-queue")])
    fake_client.add_runs_to_annotation_queue.side_effect = RuntimeError("LangSmith API down")

    tracing.enqueue_for_review(Mock(id="run-1"), reason="insufficient_knowledge")  # must not raise


# --- Phoenix review-annotation: _phoenix_enqueue_for_review() ---
#
# LIVE-REPORTED bug this whole section covers: confirmed directly against production that this
# function was silently a no-op on every real call, ever -- see its own docstring's "round 2" note.
# A single immediate spans-query after force_flush() found nothing (Phoenix hadn't finished
# ingesting the span yet, even though the flush itself succeeded), so span_graphql_id came back
# None and the function returned before ever reaching the annotation mutation. These tests drive
# that exact code path with a fake httpx.Client double, since the real bug was entirely in the
# retry/timing behavior around the GraphQL calls, not in any of the LangSmith-side code above.


class _FakePhoenixResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakePhoenixClient:
    """Stands in for `httpx.Client(...)` -- `responses` is consumed one-per-`post()` call, in the
    exact order `_phoenix_enqueue_for_review()` makes them (project lookup, then one spans query
    per retry attempt, then the annotation mutation)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def post(self, url, json):
        self.calls.append(json)
        return _FakePhoenixResponse(self._responses.pop(0))


_PROJECT_RESP = {"data": {"getProjectByName": {"id": "project-1"}}}


def _spans_resp(edges):
    return {"data": {"node": {"spans": {"edges": edges}}}}


_MATCHING_EDGE = {"node": {"id": "span-gql-1", "context": {"traceId": "trace-1", "spanId": "span-1"}}}
_OTHER_EDGE = {"node": {"id": "span-gql-999", "context": {"traceId": "trace-999", "spanId": "span-999"}}}


def _enable_phoenix_review(monkeypatch, responses):
    """Turns Phoenix on, points a run's trace/span ids at the fixed ids the fake responses above
    use, no-ops force_flush and time.sleep (retry delays would otherwise slow every test down for
    no reason), and installs the fake httpx.Client returning `responses` in order."""
    monkeypatch.setattr(settings, "PHOENIX_TRACING", True)
    monkeypatch.setattr(tracing, "_phoenix_register", Mock())
    monkeypatch.setattr(settings, "PHOENIX_COLLECTOR_ENDPOINT", "http://phoenix:6006/v1/traces")
    monkeypatch.setattr(settings, "PHOENIX_PROJECT_NAME", "jansarthi-ai")
    monkeypatch.setattr(tracing, "_phoenix_provider", None)  # skip force_flush entirely
    monkeypatch.setattr(tracing.time, "sleep", lambda seconds: None)
    fake_client = _FakePhoenixClient(responses)
    monkeypatch.setattr(tracing.httpx, "Client", Mock(return_value=fake_client))

    run_id = uuid.uuid4()
    tracing._phoenix_trace_ids[run_id] = "trace-1"
    tracing._phoenix_span_ids[run_id] = "span-1"
    return run_id, fake_client


def test_phoenix_enqueue_for_review_finds_span_immediately(monkeypatch):
    run_id, fake_client = _enable_phoenix_review(
        monkeypatch,
        responses=[
            _PROJECT_RESP,
            _spans_resp([_MATCHING_EDGE]),
            {"data": {"createSpanAnnotations": {"spanAnnotations": [{"id": "ann-1"}]}}},
        ],
    )

    tracing._phoenix_enqueue_for_review(run_id, reason="NONE_OUT_OF_SCOPE")

    assert len(fake_client.calls) == 3  # project lookup, one spans query, the mutation
    mutation_call = fake_client.calls[-1]
    assert mutation_call["variables"]["input"][0]["spanId"] == "span-gql-1"
    assert mutation_call["variables"]["input"][0]["label"] == "NONE_OUT_OF_SCOPE"


def test_phoenix_enqueue_for_review_retries_until_span_is_queryable(monkeypatch, caplog):
    """The exact production gap: the span isn't in Phoenix's result set on the first (or second)
    query, only the third -- must retry and still succeed, not give up after one miss."""
    run_id, fake_client = _enable_phoenix_review(
        monkeypatch,
        responses=[
            _PROJECT_RESP,
            _spans_resp([_OTHER_EDGE]),  # attempt 1: not there yet
            _spans_resp([_OTHER_EDGE]),  # attempt 2: still not there
            _spans_resp([_OTHER_EDGE, _MATCHING_EDGE]),  # attempt 3: now it is
            {"data": {"createSpanAnnotations": {"spanAnnotations": [{"id": "ann-1"}]}}},
        ],
    )

    tracing._phoenix_enqueue_for_review(run_id, reason="insufficient_knowledge")

    assert len(fake_client.calls) == 5  # project lookup + 3 spans queries + the mutation
    assert fake_client.calls[-1]["variables"]["input"][0]["spanId"] == "span-gql-1"


def test_phoenix_enqueue_for_review_gives_up_after_max_attempts_without_raising(monkeypatch, caplog):
    """The span is NEVER found (e.g. Phoenix genuinely down, or this trace was somehow pruned) --
    must stop after `_REVIEW_SPAN_LOOKUP_ATTEMPTS`, never hang or raise, and never call the
    mutation with a bogus id."""
    responses = [_PROJECT_RESP] + [_spans_resp([_OTHER_EDGE])] * tracing._REVIEW_SPAN_LOOKUP_ATTEMPTS
    run_id, fake_client = _enable_phoenix_review(monkeypatch, responses=responses)

    with caplog.at_level("INFO"):
        tracing._phoenix_enqueue_for_review(run_id, reason="insufficient_knowledge")  # must not raise

    # project lookup + exactly _REVIEW_SPAN_LOOKUP_ATTEMPTS spans queries, no mutation call
    assert len(fake_client.calls) == 1 + tracing._REVIEW_SPAN_LOOKUP_ATTEMPTS
    assert "not queryable after" in caplog.text


def test_phoenix_enqueue_for_review_logs_graphql_errors_in_mutation_response(monkeypatch, caplog):
    """GraphQL reports a failed mutation as HTTP 200 with an "errors" array, not an HTTP error --
    this must be surfaced in the logs (not silently swallowed as success) without raising."""
    run_id, fake_client = _enable_phoenix_review(
        monkeypatch,
        responses=[
            _PROJECT_RESP,
            _spans_resp([_MATCHING_EDGE]),
            {"data": None, "errors": [{"message": "spanId not found"}]},
        ],
    )

    with caplog.at_level("WARNING"):
        tracing._phoenix_enqueue_for_review(run_id, reason="insufficient_knowledge")  # must not raise

    assert "GraphQL errors" in caplog.text


def test_phoenix_enqueue_for_review_is_noop_when_run_id_none(monkeypatch):
    monkeypatch.setattr(settings, "PHOENIX_TRACING", True)
    monkeypatch.setattr(tracing, "_phoenix_register", Mock())
    tracing._phoenix_enqueue_for_review(None, reason="insufficient_knowledge")  # must not raise


def test_phoenix_enqueue_for_review_is_noop_when_no_span_ids_recorded(monkeypatch):
    """A run with no Phoenix span at all (e.g. Phoenix was disabled when the request started) --
    must no-op instead of making any GraphQL calls."""
    monkeypatch.setattr(settings, "PHOENIX_TRACING", True)
    monkeypatch.setattr(tracing, "_phoenix_register", Mock())
    fake_client = _FakePhoenixClient(responses=[])
    monkeypatch.setattr(tracing.httpx, "Client", Mock(return_value=fake_client))

    tracing._phoenix_enqueue_for_review(uuid.uuid4(), reason="insufficient_knowledge")

    assert fake_client.calls == []


# --- Phoenix review-annotation: real LLM-generated explanation (review_diagnosis_service.py) ---


def _stub_review_diagnosis_service(monkeypatch, explanation):
    """Replaces the lazily-constructed ReviewDiagnosisService singleton with a fake whose
    diagnose() returns a fixed value -- keeps these tests isolated from Sarvam entirely, same
    "fake the seam, not the network" approach the rest of this file already uses for Phoenix/
    LangSmith. Also resets tracing.py's own cached singleton so an earlier test's stub can never
    leak into a later one."""
    monkeypatch.setattr(tracing, "_review_diagnosis_service", None)
    fake_service = Mock()
    fake_service.diagnose.return_value = explanation
    monkeypatch.setattr(tracing, "_get_review_diagnosis_service", lambda: fake_service)
    return fake_service


def test_phoenix_enqueue_for_review_attaches_real_explanation_when_question_given(monkeypatch):
    run_id, fake_client = _enable_phoenix_review(
        monkeypatch,
        responses=[
            _PROJECT_RESP,
            _spans_resp([_MATCHING_EDGE]),
            {"data": {"createSpanAnnotations": {"spanAnnotations": [{"id": "ann-1"}]}}},
        ],
    )
    fake_service = _stub_review_diagnosis_service(
        monkeypatch, "This question is about a service not yet covered for this city's knowledge base."
    )

    tracing._phoenix_enqueue_for_review(
        run_id, "NONE_OUT_OF_SCOPE", question="Who do I contact for a new electricity connection?",
        service_category=None, city="Surat", state="Gujarat",
    )

    fake_service.diagnose.assert_called_once_with(
        question="Who do I contact for a new electricity connection?",
        reason="NONE_OUT_OF_SCOPE", service_category=None, city="Surat", state="Gujarat",
    )
    mutation_input = fake_client.calls[-1]["variables"]["input"][0]
    assert mutation_input["explanation"] == "This question is about a service not yet covered for this city's knowledge base."
    assert mutation_input["annotatorKind"] == "LLM"  # a real explanation was attached -> LLM, not CODE


def test_phoenix_enqueue_for_review_omits_explanation_when_diagnosis_returns_none(monkeypatch):
    """The diagnosis call itself is fail-open (see review_diagnosis_service.py) -- a None result
    (Sarvam unconfigured, the call failed) must still let the annotation itself go through, just
    without an explanation field, same annotatorKind as before this feature existed."""
    run_id, fake_client = _enable_phoenix_review(
        monkeypatch,
        responses=[
            _PROJECT_RESP,
            _spans_resp([_MATCHING_EDGE]),
            {"data": {"createSpanAnnotations": {"spanAnnotations": [{"id": "ann-1"}]}}},
        ],
    )
    _stub_review_diagnosis_service(monkeypatch, None)

    tracing._phoenix_enqueue_for_review(
        run_id, "insufficient_knowledge", question="Some question", service_category=None, city=None, state=None,
    )

    mutation_input = fake_client.calls[-1]["variables"]["input"][0]
    assert "explanation" not in mutation_input
    assert mutation_input["annotatorKind"] == "CODE"


def test_phoenix_enqueue_for_review_skips_diagnosis_when_no_question_given(monkeypatch):
    """No `question` passed at all (e.g. an older caller, or enqueue_for_review()'s own default)
    -- must not even construct the diagnosis service, matching every existing test in this file
    that calls _phoenix_enqueue_for_review() with just (run_id, reason)."""
    run_id, fake_client = _enable_phoenix_review(
        monkeypatch,
        responses=[
            _PROJECT_RESP,
            _spans_resp([_MATCHING_EDGE]),
            {"data": {"createSpanAnnotations": {"spanAnnotations": [{"id": "ann-1"}]}}},
        ],
    )
    fake_service = _stub_review_diagnosis_service(monkeypatch, "should never be used")

    tracing._phoenix_enqueue_for_review(run_id, "NONE_OUT_OF_SCOPE")

    fake_service.diagnose.assert_not_called()
    mutation_input = fake_client.calls[-1]["variables"]["input"][0]
    assert "explanation" not in mutation_input
    assert mutation_input["annotatorKind"] == "CODE"


def test_enqueue_for_review_threads_diagnosis_context_through_to_phoenix(monkeypatch):
    """The public enqueue_for_review() -> _enqueue_for_review_sync() -> _phoenix_enqueue_for_review()
    chain must actually carry question/service_category/city/state all the way through, not just
    the two originally-supported positional args."""
    _enable(monkeypatch)  # LangSmith side -- irrelevant here beyond not erroring
    monkeypatch.setattr(settings, "PHOENIX_TRACING", True)
    monkeypatch.setattr(tracing, "_phoenix_register", Mock())
    monkeypatch.setattr(tracing, "_phoenix_provider", None)

    captured = {}

    def fake_phoenix_enqueue(run_id, reason, question=None, service_category=None, city=None, state=None):
        captured.update(reason=reason, question=question, service_category=service_category, city=city, state=state)

    monkeypatch.setattr(tracing, "_phoenix_enqueue_for_review", fake_phoenix_enqueue)

    tracing.enqueue_for_review(
        Mock(id="run-1"), "NONE_OUT_OF_SCOPE",
        question="Who do I contact for a water leak?", service_category="WATER_DRAINAGE", city="Pune", state="Maharashtra",
    )

    assert captured == {
        "reason": "NONE_OUT_OF_SCOPE",
        "question": "Who do I contact for a water leak?",
        "service_category": "WATER_DRAINAGE",
        "city": "Pune",
        "state": "Maharashtra",
    }
