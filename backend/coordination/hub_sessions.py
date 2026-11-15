"""Hub-local session registry for cloud lanes and external agents (#4458, epic #2856)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

_lock = threading.RLock()
_sessions: dict[str, dict[str, Any]] = {}


def reset_hub_sessions() -> None:
    """Clear all hub-registered sessions (tests)."""
    with _lock:
        _sessions.clear()


def _parse_expires(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _is_expired(row: dict[str, Any], *, clock: datetime) -> bool:
    exp = _parse_expires(str(row.get("expires") or ""))
    return exp is not None and exp <= clock


def _prune(now: datetime | None = None) -> None:
    clock = now or datetime.now(UTC)
    expired = [sid for sid, row in _sessions.items() if _is_expired(row, clock=clock)]
    for sid in expired:
        _sessions.pop(sid, None)


def upsert(row: dict[str, Any]) -> dict[str, Any]:
    """Register or heartbeat one hub session (replaces prior row for the same session id)."""
    session = str(row.get("session") or "").strip()
    if not session:
        raise ValueError("session is required")
    stored = {**row, "session": session, "source": "hub"}
    with _lock:
        _prune()
        _sessions[session] = stored
        return dict(stored)


def list_sessions(repo: str | None = None) -> list[dict[str, Any]]:
    """Live hub sessions, optionally filtered by repo (case-insensitive)."""
    with _lock:
        _prune()
        rows = [dict(r) for r in _sessions.values()]
    if repo:
        wanted = repo.casefold()
        rows = [r for r in rows if str(r.get("repo") or "").casefold() == wanted]
    return rows