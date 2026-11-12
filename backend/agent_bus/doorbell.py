"""In-process doorbell for agent-bus push delivery.

``AgentBusStore.send`` runs in sync code (often a threadpool worker), while SSE
subscribers wait on the event loop. ``ring`` is thread-safe: it bumps a
generation counter and wakes every waiter via ``call_soon_threadsafe``. Waiters
re-query SQLite after each wake, so a missed ring only costs the fallback
interval, never a message.
"""

from __future__ import annotations

import asyncio
import threading

_lock = threading.Lock()
_generation = 0
_waiters: set[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = set()


def generation() -> int:
    return _generation


def ring() -> int:
    """Wake every subscriber; safe to call from any thread."""
    global _generation
    with _lock:
        _generation += 1
        waiters = list(_waiters)
        gen = _generation
    for loop, event in waiters:
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:  # loop closed
            continue
    return gen


async def wait(since: int, timeout: float) -> bool:
    """Return True when the generation moved past ``since`` within ``timeout`` seconds."""
    if _generation != since:
        return True
    event = asyncio.Event()
    entry = (asyncio.get_running_loop(), event)
    with _lock:
        _waiters.add(entry)
    try:
        if _generation != since:
            return True
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            return _generation != since
        return True
    finally:
        with _lock:
            _waiters.discard(entry)


def subscriber_count() -> int:
    return len(_waiters)
