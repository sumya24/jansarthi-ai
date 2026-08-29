"""Tests for backend/services/observability/tracing.py: LangSmith configuration, trace/span
initialization, PII redaction, and the non-blocking/fail-open guarantee (a LangSmith outage or
misconfiguration must never raise out of any function in this module).

See tests/test_ask_janmitra_tracing.py for tracing exercised through the real LangGraph/RAG
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
    assert tracing.start_root_run("ask_janmitra_graph", inputs={"question": "hi"}) is None


def test_start_root_run_returns_phoenix_only_run_when_langsmith_disabled_but_phoenix_enabled(monkeypatch):
    """LIVE-REPORTED gap this covers: local dev running Phoenix-only (LangSmith off) must still
    get a real, endable Phoenix span -- previously `start_root_run()` returned `None` here,
    silently losing every span this module hand-builds (see `_PhoenixOnlyRun`'s own docstring)."""
    monkeypatch.setattr(settings, "LANGSMITH_TRACING", False)
    monkeypatch.setattr(settings, "PHOENIX_TRACING", True)
    monkeypatch.setattr(tracing, "_phoenix_register", Mock())

    run = tracing.start_root_run("ask_janmitra_graph", inputs={"question": "hi"})

    assert isinstance(run, tracing._PhoenixOnlyRun)
    assert run.name == "ask_janmitra_graph"


def test_start_root_run_posts_and_returns_run_when_enabled(monkeypatch):
    fake_client = _enable(monkeypatch)
    run_id = uuid.uuid4()

    run = tracing.start_root_run(
        "ask_janmitra_graph", run_id=run_id, inputs={"question": "hi"}, tags=["ask_janmitra"], metadata={"request_id": "abc"}
    )

    assert run is not None
    assert run.id == run_id
    assert run.name == "ask_janmitra_graph"
    assert run.inputs == {"question": "hi"}
    fake_client.create_run.assert_called_once()  # the "run started" event was posted


def test_start_child_run_returns_none_when_parent_none():
    assert tracing.start_child_run(None, "rag_retrieval", "retriever", inputs={"query": "x"}) is None


def test_start_child_run_nests_under_parent(monkeypatch):
    _enable(monkeypatch)
    root = tracing.start_root_run("ask_janmitra_graph", inputs={})

    child = tracing.start_child_run(root, "rag_retrieval", "retriever", inputs={"query": "x"})

    assert child is not None
    assert child.parent_run_id == root.id
    assert child.trace_id == root.trace_id
    assert child.run_type == "retriever"


def test_end_run_is_noop_when_run_none():
    tracing.end_run(None, outputs={"a": 1})  # must not raise


def test_end_run_sets_outputs_and_sends_update(monkeypatch):
    fake_client = _enable(monkeypatch)
    run = tracing.start_root_run("ask_janmitra_graph", inputs={})

    tracing.end_run(run, outputs={"routed_to": "RAG"})

    assert run.outputs == {"routed_to": "RAG"}
    fake_client.update_run.assert_called_once()  # the "run finished" event was sent


def test_end_run_records_error(monkeypatch):
    _enable(monkeypatch)
    run = tracing.start_root_run("ask_janmitra_graph", inputs={})

    tracing.end_run(run, error="AIServiceError")

    assert run.error == "AIServiceError"


# --- 6/7: missing configuration / LangSmith unavailable must never break the caller ---


def test_client_init_failure_is_swallowed_not_raised(monkeypatch):
    monkeypatch.setattr(settings, "LANGSMITH_TRACING", True)
    monkeypatch.setattr(settings, "LANGSMITH_API_KEY", "ls-test-key")
    monkeypatch.setattr(settings, "PHOENIX_TRACING", False)
    monkeypatch.setattr(tracing, "_LangSmithClient", Mock(side_effect=RuntimeError("network unreachable")))

    assert tracing.start_root_run("ask_janmitra_graph", inputs={}) is None


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

    assert tracing.start_root_run("ask_janmitra_graph", inputs={}) is None


def test_start_root_run_returns_phoenix_only_run_when_langsmith_post_fails_but_phoenix_enabled(monkeypatch):
    """Same LangSmith failure as above, but Phoenix still gets its span -- one backend's outage
    must never take the other down with it."""
    fake_client = _enable(monkeypatch)
    monkeypatch.setattr(settings, "PHOENIX_TRACING", True)
    monkeypatch.setattr(tracing, "_phoenix_register", Mock())
    fake_client.create_run.side_effect = RuntimeError("LangSmith API unreachable")

    run = tracing.start_root_run("ask_janmitra_graph", inputs={})

    assert isinstance(run, tracing._PhoenixOnlyRun)


def test_start_child_run_swallows_post_failure(monkeypatch):
    fake_client = _enable(monkeypatch)
    monkeypatch.setattr(settings, "PHOENIX_TRACING", False)
    root = tracing.start_root_run("ask_janmitra_graph", inputs={})
    fake_client.create_run.side_effect = RuntimeError("LangSmith API unreachable")

    assert tracing.start_child_run(root, "rag_retrieval", "retriever") is None


def test_end_run_swallows_update_failure(monkeypatch):
    fake_client = _enable(monkeypatch)
    run = tracing.start_root_run("ask_janmitra_graph", inputs={})
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
