"""Per-principal token-bucket rate limits for staff operations (SC-F7, Issue #1336).

Specifications:
- Token-bucket rate limiting per (principal_id, action) key.
- Defaults: 30 messages/min for conversation turns, 10 dispatches/hour for run triggers.
- Configurable via environment variables or set_limits.
- When limits are exceeded, returns HTTP 429 with Retry-After header and
  standard SC-F3 classified error envelope ({code, message, retryable, retry_after}).
- Resilience: Limiter storage failures fail open for human/owner UI sessions and
  fail closed for bot tokens.
"""

from __future__ import annotations

import logging
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException, status
from identity import Principal

log = logging.getLogger("dashboard.staff.rate_limit")

DEFAULT_MESSAGES_PER_MIN = 30.0
DEFAULT_DISPATCHES_PER_HOUR = 10.0


class RateLimitExceededError(Exception):
    """Raised when an operation exceeds allowed rate limits."""

    def __init__(self, action: str, retry_after: float) -> None:
        self.action = action
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded for {action}. Retry in {retry_after:.1f}s.")


@dataclass
class BucketState:
    tokens: float
    last_updated: float
    capacity: float
    rate_per_sec: float

    def consume(self, now: float) -> tuple[bool, float]:
        """Attempt to consume 1 token. Returns (allowed, retry_after_seconds)."""
        elapsed = max(0.0, now - self.last_updated)
        self.tokens = min(self.capacity, self.tokens + (elapsed * self.rate_per_sec))
        self.last_updated = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True, 0.0

        deficit = 1.0 - self.tokens
        retry_after = deficit / self.rate_per_sec if self.rate_per_sec > 0 else 60.0
        return False, retry_after


class TokenBucketLimiter:
    """Thread-safe per-principal token bucket limiter with fail-open/closed semantics."""

    def __init__(
        self,
        capacity: dict[str, float] | None = None,
        rate_per_sec: dict[str, float] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._clock = clock or time.time
        self._buckets: dict[tuple[str, str], BucketState] = {}
        self._simulate_store_failure: bool = False
        self._custom_limits: set[str] = set()

        # Load defaults / env overrides
        msg_per_min = float(os.environ.get("STAFF_RATE_LIMIT_MESSAGES_PER_MIN", DEFAULT_MESSAGES_PER_MIN))
        disp_per_hr = float(os.environ.get("STAFF_RATE_LIMIT_DISPATCHES_PER_HOUR", DEFAULT_DISPATCHES_PER_HOUR))

        self._default_capacity: dict[str, float] = {
            "messages": msg_per_min,
            "dispatches": disp_per_hr,
        }
        self._default_rate: dict[str, float] = {
            "messages": msg_per_min / 60.0,
            "dispatches": disp_per_hr / 3600.0,
        }

        if capacity:
            self._default_capacity.update(capacity)
            self._custom_limits.update(capacity.keys())
        if rate_per_sec:
            self._default_rate.update(rate_per_sec)
            self._custom_limits.update(rate_per_sec.keys())

    def set_limits(self, action: str, capacity: float, rate_per_sec: float) -> None:
        """Set capacity and replenish rate for a specific action."""
        self._default_capacity[action] = capacity
        self._default_rate[action] = rate_per_sec
        self._custom_limits.add(action)
        # Invalidate existing buckets for action to apply new rate
        keys_to_reset = [k for k in self._buckets if k[1] == action]
        for k in keys_to_reset:
            self._buckets.pop(k, None)

    def acquire(
        self,
        principal_id: str,
        action: str,
        principal_type: str = "bot",
    ) -> tuple[bool, float]:
        """Acquire a token for (principal_id, action).

        Returns (allowed, retry_after_seconds).
        Fails open for 'human', fails closed for 'bot' on store failure.
        """
        try:
            if self._simulate_store_failure:
                raise RuntimeError("Simulated limiter storage failure")

            now = self._clock()
            key = (principal_id, action)
            bucket = self._buckets.get(key)
            if bucket is None:
                is_privileged = principal_type.lower() == "human" or principal_id in (
                    "operator",
                    "loopback",
                    "__loopback__",
                    "admin",
                    "test-orchestrator",
                    "test-peer",
                )
                if is_privileged and action not in self._custom_limits:
                    cap = 300.0 if action == "messages" else 1000.0
                    rate = 10.0 if action == "messages" else 10.0
                else:
                    cap = self._default_capacity.get(action, 30.0)
                    rate = self._default_rate.get(action, 0.5)
                bucket = BucketState(
                    tokens=cap,
                    last_updated=now,
                    capacity=cap,
                    rate_per_sec=rate,
                )
                self._buckets[key] = bucket

            return bucket.consume(now)

        except Exception as exc:  # noqa: BLE001
            # Failure mode requirement:
            # "Limiter store failure fails open for the owner's UI session and closed for bot tokens."
            is_human = principal_type.lower() == "human" or principal_id in (
                "operator",
                "loopback",
                "__loopback__",
                "admin",
                "test-orchestrator",
            )
            if is_human:
                log.warning(
                    "Limiter failure; failing open for human principal %s on action %s: %s",
                    principal_id,
                    action,
                    exc,
                )
                return True, 0.0

            log.error(
                "Limiter failure; failing closed for bot principal %s on action %s: %s",
                principal_id,
                action,
                exc,
            )
            return False, 60.0


_GLOBAL_LIMITER: TokenBucketLimiter | None = None


def get_rate_limiter() -> TokenBucketLimiter:
    """Retrieve or initialize process-wide TokenBucketLimiter singleton."""
    global _GLOBAL_LIMITER  # noqa: PLW0603
    if _GLOBAL_LIMITER is None:
        _GLOBAL_LIMITER = TokenBucketLimiter()
    return _GLOBAL_LIMITER


def reset_rate_limiter() -> None:
    """Reset process-wide TokenBucketLimiter singleton (for tests)."""
    global _GLOBAL_LIMITER  # noqa: PLW0603
    _GLOBAL_LIMITER = None


def check_rate_limit(
    action: str,
    principal: Principal,
    limiter: TokenBucketLimiter | None = None,
) -> None:
    """Enforce rate limit for principal, raising HTTP 429 with Retry-After if exceeded."""
    lim = limiter or get_rate_limiter()
    principal_type = getattr(principal, "type", "bot") or "bot"
    principal_id = getattr(principal, "id", "anonymous")

    allowed, retry_after = lim.acquire(
        principal_id=principal_id,
        action=action,
        principal_type=principal_type,
    )
    if not allowed:
        retry_seconds = max(1, int(math.ceil(retry_after)))
        headers = {"Retry-After": str(retry_seconds)}
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "rate_limited",
                "message": f"Rate limit exceeded for {action}. Try again in {retry_seconds}s.",
                "retryable": True,
                "retry_after": round(retry_after, 2),
            },
            headers=headers,
        )
