"""Sarvam API key rotation, shared by every service that calls the Sarvam SDK (sarvam_client.py,
answer_generation_service.py, summary_service.py, normalization_service.py,
complaint_category_service.py).

LIVE-REPORTED INCIDENT (2026-08-31): the configured Sarvam account ran out of credit mid-
production, failing every real call with `ApiError(status_code=402, body={"error": {"code":
"insufficient_quota_error", ...}})` -- confirmed directly against api.sarvam.ai. Every caller
already degrades gracefully when that happens (falls back to raw excerpts / skips normalization /
etc -- see each service's own docstring), but a degraded citizen-facing answer is still a worse
outcome than a real one when a second, funded key is available.

Configured via SARVAM_API_KEYS (comma-separated, up to 5 keys) in .env -- falls back to the
existing single SARVAM_API_KEY/LLM_API_KEY when SARVAM_API_KEYS is unset, so a deployment with
only one key needs no .env change at all. Adding, rotating, or retiring a key going forward is
purely an .env edit + container restart on the VM -- the key values themselves never touch the
repo, exactly like SARVAM_API_KEY always has.

`SarvamKeyRotationMixin` is deliberately shaped around `self._client` as a plain, directly-
settable attribute -- not hidden behind a pool object -- because the existing test suite already
established the convention of bypassing `__init__` (`SomeService.__new__(SomeService)`) and
setting `svc._client = fake_sdk`/`Mock()`/`None` directly (see test_sarvam_client.py,
test_rag_grounding_topic_mismatch.py). Keeping `_client` as the real, live attribute means that
convention keeps working completely unchanged; rotation is additive and only ever activates when
SARVAM_API_KEYS actually configures more than one key, a case those tests don't exercise.

`_init_sarvam_keys()` also takes an explicit `client_factory` (defaulting to this module's own
`SarvamAI`) rather than hardcoding the class here: three other services' tests
(test_normalization_service.py, test_summary_service.py, test_complaint_category_service.py)
already establish a SEPARATE convention -- `monkeypatch.setattr("backend.services.<module>.SarvamAI",
fake_factory)` -- which only works if each service's own `__init__` calls a `SarvamAI` name bound
in ITS OWN module's namespace (so the patch takes effect), not a name resolved inside this shared
module. Each of those services passes its own module-level `SarvamAI` import through explicitly
for exactly this reason.
"""

import logging
from typing import Callable, TypeVar

import httpx
from sarvamai import SarvamAI
from sarvamai.core.api_error import ApiError

logger = logging.getLogger(__name__)

T = TypeVar("T")

_QUOTA_EXHAUSTED_STATUS = 402


class SarvamKeyRotationMixin:
    """Mixin providing `self._client` (the active `SarvamAI` instance, or `None` if unconfigured)
    plus automatic rotation to the next configured key on quota exhaustion.

    Usage: call `self._init_sarvam_keys(timeout, keys)` once in `__init__` (in place of building
    a bare `SarvamAI(...)` directly), then route every real SDK call through
    `self._call_sarvam(lambda client: client.<...>(...))` instead of calling `self._client.<...>`
    directly -- everything else (the `self._client is None` configured-check, error handling)
    stays exactly as it already was in each service.
    """

    _client: SarvamAI | None
    _sarvam_keys: list[str]
    _sarvam_key_index: int
    _sarvam_timeout: httpx.Timeout
    _sarvam_client_factory: Callable[..., SarvamAI]

    def _init_sarvam_keys(
        self, timeout: httpx.Timeout, keys: str, client_factory: Callable[..., SarvamAI] = SarvamAI
    ) -> None:
        self._sarvam_timeout = timeout
        self._sarvam_client_factory = client_factory
        self._sarvam_keys = [key.strip() for key in keys.split(",") if key.strip()] if keys else []
        self._sarvam_key_index = 0
        self._client = (
            client_factory(api_subscription_key=self._sarvam_keys[0], timeout=timeout)
            if self._sarvam_keys
            else None
        )

    def _call_sarvam(self, invoke: Callable[[SarvamAI], T]) -> T:
        """Runs `invoke(self._client)`, rotating `self._client` forward through any remaining
        configured keys on a 402 insufficient_quota_error before giving up. Any other exception
        (network error, malformed response, a non-quota 4xx/5xx) propagates immediately,
        unchanged -- rotation only ever fires for a confirmed-exhausted key, never as a blanket
        retry-on-any-error that would mask a real bug behind a key swap. Reads `_sarvam_keys`/
        `_sarvam_key_index`/`_sarvam_client_factory` defensively (`getattr` with a safe default)
        so a test that bypasses `__init__` and only sets `self._client` directly still works -- it
        simply never rotates, identical to this method not existing."""
        while True:
            try:
                return invoke(self._client)
            except ApiError as exc:
                keys = getattr(self, "_sarvam_keys", [])
                index = getattr(self, "_sarvam_key_index", 0)
                if exc.status_code == _QUOTA_EXHAUSTED_STATUS and index + 1 < len(keys):
                    self._sarvam_key_index = index + 1
                    next_key = keys[self._sarvam_key_index]
                    logger.warning(
                        "Sarvam key #%d/%d exhausted (insufficient_quota_error); rotating to key #%d.",
                        index + 1,
                        len(keys),
                        self._sarvam_key_index + 1,
                    )
                    factory = getattr(self, "_sarvam_client_factory", SarvamAI)
                    self._client = factory(api_subscription_key=next_key, timeout=self._sarvam_timeout)
                    continue
                raise
