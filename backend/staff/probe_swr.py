"""Stale-while-revalidate for slow synchronous probes called from async code.

The CLI OAuth probes (``claude auth status``, ``agy models``) run a subprocess
with timeouts of 8-30 seconds. Many async route handlers call them through
synchronous helpers, so a cache miss used to freeze the single event loop and
every other request with it (measured: a 300-660 ms stall per miss with a fast
stub CLI; up to the full timeout with a slow real one).

``swr_probe`` keeps the last known answer. When the fresh entry has expired and
the caller is on an event-loop thread, it returns the last known answer at once
and refreshes it on a daemon thread. Callers off the loop (worker threads, CLI
scripts, sync tests) keep the old blocking behaviour, so nothing changes for
them. The very first probe still blocks, because there is no answer to serve.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import TypeVar

from cache_utils import cache_delete, cache_get, cache_set

log = logging.getLogger("dashboard.staff.probe_swr")

T = TypeVar("T")

_last_known: dict[str, object] = {}
_in_flight: set[str] = set()
_lock = threading.Lock()


def on_event_loop_thread() -> bool:
    """True when the calling thread is currently running an asyncio event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def run_in_background(key: str, work: Callable[[], None]) -> bool:
    """Run ``work`` once on a daemon thread; skip if a run for ``key`` is in flight.

    Post: never raises; returns True when a new thread was started.
    """
    with _lock:
        if key in _in_flight:
            return False
        _in_flight.add(key)

    def _target() -> None:
        try:
            work()
        except Exception as exc:  # noqa: BLE001 — a background probe must never kill the thread silently
            log.warning("background probe %s failed: %s", key, type(exc).__name__)
        finally:
            with _lock:
                _in_flight.discard(key)

    threading.Thread(target=_target, name=f"probe-swr:{key}", daemon=True).start()
    return True


def store(key: str, value: T) -> T:
    """Record ``value`` as both the fresh and the last known answer for ``key``."""
    cache_set(key, value)
    with _lock:
        _last_known[key] = value
    return value


def expire(key: str) -> None:
    """Drop the fresh entry but keep the last known answer (next loop call revalidates)."""
    cache_delete(key)


def forget(key: str) -> None:
    """Drop the fresh entry and the last known answer (next call blocks for a real probe)."""
    cache_delete(key)
    with _lock:
        _last_known.pop(key, None)


def swr_probe(key: str, ttl: float, factory: Callable[[], T]) -> T:
    """Return a cached probe result, refreshing stale results off the event loop.

    Pre: ``factory`` is safe to call from a worker thread.
    """
    cached = cache_get(key, ttl)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    with _lock:
        stale = _last_known.get(key)
    if stale is not None and on_event_loop_thread():

        def _refresh() -> None:
            store(key, factory())

        run_in_background(key, _refresh)
        return stale  # type: ignore[return-value]
    return store(key, factory())
