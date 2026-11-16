"""Bind staff runs to DispatchQueueManager path-overlap sessions (#4272)."""

from __future__ import annotations

import logging

from dispatch_routing import QueuedDispatchItem, dispatch_queue_manager

log = logging.getLogger("dashboard.dispatch.queue_link")

_run_sessions: dict[str, str] = {}


def _run_is_live(run_id: str) -> bool | None:
    try:
        from staff.store import ACTIVE_STATUSES, get_store  # noqa: PLC0415

        rec = get_store().get_run(run_id)
    except Exception:
        return None
    if rec is None:
        return None
    return rec.status in ACTIVE_STATUSES


def bind_run_session(run_id: str, session_id: str) -> None:
    """Associate an admitted staff run with its concurrency session."""
    if run_id and session_id:
        _run_sessions[run_id] = session_id
        dispatch_queue_manager.bind_run(session_id, run_id)


def touch_run_session(run_id: str) -> None:
    """Refresh overlap session activity from run heartbeats or status changes."""
    session_id = _run_sessions.get(run_id)
    if session_id:
        dispatch_queue_manager.touch_session(session_id)


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


def release_unbound_overlap_session(session_id: str | None, *, outcome: str, bound_run_id: str | None = None) -> None:
    """Release a path-overlap session that never bound to a staff run."""
    if session_id and not bound_run_id:
        dispatch_queue_manager.release_session(session_id, outcome=outcome)


def purge_stale_overlap_sessions() -> list[tuple[str, str]]:
    """Expire idle or terminal-bound overlap sessions before admission checks."""
    return dispatch_queue_manager.expire_stale_sessions(run_live=_run_is_live)


def run_id_for_session(session_id: str) -> str | None:
    for run_id, sid in _run_sessions.items():
        if sid == session_id:
            return run_id
    session = dispatch_queue_manager._active_sessions.get(session_id)
    return session.run_id if session else None
