"""Bind staff runs to DispatchQueueManager path-overlap sessions (#4272)."""

from __future__ import annotations

import logging

from dispatch_routing import QueuedDispatchItem, dispatch_queue_manager

log = logging.getLogger("dashboard.dispatch.queue_link")

_run_sessions: dict[str, str] = {}


def bind_run_session(run_id: str, session_id: str) -> None:
    """Associate an admitted staff run with its concurrency session."""
    if run_id and session_id:
        _run_sessions[run_id] = session_id


def release_run_session(run_id: str, outcome: str = "completed") -> list[QueuedDispatchItem]:
    """Release the overlap session for a staff run and return dequeued waiters."""
    session_id = _run_sessions.pop(run_id, None)
    if not session_id:
        return []
    dequeued = dispatch_queue_manager.release_session(session_id, outcome=outcome)
    log_dequeued(dequeued)
    return dequeued


def log_dequeued(dequeued: list[QueuedDispatchItem]) -> None:
    for item in dequeued:
        log.info(
            "dispatch queue dequeued repo=%s issue=%s queue_id=%s",
            item.repository,
            item.number,
            item.queue_id,
        )


def reset_dispatch_queue_link_state() -> None:
    """Reset overlap sessions and run bindings (pytest isolation only)."""
    _run_sessions.clear()
    dispatch_queue_manager.reset_state_for_tests()
