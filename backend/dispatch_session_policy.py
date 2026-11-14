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
