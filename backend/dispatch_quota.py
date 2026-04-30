"""Per-principal hourly dispatch quota tracking (issue #408).

Provides a sliding-window rate limiter that caps the number of bulk-dispatch
operations a single principal may perform within a rolling one-hour window.
The module is intentionally lightweight (in-memory, no external deps) so it
can be imported by ``agent_dispatch_router`` without expanding the runtime
surface.

Design principles
-----------------
- Sliding window: per-principal ``list[float]`` of dispatch timestamps;
  entries older than ``WINDOW_SECONDS`` are pruned on access.
- Hard cap: ``MAX_PER_PRINCIPAL_PER_HOUR`` dispatches per principal per
  rolling hour. Cap exceeded → caller returns HTTP 429 with ``Retry-After``.
- Anonymous rejection: a falsy / synthetic principal is rejected before any
  dispatch, mapped to HTTP 422 by the caller.
- Concurrency: a single ``asyncio.Lock`` protects the in-memory state. All
  helpers are async so callers can ``await`` them inside their own locks.
- Observability: ``METRICS`` exposes counters for ``allowed``,
  ``rejected_anonymous``, ``rejected_rate_limited`` and ``current_principals``
  without requiring a Prometheus dependency.
- Testability: ``time_fn`` is injectable so tests can advance the clock.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TypedDict

# ─── Tunables ─────────────────────────────────────────────────────────────────

WINDOW_SECONDS: int = 3600
MAX_PER_PRINCIPAL_PER_HOUR: int = 100


class QuotaCheck(TypedDict):
    """Result of ``check_and_record``.

    ``allowed`` is True when the dispatch may proceed.  ``retry_after`` is the
    number of whole seconds the caller should advise the client to wait when
    ``allowed`` is False due to the hourly cap; it is 0 for anonymous
    rejections.
    """

    allowed: bool
    reason: str
    retry_after: int


# ─── Module-level metrics (counter-style dict) ────────────────────────────────

METRICS: dict[str, int] = {
    "allowed": 0,
    "rejected_anonymous": 0,
    "rejected_rate_limited": 0,
    "current_principals": 0,
}


class DispatchQuota:
    """Sliding-window dispatch quota tracker.

    A single shared instance lives at module level (``quota``).  Tests may
    construct their own instance with a stubbed ``time_fn`` for deterministic
    behaviour.
    """

    def __init__(
        self,
        *,
        window_seconds: int = WINDOW_SECONDS,
        max_per_window: int = MAX_PER_PRINCIPAL_PER_HOUR,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        self._window_seconds = window_seconds
        self._max_per_window = max_per_window
        self._time_fn: Callable[[], float] = time_fn or time.monotonic
        self._timestamps: dict[str, list[float]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    def is_anonymous(self, principal: str | None) -> bool:
        """Return True when ``principal`` should be treated as anonymous.

        Empty / falsy strings are anonymous.  We additionally treat a small
        set of synthetic identifiers as anonymous to harden against forged
        callers that pass a placeholder string instead of an empty value.
        """
        if not principal:
            return True
        normalised = principal.strip().lower()
        if not normalised:
            return True
        return normalised in {"anonymous", "none", "null", "unknown", "-"}

    async def check_and_record(self, principal: str | None) -> QuotaCheck:
        """Atomically check the per-principal quota and record a dispatch.

        Returns a ``QuotaCheck`` describing whether the dispatch is permitted.
        On success, the current timestamp is recorded so subsequent checks
        within the rolling window count this dispatch.
        """
        if self.is_anonymous(principal):
            METRICS["rejected_anonymous"] += 1
            return QuotaCheck(
                allowed=False,
                reason="anonymous_principal",
                retry_after=0,
            )

        # ``principal`` is non-empty here; cast for the type checker.
        principal_id: str = principal  # type: ignore[assignment]
        async with self._lock:
            now = self._time_fn()
            cutoff = now - self._window_seconds
            timestamps = self._prune_locked(principal_id, cutoff)

            if len(timestamps) >= self._max_per_window:
                # Oldest timestamp is the one that needs to age out for the
                # next dispatch slot to open up.
                oldest = timestamps[0]
                retry_after = max(1, int(self._window_seconds - (now - oldest)) + 1)
                METRICS["rejected_rate_limited"] += 1
                return QuotaCheck(
                    allowed=False,
                    reason="rate_limited",
                    retry_after=retry_after,
                )

            timestamps.append(now)
            self._timestamps[principal_id] = timestamps
            METRICS["allowed"] += 1
            METRICS["current_principals"] = len(self._timestamps)
            return QuotaCheck(
                allowed=True,
                reason="ok",
                retry_after=0,
            )

    async def current_count(self, principal: str) -> int:
        """Return the number of dispatches counted for ``principal`` right now."""
        async with self._lock:
            now = self._time_fn()
            cutoff = now - self._window_seconds
            return len(self._prune_locked(principal, cutoff))

    async def reset(self, principal: str | None = None) -> None:
        """Forget recorded dispatches for ``principal`` (or everyone)."""
        async with self._lock:
            if principal is None:
                self._timestamps.clear()
            else:
                self._timestamps.pop(principal, None)
            METRICS["current_principals"] = len(self._timestamps)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _prune_locked(self, principal_id: str, cutoff: float) -> list[float]:
        """Drop timestamps older than ``cutoff``. Caller must hold ``_lock``."""
        timestamps = self._timestamps.get(principal_id, [])
        if not timestamps:
            return []
        # Timestamps are appended in monotonic order, so a left-trim suffices.
        first_keep = 0
        for idx, ts in enumerate(timestamps):
            if ts >= cutoff:
                first_keep = idx
                break
        else:
            # All timestamps are older than the cutoff.
            self._timestamps.pop(principal_id, None)
            METRICS["current_principals"] = len(self._timestamps)
            return []
        if first_keep > 0:
            timestamps = timestamps[first_keep:]
            self._timestamps[principal_id] = timestamps
        return timestamps


# Module-level singleton used by ``agent_dispatch_router``.
quota: DispatchQuota = DispatchQuota()


__all__ = [
    "MAX_PER_PRINCIPAL_PER_HOUR",
    "METRICS",
    "WINDOW_SECONDS",
    "DispatchQuota",
    "QuotaCheck",
    "quota",
]
