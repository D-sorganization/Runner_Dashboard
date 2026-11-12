"""Shared GitHub API rate-limit governor for httpx and ``gh`` subprocess paths (#4278 I-12).

One token bucket (plus secondary-limit backoff) per GitHub installation / credential
identity so dashboard traffic from ``gh_client`` and ``gh_utils.run_cmd`` draws
from the same budget.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Mapping
from typing import Any

log = logging.getLogger("dashboard.github_rate_governor")

# GitHub REST primary limit for app/installation tokens (requests per hour).
_DEFAULT_CAPACITY = 5000.0
_DEFAULT_REFILL_PER_SEC = _DEFAULT_CAPACITY / 3600.0
_DEFAULT_MAX_WAIT_SEC = float(os.environ.get("GITHUB_GOVERNOR_MAX_WAIT_SEC", "30"))


class GitHubGovernorExhausted(TimeoutError):
    """Raised when the governor cannot grant a token within the bounded wait."""

    def __init__(self, *, retry_after_seconds: float, identity: str) -> None:
        super().__init__(f"GitHub rate governor exhausted for {identity}; retry after {retry_after_seconds:.1f}s")
        self.retry_after_seconds = max(0.1, retry_after_seconds)
        self.identity = identity


class GitHubRateGovernor:
    """Thread-safe token bucket with secondary-limit backoff and header sync."""

    def __init__(
        self,
        *,
        identity: str,
        capacity: float = _DEFAULT_CAPACITY,
        refill_per_sec: float = _DEFAULT_REFILL_PER_SEC,
        max_wait_sec: float = _DEFAULT_MAX_WAIT_SEC,
    ) -> None:
        self.identity = identity
        self._capacity = max(1.0, capacity)
        self._refill_per_sec = max(0.0, refill_per_sec)
        self._max_wait_sec = max(0.0, max_wait_sec)
        self._lock = threading.Lock()
        self._tokens = self._capacity
        self._last_refill = time.monotonic()
        self._blocked_until = 0.0
        self._header_reset_epoch: int | None = None

    def configure_for_tests(self, *, capacity: float, refill_per_sec: float) -> None:
        with self._lock:
            self._capacity = max(1.0, capacity)
            self._refill_per_sec = max(0.0, refill_per_sec)
            self._tokens = min(self._tokens, self._capacity)
            self._last_refill = time.monotonic()

    def tokens_available(self) -> float:
        with self._lock:
            self._refill_locked(time.monotonic())
            return self._tokens

    def backoff_remaining_seconds(self) -> float:
        with self._lock:
            return max(0.0, self._blocked_until - time.monotonic())

    def _refill_locked(self, now: float) -> None:
        if self._refill_per_sec <= 0:
            return
        elapsed = max(0.0, now - self._last_refill)
        if elapsed <= 0:
            return
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_sec)
        self._last_refill = now

    def _wait_seconds_locked(self, now: float, cost: float) -> float:
        if now < self._blocked_until:
            return self._blocked_until - now
        self._refill_locked(now)
        if self._tokens >= cost:
            return 0.0
        if self._refill_per_sec <= 0:
            return float("inf")
        return (cost - self._tokens) / self._refill_per_sec

    def acquire(self, *, cost: float = 1.0, max_wait: float | None = None) -> None:
        """Block until ``cost`` tokens are available or raise after ``max_wait``."""
        cost = max(0.0, cost)
        budget = self._max_wait_sec if max_wait is None else max(0.0, max_wait)
        deadline = time.monotonic() + budget
        identity = self.identity

        while True:
            with self._lock:
                now = time.monotonic()
                wait = self._wait_seconds_locked(now, cost)
                if wait <= 0:
                    self._tokens -= cost
                    return
            remaining_budget = deadline - time.monotonic()
            if remaining_budget <= 0 or wait == float("inf"):
                with self._lock:
                    retry = max(self._blocked_until - time.monotonic(), 0.1)
                    if self._refill_per_sec > 0 and self._tokens < cost:
                        retry = max(retry, (cost - self._tokens) / self._refill_per_sec)
                raise GitHubGovernorExhausted(retry_after_seconds=min(retry, budget or retry), identity=identity)
            time.sleep(min(wait, remaining_budget, 0.25))

    async def acquire_async(self, *, cost: float = 1.0, max_wait: float | None = None) -> None:
        """Async variant of :meth:`acquire` (does not block the event loop)."""
        cost = max(0.0, cost)
        budget = self._max_wait_sec if max_wait is None else max(0.0, max_wait)
        deadline = time.monotonic() + budget
        identity = self.identity

        while True:
            with self._lock:
                now = time.monotonic()
                wait = self._wait_seconds_locked(now, cost)
                if wait <= 0:
                    self._tokens -= cost
                    return
            remaining_budget = deadline - time.monotonic()
            if remaining_budget <= 0 or wait == float("inf"):
                with self._lock:
                    retry = max(self._blocked_until - time.monotonic(), 0.1)
                    if self._refill_per_sec > 0 and self._tokens < cost:
                        retry = max(retry, (cost - self._tokens) / self._refill_per_sec)
                raise GitHubGovernorExhausted(retry_after_seconds=min(retry, budget or retry), identity=identity)
            await asyncio.sleep(min(wait, remaining_budget, 0.25))

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str:
        for key, value in headers.items():
            if key.lower() == name.lower():
                return value.strip()
        return ""

    def observe_response_headers(self, headers: Mapping[str, str]) -> None:
        """Reconcile local tokens with GitHub ``X-RateLimit-*`` response headers."""
        remaining_raw = self._header(headers, "X-RateLimit-Remaining")
        reset_raw = self._header(headers, "X-RateLimit-Reset")
        if not remaining_raw.isdigit():
            return
        remaining = float(remaining_raw)
        capped = min(self._capacity, remaining)
        with self._lock:
            reset_epoch = int(reset_raw) if reset_raw.isdigit() else None
            tracked = self._header_reset_epoch
            if reset_epoch is not None and tracked is not None and reset_epoch < tracked:
                # Response from a prior rate-limit window; ignore remaining (stale ordering).
                pass
            elif reset_epoch is not None and (tracked is None or reset_epoch > tracked):
                # New window: GitHub reset the primary bucket; trust its remaining count.
                self._header_reset_epoch = reset_epoch
                self._tokens = capped
                self._last_refill = time.monotonic()
            else:
                # Same window (or no reset header): never raise balance from out-of-order responses.
                self._tokens = min(self._tokens, capped)
                if reset_epoch is not None and tracked is None:
                    self._header_reset_epoch = reset_epoch
            if reset_epoch is not None:
                seconds_until_reset = max(0, reset_epoch - int(time.time()))
                if remaining <= 0 and seconds_until_reset > 0:
                    self._blocked_until = max(self._blocked_until, time.monotonic() + seconds_until_reset)

    def record_rate_limit(self, *, retry_after_seconds: int, reset_epoch: int | None = None) -> None:
        """Trip backoff after a 429 or exhausted primary limit."""
        retry_after_seconds = max(1, int(retry_after_seconds))
        with self._lock:
            now = time.monotonic()
            block_until = now + retry_after_seconds
            if reset_epoch is not None:
                block_until = max(block_until, now + max(0, reset_epoch - int(time.time())))
            self._blocked_until = max(self._blocked_until, block_until)
            self._tokens = 0.0

    def _parse_retry_after(self, headers: Mapping[str, str]) -> int:
        raw = self._header(headers, "Retry-After")
        if raw.isdigit():
            return max(1, int(raw))
        return 60

    def record_rate_limit_from_headers(
        self,
        *,
        status_code: int,
        headers: Mapping[str, str],
        body: str = "",
    ) -> None:
        """Honor ``Retry-After`` / secondary-limit bodies on 403 and 429."""
        is_limit = (
            status_code == 429
            or self._header(headers, "X-RateLimit-Remaining") == "0"
            or looks_rate_limited_output(body)
        )
        if not is_limit:
            return
        retry_after = self._parse_retry_after(headers)
        reset_raw = self._header(headers, "X-RateLimit-Reset")
        reset_epoch = int(reset_raw) if reset_raw.isdigit() else None
        self.record_rate_limit(retry_after_seconds=retry_after, reset_epoch=reset_epoch)


_REGISTRY: dict[str, GitHubRateGovernor] = {}
_REGISTRY_LOCK = threading.Lock()


def _default_identity() -> str:
    try:
        import gh_client

        return gh_client.get_auth_fingerprint()
    except Exception:  # noqa: BLE001
        log.debug("github_rate_governor: gh_client fingerprint unavailable", exc_info=True)
    token = os.environ.get("GH_TOKEN", "").strip() or os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        from hashlib import sha256

        return f"token:{sha256(token.encode()).hexdigest()[:16]}"
    app_id = os.environ.get("GITHUB_APP_ID", "").strip()
    installation_id = os.environ.get("GITHUB_APP_INSTALLATION_ID", "").strip()
    if app_id and installation_id:
        return f"github_app:{app_id}:{installation_id}"
    return "anonymous"


def get_governor(identity: str | None = None) -> GitHubRateGovernor:
    key = identity or _default_identity()
    with _REGISTRY_LOCK:
        gov = _REGISTRY.get(key)
        if gov is None:
            gov = GitHubRateGovernor(identity=key)
            _REGISTRY[key] = gov
        return gov


def reset_governors_for_tests() -> None:
    with _REGISTRY_LOCK:
        _REGISTRY.clear()


async def acquire_before_github_call_async(*, cost: float = 1.0, identity: str | None = None) -> None:
    await get_governor(identity).acquire_async(cost=cost)


def acquire_before_github_call(*, cost: float = 1.0, identity: str | None = None) -> None:
    get_governor(identity).acquire(cost=cost)


def observe_github_response(headers: Mapping[str, str], *, identity: str | None = None) -> None:
    get_governor(identity).observe_response_headers(headers)


def record_github_rate_limit(
    *,
    retry_after_seconds: int,
    reset_epoch: int | None = None,
    identity: str | None = None,
) -> None:
    get_governor(identity).record_rate_limit(retry_after_seconds=retry_after_seconds, reset_epoch=reset_epoch)


def looks_rate_limited_output(text: str) -> bool:
    """Detect rate-limit errors in combined ``gh`` CLI stderr/stdout."""
    lower = text.lower()
    return (
        "x-ratelimit-remaining: 0" in lower
        or "api rate limit exceeded" in lower
        or "secondary rate limit" in lower
        or "rate limit exceeded" in lower
    )


def parse_gh_cli_retry_after_seconds(text: str) -> int:
    """Parse ``Retry-After`` from ``gh api -i`` style header lines in CLI output."""
    for line in text.splitlines():
        name, separator, value = line.partition(":")
        if separator and name.strip().lower() == "retry-after":
            value = value.strip()
            if value.isdigit():
                return max(1, int(value))
    return 60


def record_github_rate_limit_from_response(
    *,
    status_code: int,
    headers: Mapping[str, str],
    body: str = "",
    identity: str | None = None,
) -> None:
    get_governor(identity).record_rate_limit_from_headers(status_code=status_code, headers=headers, body=body)


def governor_status_snapshot() -> dict[str, Any]:
    """Lightweight JSON-safe status for diagnostics."""
    gov = get_governor()
    return {
        "identity": gov.identity,
        "tokens_available": round(gov.tokens_available(), 2),
        "backoff_remaining_seconds": round(gov.backoff_remaining_seconds(), 2),
    }
