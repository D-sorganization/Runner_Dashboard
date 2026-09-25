"""Loop guard preventing infinite agent-to-agent turn recursion (SC-F7, Issue #1336).

Specifications:
- Detects threads with more than N consecutive agent-to-agent turns without a human user message.
- Default limit is 5 turns (configurable via STAFF_LOOP_GUARD_TURNS).
- When triggered, pauses the thread, emits a system alert asking the owner, and records an audit event.
- Bot message attempts while tripped return HTTP 429 / classified loop_guard_triggered error.
- Resets as soon as a human user sends a message.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import HTTPException, status
from staff.audit import record_audit

log = logging.getLogger("dashboard.staff.loop_guard")

DEFAULT_MAX_AGENT_TURNS = 5


class LoopGuard:
    """Detects and interrupts unattended multi-agent conversation loops."""

    def __init__(self, max_agent_turns: int | None = None) -> None:
        self.max_agent_turns = max_agent_turns or int(os.environ.get("STAFF_LOOP_GUARD_TURNS", DEFAULT_MAX_AGENT_TURNS))

    def check(self, thread_id: str, store: Any) -> tuple[bool, int]:
        """Check if thread has exceeded max consecutive agent turns without human user input.

        Returns (tripped: bool, consecutive_agent_turns: int).
        """
        # Fetch the latest messages in reverse order
        messages = store.list_messages(thread_id, limit=self.max_agent_turns + 10)
        if not messages:
            return False, 0

        # Sort by seq descending to walk backwards from newest message
        sorted_messages = sorted(messages, key=lambda m: getattr(m, "seq", 0), reverse=True)

        consecutive_agent_turns = 0
        for msg in sorted_messages:
            if getattr(msg, "delivery", "") == "pending":
                continue
            author_kind = getattr(msg, "author_kind", "user")
            # Human user message resets the loop count
            if author_kind == "user":
                break
            # Skip system notices when checking consecutive agent dialogue turns
            if author_kind == "system":
                continue
            consecutive_agent_turns += 1

        tripped = consecutive_agent_turns >= self.max_agent_turns
        return tripped, consecutive_agent_turns

    def trip(self, thread_id: str, store: Any, turn_count: int) -> None:
        """Pause thread, post system message asking the owner, and record audit event."""
        log.warning(
            "Loop guard tripped on thread %s with %d consecutive agent turns",
            thread_id,
            turn_count,
        )

        system_msg_body = (
            f"⚠️ Loop guard triggered: Thread has reached {turn_count} consecutive agent turns "
            "without human user input. Execution is paused to prevent runaway token spend. "
            "Please reply to confirm next steps or continue."
        )

        try:
            store.add_message(
                thread_id=thread_id,
                author_kind="system",
                author="loop-guard",
                kind="text",
                body_md=system_msg_body,
                meta={"paused_by": "loop_guard", "turns": turn_count},
                delivery="complete",
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to append loop guard system message to thread %s: %s", thread_id, exc)

        record_audit(
            action="loop_guard_tripped",
            target=f"thread:{thread_id}",
            principal="system",
            surface="loop_guard",
            outcome="paused",
            detail={"consecutive_turns": turn_count, "limit": self.max_agent_turns},
            fail_closed=False,
        )


_GLOBAL_LOOP_GUARD: LoopGuard | None = None


def get_loop_guard() -> LoopGuard:
    global _GLOBAL_LOOP_GUARD  # noqa: PLW0603
    if _GLOBAL_LOOP_GUARD is None:
        _GLOBAL_LOOP_GUARD = LoopGuard()
    return _GLOBAL_LOOP_GUARD


def check_loop_guard(thread_id: str, store: Any) -> tuple[bool, int]:
    """Convenience helper to check thread against global loop guard."""
    return get_loop_guard().check(thread_id, store)


def enforce_loop_guard_or_raise(
    thread_id: str,
    store: Any,
    principal_type: str = "bot",
) -> None:
    """Enforce loop guard for incoming bot message; trips and raises 429 if limit exceeded."""
    # Human user messages break the loop and are always allowed
    if principal_type.lower() == "human":
        return

    guard = get_loop_guard()
    tripped, count = guard.check(thread_id, store)
    if tripped:
        guard.trip(thread_id, store, count)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "loop_guard_triggered",
                "message": (
                    f"Loop guard active: Thread has reached {count} consecutive agent turns without user input. "
                    "Thread is paused waiting for owner response."
                ),
                "retryable": False,
            },
        )
