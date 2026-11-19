"""Repo-managed per-role staff dispatch rate limits (#4457, epic #4251)."""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger("dashboard.staff.dispatch_rate_policy")

_DEFAULT_DISPATCHES_PER_HOUR = 10.0
_CODE_REVIEWER_DISPATCHES_PER_HOUR = 30.0

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "staff_dispatch_rate_limits.json"


def _config_path() -> Path:
    override = os.environ.get("STAFF_DISPATCH_RATE_LIMITS_PATH", "").strip()
    return Path(override).expanduser() if override else _CONFIG_PATH


def _load_raw() -> dict[str, Any]:
    path = _config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("%s unreadable (%s); using built-in defaults", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def _policy() -> tuple[float, dict[str, float]]:
    raw = _load_raw()
    default = float(raw.get("default_dispatches_per_hour", _DEFAULT_DISPATCHES_PER_HOUR))
    roles_raw = raw.get("roles") or {}
    per_role: dict[str, float] = {}
    if isinstance(roles_raw, dict):
        for name, spec in roles_raw.items():
            if not isinstance(spec, dict):
                continue
            try:
                per_role[str(name)] = float(spec.get("dispatches_per_hour", default))
            except (TypeError, ValueError):
                log.warning("invalid dispatches_per_hour for role %s; skipping", name)
    if "code-reviewer" not in per_role:
        per_role["code-reviewer"] = _CODE_REVIEWER_DISPATCHES_PER_HOUR
    return default, per_role


def reload_dispatch_rate_policy() -> None:
    """Clear cached policy (tests and hot reload)."""
    _policy.cache_clear()


def dispatch_action_for_role(role: str | None) -> str:
    """Stable token-bucket action key isolating roles per principal."""
    token = (role or "").strip() or "_default"
    return f"dispatches:{token}"


def resolve_dispatch_bucket(role: str | None) -> tuple[float, float]:
    """Return ``(capacity, rate_per_sec)`` for ``role`` dispatch triggers."""
    default, per_role = _policy()
    key = (role or "").strip()
    per_hour = per_role.get(key, default) if key else default
    if per_hour <= 0:
        per_hour = default
    return per_hour, per_hour / 3600.0
