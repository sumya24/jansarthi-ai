"""Prompt-injection guardrail checks for Ask Sarthi -- a real, always-on defense layer distinct
from `observability/tracing.py`'s `redact_text()`, which is explicitly PII redaction for TRACE
LOGS ONLY (see that function's own docstring) and was never built as a defense against a citizen
trying to manipulate the model's own instructions.

Hand-rolled, not a new dependency (e.g. Guardrails AI): consistent with this codebase's existing
preference for a small, auditable, zero-network-cost check over a new library for something this
project can implement directly (see auth_service.py's hand-rolled JWT, rate_limiter.py's
hand-rolled sliding window) -- and a direct, concrete lesson from earlier this session: installing
`ragas` silently shifted several already-pinned, load-bearing dependency versions (see
scripts/ragas_rag_evaluation.py's own docstring) for a job this codebase could do without it.
Deliberately a fast, zero-latency, zero-added-Sarvam-call pattern match, not a per-request
LLM-judge call -- adding a network round-trip to Sarvam before EVERY Ask Sarthi request would
double this app's most latency- and cost-sensitive path (see config.py's AI_RATE_LIMIT comment on
how tightly this app already tracks Sarvam call volume) for a check a well-curated pattern list
already catches for the overwhelming majority of real-world attempts.

Two checks, both structural, applied at the two chokepoints every Ask Sarthi entry point
(`ask()`/`ask_with_image()`/`ask_voice()`) already funnels through in `AskJanMitraService._run()`:

`check_input()`: scans a citizen's raw message for known prompt-injection/jailbreak phrasing
BEFORE it ever reaches the intent classifier, RAG answer generation, or complaint agent -- every
one of which builds a prompt that includes the citizen's own text verbatim. A match short-circuits
`_run()` before the graph (and therefore any LLM call) ever runs at all -- the cheapest possible
place to stop it, and the only one that guarantees the attempted override never reaches a model.

`check_output()`: scans the LLM's OWN generated answer for the same injection markers (a
citizen's attempt only actually succeeded if the model's reply visibly complied with or echoed it
back -- e.g. "Sure, ignoring my previous instructions...") AND for verbatim leakage of this app's
own answer-generation system prompt text -- a distinct failure mode where the model discloses its
internal instructions without necessarily having been "hijacked" into anything else. Applied to
the final answer text right before it's returned to the citizen.

Honest limitation, stated directly rather than overstated: both checks are pattern-based, not a
semantic guarantee -- a sufficiently creative paraphrase can still get through, exactly as any
keyword-based filter can. This is a real, meaningful floor against the common, actually-documented
attack shapes (instruction override, role-play jailbreaks, system-prompt extraction), not a claim
of complete prompt-injection immunity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Case-insensitive; each pattern targets one well-documented injection/jailbreak shape rather than
# one exact phrase, so common rewordings ("ignore all prior instructions", "please disregard the
# above prompt") still match without needing an entry per variant.
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignor(e|ing)\s+(all|any|the|my)?\s*(previous|prior|above|preceding)\s*(instructions|prompts|rules|context)",
        # BUG FIX (code review): the trailing (prompt|instructions|rules) group used to be
        # optional, so "disregard my previous complaint" or "disregard the previous ticket
        # number" -- completely ordinary things a citizen amending their own complaint would
        # say -- matched on "disregard...previous" alone and got wrongly blocked. Required now:
        # only a genuine instruction-override phrasing ("disregard the previous instructions")
        # matches, not any "disregard...previous/system/above X" sentence regardless of what X is.
        r"disregard\s+(the|your|my|any)?\s*(system|above|previous|prior)\s+(prompt|instructions|rules)",
        r"forget\s+(all|everything|your)\s*(previous|prior|above)?\s*(instructions|rules|training)",
        # BUG FIX (code review): missing a word-boundary after the alternation meant "a"/"an"
        # matched as a bare substring, not a whole word -- "you are now able to track your
        # complaint" or "you are now assigned a worker" (ordinary, correct civic answers) both
        # contain "you are now a" as a literal substring of "able"/"assigned" and were wrongly
        # flagged as jailbreak-compliance signals. \b forces "a"/"an" to be a standalone word.
        r"you\s+are\s+now\s+(a|an|no\s+longer)\b",
        r"act\s+as\s+(if\s+you\s+(are|were)|an?)\b",
        r"pretend\s+(that\s+)?you\s+(are|have|can)\b",
        r"reveal\s+(your|the)\s+(system\s+)?(prompt|instructions)",
        r"(print|show|display|output)\s+(your|the)\s+(system\s+)?(prompt|instructions)",
        r"what\s+(is|are)\s+your\s+(system\s+)?(prompt|instructions)",
        r"new\s+instructions\s*:",
        r"\[\s*/?\s*(system|inst)\s*\]|<\s*/?\s*system\s*>",
        r"###\s*system",
        r"developer\s+mode",
        r"\bdan\s+mode\b",
        r"\bjailbreak\b",
        r"bypass\s+(your|any)\s*(restrictions|rules|filters|guidelines|safety)",
        r"do\s+anything\s+now",
    )
)


@dataclass
class GuardrailResult:
    """`flagged=False` (the overwhelming majority case) means nothing else needs to happen --
    callers only branch on `flagged`; `reason` is for logging/audit only, never shown to the
    citizen verbatim (see ask_janmitra_service.py's own citizen-safe fallback text)."""

    flagged: bool
    reason: str | None = None


def check_input(text: str) -> GuardrailResult:
    """Scans a citizen's raw message for known prompt-injection/jailbreak phrasing. Never raises;
    an empty/blank message is never flagged (nothing to inject)."""
    if not text or not text.strip():
        return GuardrailResult(flagged=False)
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            return GuardrailResult(flagged=True, reason=f"input matched injection pattern: {pattern.pattern}")
    return GuardrailResult(flagged=False)


def _contains_verbatim_leak(text: str, source: str, window: int = 40, step: int = 20) -> bool:
    """True if any `window`-character run of `source` (whitespace-normalized, so line-wrapping in
    the prompt file doesn't defeat this) appears verbatim inside `text`. A real, direct structural
    check for system-prompt disclosure -- no NLP/embedding similarity needed, since a genuine leak
    reproduces the prompt's own wording exactly, not a paraphrase of it."""
    normalized_source = " ".join(source.split())
    normalized_text = " ".join(text.split())
    if len(normalized_source) < window:
        return normalized_source.lower() in normalized_text.lower() if normalized_source else False
    normalized_text_lower = normalized_text.lower()
    for start in range(0, len(normalized_source) - window + 1, step):
        chunk = normalized_source[start : start + window].lower()
        if chunk in normalized_text_lower:
            return True
    return False


def check_output(text: str, system_prompt: str | None = None) -> GuardrailResult:
    """Scans the LLM's own generated answer for (a) the same injection markers -- a sign the
    model's reply visibly complied with or echoed back an attempted override -- and (b), when
    `system_prompt` is provided, verbatim leakage of this app's own internal prompt text. Never
    raises; an empty answer is never flagged."""
    if not text or not text.strip():
        return GuardrailResult(flagged=False)
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            return GuardrailResult(flagged=True, reason=f"output matched injection pattern: {pattern.pattern}")
    if system_prompt and _contains_verbatim_leak(text, system_prompt):
        return GuardrailResult(flagged=True, reason="output contains a verbatim leak of system prompt text")
    return GuardrailResult(flagged=False)
