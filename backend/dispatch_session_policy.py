"""Role and TTL policy for in-memory dispatch overlap sessions."""

from __future__ import annotations

import os

from staff.role_spec import RoleSpec

DEFAULT_DISPATCH_SESSION_IDLE_TTL_SECONDS = 45 * 60


def session_idle_ttl_seconds() -> float:
    raw = os.environ.get("DISPATCH_SESSION_IDLE_TTL_SECONDS", "").strip()
    if raw:
        try:
            val = float(raw)
            if val > 0:
                return val
        except ValueError:
            pass
    return float(DEFAULT_DISPATCH_SESSION_IDLE_TTL_SECONDS)


def role_occupies_paths(role: RoleSpec | None) -> bool:
    """True when this role participates in path-overlap gating (write-capable by default)."""
    if role is None:
        return True
    return role.occupies_paths


DEFAULT_MAX_SESSIONS_PER_REPO = 3
MAX_SESSIONS_PER_REPO_ENV = "DISPATCH_MAX_SESSIONS_PER_REPO"
MAX_SESSIONS_PER_REPO_OVERRIDES_ENV = "DISPATCH_MAX_SESSIONS_PER_REPO_OVERRIDES"


def _positive_int(raw: str) -> int | None:
    try:
        val = int(raw.strip())
    except ValueError:
        return None
    return val if val > 0 else None


def _repo_key(repository: str) -> str:
    return repository.strip().lower().rsplit("/", 1)[-1]


def max_sessions_per_repo(repository: str) -> int:
    """Concurrent path-occupying dispatch sessions allowed in one repo (#4492).

    ``DISPATCH_MAX_SESSIONS_PER_REPO`` sets the default (3); ``DISPATCH_MAX_SESSIONS_PER_REPO_OVERRIDES``
    is a comma list of ``Repo=N`` (owner prefix optional). Invalid or non-positive values are ignored.
    """
    cap = _positive_int(os.environ.get(MAX_SESSIONS_PER_REPO_ENV, "")) or DEFAULT_MAX_SESSIONS_PER_REPO
    key = _repo_key(repository)
    for entry in os.environ.get(MAX_SESSIONS_PER_REPO_OVERRIDES_ENV, "").split(","):
        name, sep, value = entry.partition("=")
        if not sep or _repo_key(name) != key:
            continue
        override = _positive_int(value)
        if override is not None:
            cap = override
    return cap
