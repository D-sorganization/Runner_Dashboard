"""Session management utilities for mobile auth and remote logout.

Tracks active sessions per principal with metadata for remote session
invalidation and audit logging.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from config_schema import atomic_write_json
from pydantic import BaseModel, Field


def _now() -> float:
    return time.time()


class SessionRecord(BaseModel):
    """Metadata for an authenticated dashboard session."""

    session_id: str
    principal_id: str
    created_at: float = Field(default_factory=_now)
    last_seen_at: float = Field(default_factory=_now)
    user_agent: str | None = None
    ip_address: str | None = None
    revoked_at: float | None = None


_SESSIONS_PATH = Path(
    os.environ.get("DASHBOARD_SESSIONS_PATH")
    or (Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "runner-dashboard" / "sessions.json")
)

_SESSION_TTL_SECONDS = int(os.environ.get("DASHBOARD_SESSION_TTL_SECONDS", "86400"))
_MAX_SESSIONS_PER_PRINCIPAL = int(os.environ.get("DASHBOARD_MAX_SESSIONS_PER_PRINCIPAL", "10"))


def _lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


@contextmanager
def _sessions_file_lock(path: Path) -> Iterator[None]:
    lock_path = _lock_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_sessions(path: Path | None = None) -> list[SessionRecord]:
    if path is None:
        path = _SESSIONS_PATH
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    records: list[SessionRecord] = []
    for item in raw:
        if isinstance(item, dict):
            try:
                records.append(SessionRecord(**item))
            except (TypeError, ValueError):
                continue
    return records


def _save_sessions(records: list[SessionRecord], path: Path | None = None) -> None:
    if path is None:
        path = _SESSIONS_PATH
    payload = [record.model_dump() for record in records]
    atomic_write_json(path, payload)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _prune_expired_sessions(records: list[SessionRecord]) -> list[SessionRecord]:
    now = _now()
    cutoff = now - _SESSION_TTL_SECONDS
    return [record for record in records if record.last_seen_at > cutoff and record.revoked_at is None]


def generate_session_id() -> str:
    """Generate a cryptographically secure session identifier."""
    return "sess_" + secrets.token_urlsafe(24)


def register_session(
    principal_id: str,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> str:
    """Register a new active session and return its session_id."""
    path = _SESSIONS_PATH
    with _sessions_file_lock(path):
        records = _load_sessions(path)
        records = _prune_expired_sessions(records)

        principal_sessions = [r for r in records if r.principal_id == principal_id]
        if len(principal_sessions) >= _MAX_SESSIONS_PER_PRINCIPAL:
            principal_sessions.sort(key=lambda r: r.created_at)
            to_revoke = principal_sessions[: len(principal_sessions) - _MAX_SESSIONS_PER_PRINCIPAL + 1]
            revoke_ids = {r.session_id for r in to_revoke}
            records = [r for r in records if r.session_id not in revoke_ids]

        session_id = generate_session_id()
        record = SessionRecord(
            session_id=session_id,
            principal_id=principal_id,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        records.append(record)
        _save_sessions(records, path)
        return session_id


def touch_session(session_id: str) -> bool:
    """Update last_seen_at for a session. Returns True if session is active."""
    path = _SESSIONS_PATH
    with _sessions_file_lock(path):
        records = _load_sessions(path)
        for record in records:
            if record.session_id != session_id:
                continue
            if record.revoked_at is not None:
                return False
            if record.last_seen_at <= _now() - _SESSION_TTL_SECONDS:
                return False
            record.last_seen_at = _now()
            _save_sessions(records, path)
            return True
        return False


def revoke_session(session_id: str) -> bool:
    """Revoke a single session by ID."""
    path = _SESSIONS_PATH
    with _sessions_file_lock(path):
        records = _load_sessions(path)
        changed = False
        now = _now()
        for record in records:
            if record.session_id == session_id and record.revoked_at is None:
                record.revoked_at = now
                changed = True
        if changed:
            _save_sessions(records, path)
        return changed


def revoke_all_sessions_for_principal(principal_id: str, exclude_session_id: str | None = None) -> int:
    """Revoke all active sessions for a principal. Returns number revoked."""
    path = _SESSIONS_PATH
    with _sessions_file_lock(path):
        records = _load_sessions(path)
        changed = 0
        now = _now()
        for record in records:
            if (
                record.principal_id == principal_id
                and record.revoked_at is None
                and (exclude_session_id is None or record.session_id != exclude_session_id)
            ):
                record.revoked_at = now
                changed += 1
        if changed:
            _save_sessions(records, path)
        return changed


def list_sessions_for_principal(principal_id: str) -> list[dict[str, Any]]:
    """Return active session metadata for a principal."""
    records = _load_sessions()
    records = _prune_expired_sessions(records)
    return [
        {
            "session_id": r.session_id,
            "created_at": r.created_at,
            "last_seen_at": r.last_seen_at,
            "user_agent": r.user_agent,
            "ip_address": r.ip_address,
        }
        for r in records
        if r.principal_id == principal_id
    ]


def is_session_active(session_id: str) -> bool:
    """Check whether a session is currently active."""
    records = _load_sessions()
    records = _prune_expired_sessions(records)
    for record in records:
        if record.session_id == session_id:
            return True
    return False


def session_count_for_principal(principal_id: str) -> int:
    """Return the number of active sessions for a principal."""
    return len(list_sessions_for_principal(principal_id))


def hash_session_id(session_id: str) -> str:
    """Return a stable hash of a session id for logging (avoids leaking raw ids)."""
    return base64.urlsafe_b64encode(hashlib.sha256(session_id.encode("utf-8")).digest()).decode("ascii").rstrip("=")
