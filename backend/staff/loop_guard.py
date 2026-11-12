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
DEFAULT_MAX_HANDOFF_HOPS = 3


class HandoffHopCapExceededError(RuntimeError):
    """Raised when a conversation handoff exceeds the configured hop cap (#3548)."""


def principal_author_kind(caller: Any) -> str:
    """Return 'bot' when *caller* is a bot principal, else 'user' (#3548)."""
    return "bot" if str(getattr(caller, "type", "human")).lower() == "bot" else "user"


def _parse_cap(env_names: tuple[str, ...], default: int) -> int:
    for name in env_names:
        raw = os.environ.get(name)
        if raw is not None and raw.strip() != "":
            try:
                val = int(raw.strip())
            except ValueError:
                return 0
            return max(0, val)
    return default


class LoopGuard:
    """Detects and interrupts unattended multi-agent conversation and handoff loops."""

    def __init__(
        self,
        max_agent_turns: int | None = None,
        max_handoff_hops: int | None = None,
    ) -> None:
        self._configured_max_turns = max_agent_turns
        self._configured_max_hops = max_handoff_hops

    @property
    def max_agent_turns(self) -> int:
        if self._configured_max_turns is not None:
            return self._configured_max_turns
        return _parse_cap(("STAFF_LOOP_GUARD_TURNS",), DEFAULT_MAX_AGENT_TURNS)

    @property
    def max_handoff_hops(self) -> int:
        if self._configured_max_hops is not None:
            return self._configured_max_hops
        return _parse_cap(("STAFF_HANDOFF_HOP_CAP", "STAFF_MAX_HANDOFF_HOPS"), DEFAULT_MAX_HANDOFF_HOPS)

    def check(self, thread_id: str, store: Any) -> tuple[bool, int]:
        """Check if thread has exceeded max consecutive agent turns without human user input.

        Returns (tripped: bool, consecutive_agent_turns: int).
        """
        limit = self.max_agent_turns
        messages = store.list_messages(thread_id, limit=max(limit, 1) + 10)
        if not messages:
            return limit <= 0, 0

        sorted_messages = sorted(messages, key=lambda m: getattr(m, "seq", 0), reverse=True)

        consecutive_agent_turns = 0
        for msg in sorted_messages:
            if getattr(msg, "delivery", "") == "pending":
                continue
            author_kind = getattr(msg, "author_kind", "user")
            # Human user message resets the loop count; bot and role turns increment it
            if author_kind == "user":
                break
            if author_kind == "system":
                continue
            consecutive_agent_turns += 1

        tripped = consecutive_agent_turns >= limit
        return tripped, consecutive_agent_turns

    def count_handoff_hops(self, thread_id: str, store: Any) -> int:
        """Return cumulative handoff hops on *thread_id* since the last human user message."""
        if not thread_id:
            return 0
        messages = store.list_messages(thread_id, limit=50)
        if not messages:
            return 0
        sorted_messages = sorted(messages, key=lambda m: getattr(m, "seq", 0), reverse=True)
        max_recorded_hops = 0
        handoff_cards = 0
        for msg in sorted_messages:
            if getattr(msg, "author_kind", "user") == "user":
                break
            meta = getattr(msg, "meta", None) or {}
            recorded = int(meta.get("handoff_hops") or 0) if isinstance(meta, dict) else 0
            if recorded > max_recorded_hops:
                max_recorded_hops = recorded
            if getattr(msg, "kind", "") == "handoff":
                handoff_cards += 1
            elif isinstance(meta, dict) and meta.get("handoff") and max_recorded_hops == 0:
                max_recorded_hops = 1
        return max(max_recorded_hops, handoff_cards)

    def check_handoff_hops(self, thread_id: str, store: Any) -> tuple[bool, int]:
        """Return (tripped, current_hops) against ``max_handoff_hops``."""
        hops = self.count_handoff_hops(thread_id, store)
        return hops >= self.max_handoff_hops, hops

    def trip_handoff_cap(self, thread_id: str, store: Any, hop_count: int) -> None:
        """Pause thread, post system message, and record audit when handoff hop cap trips."""
        limit = self.max_handoff_hops
        log.warning("Handoff hop cap tripped on thread %s with %d hops (limit %d)", thread_id, hop_count, limit)
        if thread_id:
            try:
                store.add_message(
                    thread_id=thread_id,
                    author_kind="system",
                    author="loop-guard",
                    kind="text",
                    body_md=(
                        f"⚠️ Handoff hop cap triggered: Thread has reached {hop_count} consecutive handoffs "
                        f"(limit {limit}) without human user input. Further handoffs are halted."
                    ),
                    meta={"paused_by": "handoff_hop_cap", "hops": hop_count, "limit": limit},
                    delivery="complete",
                )
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to append handoff hop cap message to thread %s: %s", thread_id, exc)
        record_audit(
            action="handoff_hop_cap_tripped",
            target=f"thread:{thread_id or 'unknown'}",
            principal="system",
            surface="loop_guard",
            outcome="paused",
            detail={"hops": hop_count, "limit": limit},
            fail_closed=False,
        )

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


def enforce_handoff_hop_cap_or_raise(
    thread_id: str | None,
    store: Any,
    max_handoff_hops: int | None = None,
) -> int:
    """Validate *thread_id* is below the handoff hop cap and return the next hop count (1-based).

    Trips the guard and raises :class:`HandoffHopCapExceededError` (fail-closed) when the cap is reached.
    """
    guard = LoopGuard(max_handoff_hops=max_handoff_hops) if max_handoff_hops is not None else get_loop_guard()
    if not thread_id:
        if guard.max_handoff_hops <= 0:
            guard.trip_handoff_cap("", store, 0)
            raise HandoffHopCapExceededError(f"Handoff hop cap exceeded (0 >= {guard.max_handoff_hops})")
        return 1
    tripped, hops = guard.check_handoff_hops(thread_id, store)
    if tripped:
        guard.trip_handoff_cap(thread_id, store, hops)
        raise HandoffHopCapExceededError(
            f"Handoff hop cap exceeded on thread {thread_id} ({hops} >= {guard.max_handoff_hops})"
        )
    return hops + 1


def enforce_loop_guard_or_raise(
    thread_id: str,
    store: Any,
    principal_type: str = "bot",
    message_kind: str = "text",
) -> None:
    """Enforce loop guard for incoming bot message; trips and raises 429 if limit exceeded."""
    if message_kind == "handoff":
        try:
            enforce_handoff_hop_cap_or_raise(thread_id, store)
        except HandoffHopCapExceededError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "handoff_hop_cap_exceeded",
                    "message": str(exc),
                    "retryable": False,
                },
            ) from exc

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
