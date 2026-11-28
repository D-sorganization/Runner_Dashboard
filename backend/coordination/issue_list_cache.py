"""In-process TTL cache for the job-board open-issues listing (#4702).

``GET /api/coordination/jobs`` shells out to ``gh api --paginate`` for the full
open-issues list on every request. That single call alone can consume most of
the coordination response budget (see #4702) and turn an otherwise-usable
listing into a bare 503. A fresh listing is reused for a short TTL, and a load
that misses the request budget (or fails) serves the last-known list with
``complete=False`` instead of blocking past the deadline or returning nothing.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("dashboard.coordination.issue_list_cache")

DEFAULT_TTL_SECONDS = 60.0
DEFAULT_BUDGET_SECONDS = 2.5

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job-board-issues")
_lock = threading.Lock()
_cache: dict[str, _Entry] = {}
_inflight: dict[str, Future[_Entry]] = {}
_clock = time.monotonic


@dataclass(frozen=True)
class _Entry:
    issues: list[dict[str, Any]]
    fetched_mono: float


def reset() -> None:
    """Drop cached listings and in-flight loads (tests)."""
    with _lock:
        _cache.clear()
        _inflight.clear()


def read(
    repo_full: str,
    loader: Callable[[], list[dict[str, Any]]],
    *,
    budget_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Return ``(issues, complete)`` for one repo's open issues.

    A fresh cache hit returns the cached listing without calling ``loader``.
    On a miss the loader runs on a worker thread; if it misses the budget or
    raises, the last-known listing is returned with ``complete=False`` (or an
    empty list when nothing is cached yet).

    Post: ``complete`` is True exactly when ``issues`` is a fresh listing
    produced by ``loader`` or served from a fresh cache entry.
    """
    key = _cache_key(repo_full)
    budget = float(budget_seconds) if budget_seconds is not None else _budget_seconds()

    fresh = _fresh_hit(key)
    if fresh is not None:
        return fresh.issues, True

    future = _shared_load(key, loader)
    try:
        entry = future.result(timeout=budget)
    except FuturesTimeoutError:
        log.warning("issue list for %s missed the %ss budget", repo_full, budget)
        return _stale_or_empty(key)
    except Exception as exc:  # noqa: BLE001 — serve last-known on any loader failure
        log.warning("issue list for %s failed: %s", repo_full, exc)
        return _stale_or_empty(key)
    return entry.issues, True


def _stale_or_empty(key: str) -> tuple[list[dict[str, Any]], bool]:
    stale = _stale_hit(key)
    if stale is not None:
        return stale.issues, False
    return [], False


def _cache_key(repo_full: str) -> str:
    return repo_full.strip().casefold()


def _fresh_hit(key: str) -> _Entry | None:
    now = _clock()
    with _lock:
        hit = _cache.get(key)
        if hit is not None and now - hit.fetched_mono < _ttl_seconds():
            return hit
    return None


def _stale_hit(key: str) -> _Entry | None:
    with _lock:
        return _cache.get(key)


def _shared_load(key: str, loader: Callable[[], list[dict[str, Any]]]) -> Future[_Entry]:
    with _lock:
        inflight = _inflight.get(key)
        if inflight is not None:
            return inflight
        future = _pool.submit(_compute_and_store, key, loader)
        _inflight[key] = future
        future.add_done_callback(lambda completed: _drop_inflight(key, completed))
        return future


def _compute_and_store(key: str, loader: Callable[[], list[dict[str, Any]]]) -> _Entry:
    issues = list(loader())
    entry = _Entry(issues=issues, fetched_mono=_clock())
    with _lock:
        _cache[key] = entry
    return entry


def _drop_inflight(key: str, future: Future[_Entry]) -> None:
    with _lock:
        if _inflight.get(key) is future:
            _inflight.pop(key, None)


def _ttl_seconds() -> float:
    return float(os.environ.get("JOB_BOARD_ISSUE_LIST_TTL_SECONDS", DEFAULT_TTL_SECONDS))


def _budget_seconds() -> float:
    return float(os.environ.get("JOB_BOARD_ISSUE_LIST_BUDGET_SECONDS", DEFAULT_BUDGET_SECONDS))
