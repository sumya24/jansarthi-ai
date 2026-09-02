"""Unit tests for ReviewDiagnosisService -- the LLM-as-judge that explains WHY a knowledge-base-gap
request couldn't be answered (see that module's own docstring, and tracing.py's
_phoenix_enqueue_for_review() for how the result gets attached to a real Phoenix annotation).
"""

from unittest.mock import Mock

from backend.services.review_diagnosis_service import ReviewDiagnosisService


def _fake_chat_response(text: str | None, finish_reason: str = "stop") -> Mock:
    message = Mock(content=text)
    choice = Mock(message=message, finish_reason=finish_reason)
    return Mock(choices=[choice])


def test_diagnose_returns_stripped_model_output(monkeypatch):
    monkeypatch.setattr("backend.services.review_diagnosis_service.settings.LLM_API_KEY", "fake-key")
    fake_client = Mock()
    fake_client.chat.completions.return_value = _fake_chat_response(
        "  This question is about a service not yet covered for this city.  "
    )
    monkeypatch.setattr(
        "backend.services.review_diagnosis_service.SarvamAI", lambda api_subscription_key, timeout=None: fake_client
    )

    service = ReviewDiagnosisService()
    result = service.diagnose(
        question="Who do I contact for a new electricity connection?",
        reason="NONE_OUT_OF_SCOPE",
        service_category=None,
        city="Surat",
        state="Gujarat",
    )

    assert result == "This question is about a service not yet covered for this city."
    fake_client.chat.completions.assert_called_once()


def test_diagnose_without_api_key_returns_none(monkeypatch):
    """Fail-open: no Sarvam configured -> None, never raises (see the module's own docstring)."""
    monkeypatch.setattr("backend.services.review_diagnosis_service.settings.LLM_API_KEY", "")

    service = ReviewDiagnosisService()
    result = service.diagnose(question="Some question", reason="insufficient_knowledge", service_category=None, city=None, state=None)

    assert result is None


def test_diagnose_without_question_returns_none(monkeypatch):
    """Nothing to diagnose without the citizen's own question text -- must not call Sarvam at all."""
    monkeypatch.setattr("backend.services.review_diagnosis_service.settings.LLM_API_KEY", "fake-key")
    fake_client = Mock()
    monkeypatch.setattr(
        "backend.services.review_diagnosis_service.SarvamAI", lambda api_subscription_key, timeout=None: fake_client
    )

    service = ReviewDiagnosisService()
    result = service.diagnose(question=None, reason="insufficient_knowledge", service_category=None, city=None, state=None)

    assert result is None
    fake_client.chat.completions.assert_not_called()


def test_diagnose_swallows_call_failure_and_returns_none(monkeypatch):
    """Fail-open on a real Sarvam error -- never raises, matches every other optional AI call in
    this pipeline (see tracing.py's own module docstring)."""
    monkeypatch.setattr("backend.services.review_diagnosis_service.settings.LLM_API_KEY", "fake-key")
    fake_client = Mock()
    fake_client.chat.completions.side_effect = RuntimeError("network blip")
    monkeypatch.setattr(
        "backend.services.review_diagnosis_service.SarvamAI", lambda api_subscription_key, timeout=None: fake_client
    )

    service = ReviewDiagnosisService()
    result = service.diagnose(question="Some question", reason="NONE_OUT_OF_SCOPE", service_category="ELECTRICITY", city=None, state=None)

    assert result is None


def test_diagnose_returns_none_on_empty_model_output(monkeypatch):
    monkeypatch.setattr("backend.services.review_diagnosis_service.settings.LLM_API_KEY", "fake-key")
    fake_client = Mock()
    fake_client.chat.completions.return_value = _fake_chat_response("   ")
    monkeypatch.setattr(
        "backend.services.review_diagnosis_service.SarvamAI", lambda api_subscription_key, timeout=None: fake_client
    )

    service = ReviewDiagnosisService()
    result = service.diagnose(question="Some question", reason="insufficient_knowledge", service_category=None, city=None, state=None)

    assert result is None


def test_diagnose_returns_none_when_model_exhausts_token_budget_on_reasoning(monkeypatch, caplog):
    """LIVE-REPORTED, confirmed directly against production: sarvam-105b (a reasoning model) can
    spend its ENTIRE max_tokens budget on internal reasoning_content and emit no final answer at
    all -- message.content comes back None with finish_reason="length", even with
    reasoning_effort="low". Regression guard for the actual bug found (a too-small hardcoded
    max_tokens): must fail open to None (with a clear log line, not silently), never raise or
    return the reasoning text itself."""
    monkeypatch.setattr("backend.services.review_diagnosis_service.settings.LLM_API_KEY", "fake-key")
    fake_client = Mock()
    fake_client.chat.completions.return_value = _fake_chat_response(None, finish_reason="length")
    monkeypatch.setattr(
        "backend.services.review_diagnosis_service.SarvamAI", lambda api_subscription_key, timeout=None: fake_client
    )

    service = ReviewDiagnosisService()
    with caplog.at_level("WARNING"):
        result = service.diagnose(question="Some question", reason="insufficient_knowledge", service_category=None, city=None, state=None)

    assert result is None
    assert "finish_reason=length" in caplog.text


def test_diagnose_uses_the_same_token_budget_as_other_reasoning_model_callers(monkeypatch):
    """Regression guard for the exact real bug found in production: a hardcoded small max_tokens
    (200) reproduced sarvam-105b's own reasoning-budget-exhaustion failure mode even with
    reasoning_effort="low". Must use settings.LLM_MAX_TOKENS -- the same budget
    summary_service.py/answer_generation_service.py already rely on -- not a smaller, ad-hoc value
    specific to this one call site."""
    monkeypatch.setattr("backend.services.review_diagnosis_service.settings.LLM_API_KEY", "fake-key")
    monkeypatch.setattr("backend.services.review_diagnosis_service.settings.LLM_MAX_TOKENS", 4096)
    fake_client = Mock()
    fake_client.chat.completions.return_value = _fake_chat_response("A real explanation.")
    monkeypatch.setattr(
        "backend.services.review_diagnosis_service.SarvamAI", lambda api_subscription_key, timeout=None: fake_client
    )

    service = ReviewDiagnosisService()
    service.diagnose(question="Some question", reason="insufficient_knowledge", service_category=None, city=None, state=None)

    _, call_kwargs = fake_client.chat.completions.call_args
    assert call_kwargs["max_tokens"] == 4096
