"""Session management utilities for mobile auth and remote logout.

Tracks active sessions per principal with metadata for remote session
invalidation and audit logging.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class SessionRecord(BaseModel):
    """Metadata for an authenticated dashboard session."""

    session_id: str
    principal_id: str
    created_at: float = Field(default_factory=time.time)
    last_seen_at: float = Field(default_factory=time.time)
    user_agent: str | None = None
    ip_address: str | None = None
    revoked_at: float | None = None


_SESSIONS_PATH = Path(
    os.environ.get("DASHBOARD_SESSIONS_PATH")
    or (Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "runner-dashboard" / "sessions.json")
)

_SESSION_TTL_SECONDS = int(os.environ.get("DASHBOARD_SESSION_TTL_SECONDS", "86400"))
_MAX_SESSIONS_PER_PRINCIPAL = int(os.environ.get("DASHBOARD_MAX_SESSIONS_PER_PRINCIPAL", "10"))


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
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [record.model_dump() for record in records]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _prune_expired_sessions(records: list[SessionRecord]) -> list[SessionRecord]:
    now = time.time()
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
    records = _load_sessions()
    records = _prune_expired_sessions(records)

    # Enforce max sessions per principal (FIFO eviction)
    principal_sessions = [r for r in records if r.principal_id == principal_id]
    if len(principal_sessions) >= _MAX_SESSIONS_PER_PRINCIPAL:
        # Sort by created_at ascending, remove oldest
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
    _save_sessions(records)
    return session_id


def touch_session(session_id: str) -> bool:
    """Update last_seen_at for a session. Returns True if session is active."""
    records = _load_sessions()
    records = _prune_expired_sessions(records)
    found = False
    for record in records:
        if record.session_id == session_id:
            record.last_seen_at = time.time()
            found = True
            break
    if found:
        _save_sessions(records)
    return found


def revoke_session(session_id: str) -> bool:
    """Revoke a single session by ID."""
    records = _load_sessions()
    changed = False
    now = time.time()
    for record in records:
        if record.session_id == session_id and record.revoked_at is None:
            record.revoked_at = now
            changed = True
    if changed:
        _save_sessions(records)
    return changed


def revoke_all_sessions_for_principal(principal_id: str, exclude_session_id: str | None = None) -> int:
    """Revoke all active sessions for a principal. Returns number revoked."""
    records = _load_sessions()
    changed = 0
    now = time.time()
    for record in records:
        if (
            record.principal_id == principal_id
            and record.revoked_at is None
            and (exclude_session_id is None or record.session_id != exclude_session_id)
        ):
            record.revoked_at = now
            changed += 1
    if changed:
        _save_sessions(records)
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
