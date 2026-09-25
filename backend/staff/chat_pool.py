"""Concurrency pool for staff conversational chat turns with Barb reservation (SC-C6)."""

from __future__ import annotations

import asyncio
import os
import threading

__all__ = [
    "DEFAULT_BARB_RESERVED_SLOTS",
    "DEFAULT_CHAT_ACQUIRE_TIMEOUT",
    "DEFAULT_MAX_CHAT_TURNS",
    "ChatConcurrencyPool",
    "get_chat_pool",
]

DEFAULT_MAX_CHAT_TURNS = int(os.environ.get("STAFF_MAX_CHAT_TURNS", "4"))
DEFAULT_BARB_RESERVED_SLOTS = 1
DEFAULT_CHAT_ACQUIRE_TIMEOUT = float(os.environ.get("STAFF_CHAT_ACQUIRE_TIMEOUT", "0.5"))


class ChatConcurrencyPool:
    """Bounded concurrency pool for chat turns with reserved slots for Barb (SC-C6)."""

    def __init__(
        self,
        max_concurrency: int = DEFAULT_MAX_CHAT_TURNS,
        barb_reserved: int = DEFAULT_BARB_RESERVED_SLOTS,
    ) -> None:
        self.max_concurrency = max(1, max_concurrency)
        self.barb_reserved = min(max(0, barb_reserved), self.max_concurrency - 1)
        self._active: int = 0
        self._lock = threading.Lock()

    def try_acquire(self, role: str) -> bool:
        """Attempt to acquire a chat slot for ``role``.

        Barb can use any slot up to ``max_concurrency``.
        Other roles can only acquire if active < (max_concurrency - barb_reserved).
        """
        with self._lock:
            if role.lower() == "barb":
                if self._active < self.max_concurrency:
                    self._active += 1
                    return True
                return False

            if self._active < (self.max_concurrency - self.barb_reserved):
                self._active += 1
                return True
            return False

    async def acquire(self, role: str, timeout: float = DEFAULT_CHAT_ACQUIRE_TIMEOUT) -> bool:
        """Attempt to acquire a chat slot for ``role``, waiting up to ``timeout`` seconds."""
        if self.try_acquire(role):
            return True
        if timeout <= 0:
            return False
        deadline = asyncio.get_running_loop().time() + timeout
        step = min(0.05, max(0.01, timeout / 10))
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(step)
            if self.try_acquire(role):
                return True
        return False

    def release(self, role: str) -> None:
        """Release an acquired chat slot."""
        with self._lock:
            if self._active > 0:
                self._active -= 1


_CHAT_POOL: ChatConcurrencyPool | None = None


def get_chat_pool() -> ChatConcurrencyPool:
    global _CHAT_POOL
    if _CHAT_POOL is None:
        _CHAT_POOL = ChatConcurrencyPool()
    return _CHAT_POOL
