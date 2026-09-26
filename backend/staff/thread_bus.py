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
        self._lock = asyncio.Lock()

    async def subscribe(self, thread_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Register an async queue to receive live events for a given thread."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        async with self._lock:
            if thread_id not in self._subscribers:
                self._subscribers[thread_id] = set()
            self._subscribers[thread_id].add(q)
        return q

    async def unsubscribe(self, thread_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        """Unregister an async queue when a client disconnects."""
        async with self._lock:
            subs = self._subscribers.get(thread_id)
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
        """Synchronously broadcast an event to active queues without requiring an event loop."""
        payload = {
            "id": event_id,
            "event": event_type,
            "data": data,
        }
        subs = list(self._subscribers.get(thread_id, set()))
        count = 0
        for q in subs:
            try:
                q.put_nowait(payload)
                count += 1
            except Exception:  # noqa: BLE001
                pass
        return count

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

    async def publish_run_card(self, thread_id: str, message_id: str, run_dict: dict[str, Any]) -> int:
        """Helper to broadcast a run card update."""
        return await self.publish(
            thread_id,
            "run_card",
            {"message_id": message_id, "run": run_dict},
        )


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
