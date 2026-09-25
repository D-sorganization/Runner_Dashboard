"""Concurrency pool for staff conversational chat turns with Barb reservation (SC-C6)."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any

__all__ = [
    "DEFAULT_BARB_RESERVED_SLOTS",
    "DEFAULT_CHAT_ACQUIRE_TIMEOUT_SECONDS",
    "DEFAULT_MAX_CHAT_TURNS",
    "ChatConcurrencyPool",
    "get_chat_pool",
    "handle_capacity_exhausted",
    "reset_chat_pool",
]

DEFAULT_MAX_CHAT_TURNS = int(os.environ.get("STAFF_MAX_CHAT_TURNS", "4"))
DEFAULT_BARB_RESERVED_SLOTS = 1
DEFAULT_CHAT_ACQUIRE_TIMEOUT_SECONDS = float(os.environ.get("STAFF_CHAT_ACQUIRE_TIMEOUT_SECONDS", "1.0"))

log = logging.getLogger("dashboard.staff.chat_pool")


class ChatConcurrencyPool:
    """Bounded concurrency pool for chat turns with reserved slots for Barb (SC-C6)."""

    def __init__(
        self,
        max_concurrency: int = DEFAULT_MAX_CHAT_TURNS,
        barb_reserved: int = DEFAULT_BARB_RESERVED_SLOTS,
    ) -> None:
        self.max_concurrency = max(1, max_concurrency)
        self.barb_reserved = min(max(0, barb_reserved), self.max_concurrency)
        self._active: int = 0
        self._lock = threading.Lock()

    @property
    def active(self) -> int:
        """Return the number of currently active chat turns."""
        with self._lock:
            return self._active

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

    async def acquire(self, role: str, timeout: float = 0.0) -> bool:
        """Attempt to acquire a chat slot for ``role``, waiting up to ``timeout`` seconds if busy."""
        if self.try_acquire(role):
            return True
        if timeout <= 0.0:
            return False

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        poll_interval = 0.02
        while loop.time() < deadline:
            wait_time = min(poll_interval, max(0.001, deadline - loop.time()))
            await asyncio.sleep(wait_time)
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
        max_concurrency = int(os.environ.get("STAFF_MAX_CHAT_TURNS", str(DEFAULT_MAX_CHAT_TURNS)))
        _CHAT_POOL = ChatConcurrencyPool(max_concurrency=max_concurrency)
    return _CHAT_POOL


def reset_chat_pool() -> None:
    """Reset the global chat pool singleton (for tests)."""
    global _CHAT_POOL
    _CHAT_POOL = None


async def handle_capacity_exhausted(
    conv_store: Any,
    thread_id: str,
    user_message_id: str,
    placeholder_id: str,
    role_name: str,
) -> Any:
    """Handle chat pool capacity exhaustion: fail placeholder, post system busy notice, audit."""
    from staff.audit import record_audit
    from staff.chat import ChatTurnResult
    from staff.thread_bus import get_thread_bus

    if not thread_id or not thread_id.strip():
        raise ValueError("thread_id must be non-empty")
    if not placeholder_id or not placeholder_id.strip():
        raise ValueError("placeholder_id must be non-empty")
    if not role_name or not role_name.strip():
        raise ValueError("role_name must be non-empty")

    log.warning(
        "Chat pool capacity reached; all slots busy for role '%s' on thread %s",
        role_name,
        thread_id,
    )

    # 1. Mark reply placeholder failed with failure_class="chat_capacity"
    err_body = f"All chat slots are busy for role '{role_name}'. Please retry shortly."
    existing_msg = conv_store.get_message(placeholder_id)
    placeholder_meta = dict(existing_msg.meta) if existing_msg and existing_msg.meta else {}
    placeholder_meta.update(
        {
            "failure_class": "chat_capacity",
            "retryable": True,
            "actions": [{"name": "retry", "label": "Retry"}],
            "error": "All chat slots busy",
        }
    )
    conv_store.update_message(
        placeholder_id,
        kind="error",
        delivery="failed",
        body_md=err_body,
        meta=placeholder_meta,
    )

    bus = get_thread_bus()
    failed_msg = conv_store.get_message(placeholder_id)
    if failed_msg:
        try:
            await bus.publish_message(thread_id, failed_msg.to_dict())
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to publish failed reply message: %s", exc)

    # 2. Post system message: all chat slots busy, retry
    sys_body = "All chat slots are busy. Please retry shortly."
    sys_msg = conv_store.add_message(
        thread_id=thread_id,
        author_kind="system",
        author="system",
        kind="text",
        body_md=sys_body,
        meta={
            "in_reply_to": user_message_id,
            "failure_class": "chat_capacity",
            "retryable": True,
            "actions": [{"name": "retry", "label": "Retry"}],
        },
        delivery="complete",
    )
    try:
        await bus.publish_message(thread_id, sys_msg.to_dict())
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to publish system capacity message: %s", exc)

    # 3. Audit state change (SC-A8)
    try:
        audit_store = getattr(conv_store, "_audit_store", None)
        record_audit(
            action="chat_capacity",
            target=f"role:{role_name}",
            principal=role_name or "system",
            surface="thread",
            thread_id=thread_id,
            outcome="busy",
            detail={
                "role": role_name,
                "user_message_id": user_message_id,
                "placeholder_id": placeholder_id,
                "failure_class": "chat_capacity",
            },
            store=audit_store,
            fail_closed=False,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to record audit event for chat_capacity: %s", exc)

    return ChatTurnResult(
        ok=False,
        failure_class="chat_capacity",
        retryable=True,
        remediation="All chat slots are busy. Please retry shortly.",
        reply="All chat slots are busy. Please retry shortly.",
    )
