"""Thin wrapper around sentry_sdk.metrics that actually respects SENTRY_ENABLE_METRICS.

Necessary because the installed sentry-sdk version's own `enable_metrics` init() option is a
confirmed no-op: read directly from the installed package (sentry_sdk/client.py, Client.__init__)
-- `self.metrics_batcher = MetricsBatcher(...)` is constructed unconditionally regardless of the
flag's value; passing `enable_metrics=False` only triggers a "has no effect and will be removed"
deprecation warning. sentry_sdk.metrics.count()/gauge()/distribution() would therefore always
send once Sentry is initialized at all, even with SENTRY_ENABLE_METRICS=false -- the opposite of
what that setting is supposed to do.

Gating happens HERE instead, at the one place every metrics call site in this codebase already
goes through, rather than repeating `if settings.SENTRY_ENABLE_METRICS:` at each of the 6 call
sites (routes/complaints.py, routes/ask_sarthi.py x3, middleware.py, deps.py x2) -- one place
to get right, one place a future call site can't forget.
"""

from __future__ import annotations

from typing import Any

from sentry_sdk import metrics as _sentry_metrics

from backend.config import settings


def count(name: str, value: float = 1, attributes: dict[str, Any] | None = None) -> None:
    if not settings.SENTRY_ENABLE_METRICS:
        return
    _sentry_metrics.count(name, value, attributes=attributes)
