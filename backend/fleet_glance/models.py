"""Validated domain models and aggregation for Fleet at a glance."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, TypeVar

_COUNT_FIELDS = ("queued", "in_progress", "pending", "issues", "prs")


@dataclass(frozen=True)
class RepoCounts:
    """Counts for one configured repository; error rows are excluded in totals."""

    repo: str
    queued: int = 0
    in_progress: int = 0
    pending: int = 0
    issues: int = 0
    prs: int = 0
    error: str | None = None
    stale: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.repo, str) or not self.repo or "/" not in self.repo:
            raise ValueError("repo must be an org/repository name")
        for field_name in _COUNT_FIELDS:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.error is not None and (
            not isinstance(self.error, str) or not self.error.strip()
        ):
            raise ValueError("error must be non-empty when provided")


@dataclass(frozen=True)
class FleetGlance:
    """Aggregated snapshot with per-repository detail and UTC capture time."""

    repos: list[RepoCounts]
    totals: dict[str, int]
    errors: list[str]
    as_of: datetime


def aggregate(
    repos: list[RepoCounts], *, as_of: datetime | None = None
) -> FleetGlance:
    """Sum healthy repositories and retain failed rows for visible diagnostics."""
    timestamp = as_of or datetime.now(UTC)
    if (
        timestamp.tzinfo is None
        or timestamp.utcoffset() != UTC.utcoffset(timestamp)
    ):
        raise ValueError("as_of must be a UTC timestamp")

    totals = {name: 0 for name in _COUNT_FIELDS}
    errors: list[str] = []
    for repo in repos:
        if repo.error:
            errors.append(repo.repo)
            continue
        for name in _COUNT_FIELDS:
            totals[name] += getattr(repo, name)
    return FleetGlance(repos=list(repos), totals=totals, errors=errors, as_of=timestamp)


T = TypeVar("T")


class TtlCache(Generic[T]):
    """Small thread-safe TTL cache that coalesces concurrent loads per key."""

    def __init__(
        self,
        ttl_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._values: dict[str, tuple[float, T]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> T | None:
        """Return a fresh cached value, or ``None`` when absent or expired."""
        with self._lock:
            entry = self._values.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if self._clock() >= expires_at:
                del self._values[key]
                return None
            return value

    def set(self, key: str, value: T) -> None:
        """Store a value for one TTL window."""
        if not key:
            raise ValueError("key must be non-empty")
        with self._lock:
            self._values[key] = (self._clock() + self._ttl_seconds, value)

    def get_or_load(self, key: str, loader: Callable[[], T]) -> T:
        """Load once per key while concurrent callers wait for the fresh value."""
        if not key:
            raise ValueError("key must be non-empty")
        with self._lock:
            entry = self._values.get(key)
            if entry is not None and self._clock() < entry[0]:
                return entry[1]
            value = loader()
            self._values[key] = (self._clock() + self._ttl_seconds, value)
            return value
