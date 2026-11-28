"""In-process cache for GET ``check_agent_claim`` (#4592).

Concurrent reads for the same ``repo#issue`` share one RM subprocess. A fresh
result is kept for a short TTL; when a read cannot finish within the request
budget, callers receive the last known payload with ``stale: true`` (or 503 when
nothing is cached yet).
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from coordination import claims
from time_utils import utc_now_iso

DEFAULT_TTL_SECONDS = 45.0
DEFAULT_BUDGET_SECONDS = 8.0
DEFAULT_RETRY_AFTER_SECONDS = 5.0

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="claims-check")
_lock = threading.Lock()
_cache: dict[str, _CacheEntry] = {}
_inflight: dict[str, Future[_CacheEntry]] = {}
_generation = 0
_clock = time.monotonic


@dataclass(frozen=True)
class _CacheEntry:
    payload: dict[str, Any]
    as_of: str
    completed_mono: float
    generation: int


class ClaimCheckUnavailable(RuntimeError):
    """No cached claim status and the budget elapsed before RM answered."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after if retry_after is not None else _retry_after_seconds()

    def to_detail(self) -> dict[str, str]:
        return {
            "error": str(self),
            "guidance": "Retry once with jitter after Retry-After; do not hammer this endpoint in lockstep.",
        }


def reset() -> None:
    """Drop cached reads and in-flight work (tests)."""
    global _generation
    with _lock:
        _cache.clear()
        _inflight.clear()
        _generation += 1


def invalidate(repo: str, issue: int) -> None:
    """Drop one ``repo#issue`` entry after a write changed claim state."""
    global _generation
    key = _cache_key(repo, issue)
    with _lock:
        _cache.pop(key, None)
        _inflight.pop(key, None)
        _generation += 1


def invalidate_repo(repo: str) -> None:
    """Drop every cached claim read for ``repo`` (e.g. presence release)."""
    global _generation
    prefix = f"{repo.casefold()}#"
    with _lock:
        for key in list(_cache):
            if key.startswith(prefix):
                _cache.pop(key, None)
        for key in list(_inflight):
            if key.startswith(prefix):
                _inflight.pop(key, None)
        _generation += 1


def read(repo: str, issue: int) -> dict[str, Any]:
    """Return claim status with ``as_of`` / ``stale`` metadata."""
    budget = _budget_seconds()
    deadline = _clock() + budget

    hit = _fresh_hit(repo, issue)
    if hit is not None:
        return _envelope(hit, stale=False)

    future = _shared_load(repo, issue)
    remaining = deadline - _clock()
    try:
        entry = future.result(timeout=max(0.0, remaining))
    except FuturesTimeoutError as exc:
        stale = _stale_hit(repo, issue)
        if stale is not None:
            return _envelope(stale, stale=True)
        raise ClaimCheckUnavailable(
            "claim check not ready within request budget and no cached result",
            retry_after=_retry_after_seconds(),
        ) from exc

    if entry.generation != _current_generation():
        stale = _stale_hit(repo, issue)
        if stale is not None:
            return _envelope(stale, stale=True)
        raise ClaimCheckUnavailable(
            "claim check was invalidated while loading",
            retry_after=_retry_after_seconds(),
        )

    return _envelope(entry, stale=False)


def _ttl_seconds() -> float:
    return float(os.environ.get("CLAIMS_CHECK_CACHE_TTL_SECONDS", DEFAULT_TTL_SECONDS))


def _budget_seconds() -> float:
    return float(os.environ.get("CLAIMS_CHECK_REQUEST_BUDGET_SECONDS", DEFAULT_BUDGET_SECONDS))


def _retry_after_seconds() -> float:
    return float(os.environ.get("CLAIMS_CHECK_RETRY_AFTER_SECONDS", DEFAULT_RETRY_AFTER_SECONDS))


def _current_generation() -> int:
    with _lock:
        return _generation


def _cache_key(repo: str, issue: int) -> str:
    return f"{repo.casefold()}#{issue}"


def _loader(repo: str, issue: int) -> dict[str, Any]:
    return claims.check(repo, issue)


def _compute(repo: str, issue: int, generation: int) -> _CacheEntry:
    payload = _loader(repo, issue)
    return _CacheEntry(payload=payload, as_of=utc_now_iso(), completed_mono=_clock(), generation=generation)


def _drop_inflight(key: str, future: Future[_CacheEntry]) -> None:
    with _lock:
        if _inflight.get(key) is future:
            _inflight.pop(key, None)


def _compute_and_store(repo: str, issue: int, generation: int, key: str) -> _CacheEntry:
    entry = _compute(repo, issue, generation)
    with _lock:
        if generation == _generation:
            _cache[key] = entry
    return entry


def _shared_load(repo: str, issue: int) -> Future[_CacheEntry]:
    key = _cache_key(repo, issue)
    with _lock:
        inflight = _inflight.get(key)
        if inflight is not None:
            return inflight
        # A completed future may have been removed from _inflight by its done
        # callback before all callers reached here. If _cache already has a
        # fresh entry for this generation, return a resolved future so callers
        # never submit a second _loader call.
        cached = _cache.get(key)
        now = _clock()
        if cached is not None and cached.generation == _generation and now - cached.completed_mono < _ttl_seconds():
            resolved: Future[_CacheEntry] = Future()
            resolved.set_result(cached)
            return resolved
        generation = _generation
        future = _pool.submit(_compute_and_store, repo, issue, generation, key)
        _inflight[key] = future
        future.add_done_callback(lambda completed: _drop_inflight(key, completed))
        return future


def _fresh_hit(repo: str, issue: int) -> _CacheEntry | None:
    key = _cache_key(repo, issue)
    now = _clock()
    with _lock:
        hit = _cache.get(key)
        if hit is None or hit.generation != _generation:
            return None
        if now - hit.completed_mono < _ttl_seconds():
            return hit
    return None


def _stale_hit(repo: str, issue: int) -> _CacheEntry | None:
    key = _cache_key(repo, issue)
    with _lock:
        hit = _cache.get(key)
        if hit is None:
            return None
        return hit if hit.generation == _generation else None


def _age_sec(as_of: str) -> float:
    text = as_of.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
    except ValueError:
        return 0.0
    return max(0.0, (datetime.now(UTC) - parsed).total_seconds())


def _envelope(entry: _CacheEntry, *, stale: bool) -> dict[str, Any]:
    out = dict(entry.payload)
    out["as_of"] = entry.as_of
    out["stale"] = stale
    if stale:
        out["age_sec"] = round(_age_sec(entry.as_of), 3)
    return out
