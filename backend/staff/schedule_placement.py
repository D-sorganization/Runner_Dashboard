"""Per-role schedule home host, registry enablement, and portable-node guards (#3901)."""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from machine_registry import _normalize_token, build_machine_registry_index, is_portable_machine
from staff.role_spec import RoleSpec
from staff.store import _config_dir

log = logging.getLogger("dashboard.staff.schedule_placement")

PLACEMENTS_FILE = "staff_schedule_placements.json"
PLACEMENTS_PATH_ENV = "STAFF_SCHEDULE_PLACEMENTS_FILE"
_persist_lock = threading.RLock()


def placements_path() -> Path:
    override = os.environ.get(PLACEMENTS_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return _config_dir() / PLACEMENTS_FILE


def load_placements(path: Path | None = None) -> dict[str, str]:
    p = path or placements_path()
    with _persist_lock:
        if not p.exists():
            return {}
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("schedule placements unreadable (%s)", exc.__class__.__name__)
            return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and key.strip() and isinstance(value, str) and value.strip():
            out[key.strip()] = value.strip()
    return out


def _registry_entry(machine: str, registry: dict[str, Any]) -> dict[str, Any] | None:
    return build_machine_registry_index(registry).get(_normalize_token(machine))


def _roles_enabled_set(entry: dict[str, Any] | None) -> frozenset[str] | None:
    if entry is None:
        return None
    staff = entry.get("staff")
    if not isinstance(staff, dict):
        return None
    raw = staff.get("roles_enabled")
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(x, str) and x for x in raw):
        raise ValueError("machine registry staff.roles_enabled must be a list of non-empty strings")
    return frozenset(str(x).strip() for x in raw)


def role_schedule_allowed_on_machine(role: str, machine: str, registry: dict[str, Any]) -> bool:
    if is_portable_machine(machine, registry):
        return False
    entry = _registry_entry(machine, registry)
    enabled = _roles_enabled_set(entry)
    if enabled is None:
        return True
    return "*" in enabled or role in enabled


def validate_schedule_host(
    role: str,
    host: str,
    registry: dict[str, Any],
    role_spec: RoleSpec | None,
) -> None:
    if role_spec is not None and role_spec.retired:
        raise ValueError(f"role '{role}' is retired")
    if not role_schedule_allowed_on_machine(role, host, registry):
        raise ValueError(f"role '{role}' is not enabled for scheduled runs on host '{host}'")


def schedule_host_for_role(role: str, placements: Mapping[str, str] | None, local_machine: str) -> str:
    if placements and role in placements:
        return placements[role]
    return local_machine


def schedule_host_skip_reason(
    role: str,
    local_machine: str,
    placements: Mapping[str, str],
    registry: dict[str, Any],
) -> str:
    home = schedule_host_for_role(role, placements, local_machine)
    if home != local_machine:
        return f"schedule home is {home}"
    if not role_schedule_allowed_on_machine(role, local_machine, registry):
        return f"role not enabled on {local_machine}"
    return ""


def schedule_inventory(
    local_machine: str,
    roles: Mapping[str, RoleSpec],
    *,
    placements: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    placement_map = placements if placements is not None else load_placements()
    rows: list[dict[str, Any]] = []
    for role in sorted(roles.values(), key=lambda r: r.name):
        if not role.schedule and not (isinstance(role.scope, dict) and role.scope.get("schedules")):
            continue
        cron = role.schedule or ""
        rows.append(
            {
                "role": role.name,
                "cron": cron,
                "host": schedule_host_for_role(role.name, placement_map, local_machine),
            }
        )
    return rows
