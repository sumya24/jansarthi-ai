"""LLM-as-judge diagnosis for Ask Sarthi requests flagged "needs review" (see
observability/tracing.py's enqueue_for_review() and models.AiRequestLog.needs_review's own
docstring) -- explains WHY the knowledge base couldn't answer one specific real question, and
what kind of content should be added to close that gap. Real reasoning text, attached to
Phoenix's own `explanation` field on the span annotation (confirmed a real field on Phoenix's
CreateSpanAnnotationInput, not invented) -- turns a bare NONE_OUT_OF_SCOPE/insufficient_knowledge
category label into something an admin can actually act on.

**Only ever called for requests ALREADY flagged needs_review** -- a small minority of real
traffic (most real questions ARE answered) -- never on every response. This is deliberate, not
an oversight: an extra Sarvam call on every single Ask Sarthi request would double this app's
most cost-sensitive path for no benefit on the (large) majority of requests that already succeed.
Cost is strictly proportional to genuine knowledge-base gaps, not to overall traffic.

**Runs in the background, adds zero citizen-facing latency.** Called from
observability/tracing.py's `_enqueue_for_review_sync()`, which already runs on a background
thread (see that function's own docstring) with nothing waiting on it -- this call's own latency
(a real LLM round trip) never delays the citizen's own already-completed response.

**Fail-open**, like every other optional AI/observability call in this pipeline: `diagnose()`
returns `None` -- never raises -- if Sarvam isn't configured or the call fails for any reason.
The caller already treats a missing explanation as "attach the annotation without one", not an
error worth surfacing anywhere citizen-facing.
"""

import logging

import httpx
from sarvamai import SarvamAI

from backend.config import get_prompt, settings
from backend.services.fake_sarvam_sdk import FakeSarvamAI
from backend.services.sarvam_key_pool import SarvamKeyRotationMixin

logger = logging.getLogger(__name__)

# Human-readable phrasing for the two real trigger reasons (see graph.py's own enqueue_for_review
# call site) -- the raw values ("NONE_OUT_OF_SCOPE"/"insufficient_knowledge") are internal routing
# codes, not something worth asking the judge LLM to interpret as English prose itself.
_REASON_PHRASES = {
    "NONE_OUT_OF_SCOPE": "the question was about a known civic service this app doesn't yet cover in its knowledge base",
    "insufficient_knowledge": "no knowledge base content matched closely enough to answer confidently",
}


class ReviewDiagnosisService(SarvamKeyRotationMixin):
    """Generates a short, real diagnostic explanation for one knowledge-base-gap request."""

    def __init__(self) -> None:
        # Same timeout shape as summary_service.py/answer_generation_service.py -- a reasoning-
        # model call (LLM_MODEL), generous read timeout, short connect timeout so a genuinely dead
        # connection fails fast rather than hanging the background thread indefinitely.
        timeout = httpx.Timeout(
            connect=settings.SARVAM_CONNECT_TIMEOUT_SECONDS,
            read=settings.SARVAM_REASONING_READ_TIMEOUT_SECONDS,
            write=settings.SARVAM_REASONING_READ_TIMEOUT_SECONDS,
            pool=settings.SARVAM_REASONING_READ_TIMEOUT_SECONDS,
        )
        self._init_sarvam_keys(
            timeout,
            settings.SARVAM_API_KEYS or settings.LLM_API_KEY,
            client_factory=FakeSarvamAI if settings.SARVAM_MOCK_MODE else SarvamAI,
        )

    def diagnose(
        self,
        *,
        question: str | None,
        reason: str,
        service_category: str | None,
        city: str | None,
        state: str | None,
    ) -> str | None:
        """Returns a short (2-3 sentence) real explanation of why the knowledge base couldn't
        answer this question and what should be added to fix it, or `None` if generation isn't
        possible (Sarvam unconfigured, no question text to diagnose) or fails for any reason --
        never raises."""
        if self._client is None or not question:
            return None
        try:
            prompt_template = get_prompt("review_diagnosis_prompt.txt")
            prompt = prompt_template.format(
                question=question,
                reason=_REASON_PHRASES.get(reason, reason),
                service_category=service_category or "unclear/unclassified",
                location=", ".join(filter(None, [city, state])) or "unknown",
            )
            response = self._call_sarvam(lambda client: client.chat.completions(
                model=settings.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                # LIVE-REPORTED (confirmed directly against production, not guessed): a hardcoded
                # 200 here reproduced sarvam-105b's own well-known reasoning-model failure mode
                # (see summary_service.py's own comment on this) EVEN with reasoning_effort="low"
                # -- finish_reason came back "length" with reasoning_content fully populated but
                # message.content empty, i.e. the entire budget was spent on internal reasoning
                # before any final answer token was emitted. settings.LLM_MAX_TOKENS (1024) is the
                # same budget every other reasoning-model caller in this app already uses for
                # exactly this reason -- this prompt gets no special exemption from that budget.
                max_tokens=settings.LLM_MAX_TOKENS,
                reasoning_effort="low",
            ))
            choice = response.choices[0]
            content = choice.message.content
            if not content or not content.strip():
                logger.warning(
                    "Review diagnosis returned no content (finish_reason=%s); annotation will carry no explanation.",
                    choice.finish_reason,
                )
                return None
            return content.strip()
        except Exception:
            logger.warning("Review diagnosis generation failed; annotation will carry no explanation.", exc_info=True)
            return None
