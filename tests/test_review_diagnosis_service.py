"""Unit tests for ReviewDiagnosisService -- the LLM-as-judge that explains WHY a knowledge-base-gap
request couldn't be answered (see that module's own docstring, and tracing.py's
_phoenix_enqueue_for_review() for how the result gets attached to a real Phoenix annotation).
"""

from unittest.mock import Mock

from backend.services.review_diagnosis_service import ReviewDiagnosisService


def _fake_chat_response(text: str | None) -> Mock:
    message = Mock(content=text)
    choice = Mock(message=message)
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
