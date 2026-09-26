"""Thread-safe event distribution bus for staff conversation SSE streaming (SC-B3, Issue #1306).

Manages per-thread subscriber queues for real-time delivery of tokens,
message completions, action proposals, and run cards.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("dashboard.staff.thread_bus")


class ThreadEventBus:
    """In-process async pub-sub bus for conversation thread SSE events."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        # The loop each queue was created on: worker threads must hand events to it (#1547).
        self._loops: dict[asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, thread_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Register an async queue to receive live events for a given thread."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._loops[q] = asyncio.get_running_loop()
        async with self._lock:
            if thread_id not in self._subscribers:
                self._subscribers[thread_id] = set()
            self._subscribers[thread_id].add(q)
        return q

    async def unsubscribe(self, thread_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        """Unregister an async queue when a client disconnects."""
        async with self._lock:
            subs = self._subscribers.get(thread_id)
            self._loops.pop(q, None)
            if subs and q in subs:
                subs.remove(q)
                if not subs:
                    self._subscribers.pop(thread_id, None)

    async def publish(
        self,
        thread_id: str,
        event_type: str,
        data: dict[str, Any],
        event_id: int | str | None = None,
    ) -> int:
        """Broadcast an event to all active subscribers for the thread.

        Returns the number of subscribers that received the event.
        """
        payload = {
            "id": event_id,
            "event": event_type,
            "data": data,
        }
        async with self._lock:
            subs = list(self._subscribers.get(thread_id, set()))

        count = 0
        for q in subs:
            try:
                q.put_nowait(payload)
                count += 1
            except asyncio.QueueFull:
                log.warning("Subscriber queue full for thread %s; dropping event", thread_id)
        return count

    def publish_sync(
        self,
        thread_id: str,
        event_type: str,
        data: dict[str, Any],
        event_id: int | str | None = None,
    ) -> int:
        """Broadcast an event from any thread (run workers, action executors).

        Post: each subscriber's queue is fed on the loop that owns it, so a waiting SSE
        generator wakes; an ``asyncio.Queue`` is not thread-safe to feed directly (#1547).
        """
        payload = {
            "id": event_id,
            "event": event_type,
            "data": data,
        }
        subs = list(self._subscribers.get(thread_id, set()))
        count = 0
        for q in subs:
            try:
                loop = self._loops.get(q)
                if loop is not None and loop.is_running() and not _on_loop(loop):
                    loop.call_soon_threadsafe(_put_or_drop, q, payload, thread_id)
                else:
                    _put_or_drop(q, payload, thread_id)
                count += 1
            except RuntimeError:  # the subscriber's loop has closed
                pass
        return count

    def publish_message_sync(self, thread_id: str, message_dict: dict[str, Any]) -> int:
        """:meth:`publish_message` for callers outside the event loop."""
        return self.publish_sync(thread_id, "message", {"message": message_dict}, event_id=message_dict.get("seq"))

    async def publish_token(self, thread_id: str, message_id: str, delta: str) -> int:
        """Helper to broadcast a token delta."""
        return await self.publish(
            thread_id,
            "token",
            {"message_id": message_id, "delta": delta},
        )

    async def publish_message(self, thread_id: str, message_dict: dict[str, Any]) -> int:
        """Helper to broadcast a message creation or update."""
        return await self.publish(
            thread_id,
            "message",
            {"message": message_dict},
            event_id=message_dict.get("seq"),
        )


def _on_loop(loop: asyncio.AbstractEventLoop) -> bool:
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


def _put_or_drop(q: asyncio.Queue[dict[str, Any]], payload: dict[str, Any], thread_id: str) -> None:
    try:
        q.put_nowait(payload)
    except asyncio.QueueFull:
        log.warning("Subscriber queue full for thread %s; dropping event", thread_id)


_BUS: ThreadEventBus | None = None


def get_thread_bus() -> ThreadEventBus:
    """Retrieve the singleton ThreadEventBus instance."""
    global _BUS
    if _BUS is None:
        _BUS = ThreadEventBus()
    return _BUS


def reset_thread_bus() -> None:
    """Reset the singleton ThreadEventBus (primarily for testing)."""
    global _BUS
    _BUS = None
