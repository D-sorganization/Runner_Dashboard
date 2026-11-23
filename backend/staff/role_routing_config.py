"""Reference role repository and schedule-home routing (#4582).

Shipped JSON under ``config/`` is the fleet policy source of truth for:
- Repository scope overlays (e.g. pm-sidekick → Tools while RM may lag).
- Schedule placement homes after offloading overloaded Brick (node-c).

Provider chains in ``staff_role_provider_chains.json`` are intentionally not edited here.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger("dashboard.staff.role_routing_config")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROUTING_PATH = _REPO_ROOT / "config" / "staff_role_repo_routing.reference.json"
_SCHEDULE_PLACEMENTS_PATH = _REPO_ROOT / "config" / "staff_schedule_placements.reference.json"

BRICK_HOST = "node-c"
_DESK_FALLBACK_DEFAULT = "hub-a"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("%s unreadable (%s)", path.name, exc.__class__.__name__)
        return {}
    return raw if isinstance(raw, dict) else {}


@lru_cache(maxsize=1)
def _repo_routing_raw() -> dict[str, Any]:
    return _read_json(_REPO_ROUTING_PATH)


@lru_cache(maxsize=1)
def _schedule_reference_raw() -> dict[str, Any]:
    return _read_json(_SCHEDULE_PLACEMENTS_PATH)


def reset_role_routing_config_cache() -> None:
    _repo_routing_raw.cache_clear()
    _schedule_reference_raw.cache_clear()


def load_reference_repo_routing() -> dict[str, Any]:
    return dict(_repo_routing_raw())


def load_reference_schedule_placements() -> dict[str, str]:
    raw = _schedule_reference_raw()
    placements = raw.get("placements") or {}
    if not isinstance(placements, dict):
        return {}
    return {str(k).strip(): str(v).strip() for k, v in placements.items() if str(k).strip() and str(v).strip()}


def desk_fallback_host() -> str:
    raw = _schedule_reference_raw().get("desk_fallback_host", _DESK_FALLBACK_DEFAULT)
    return str(raw).strip() or _DESK_FALLBACK_DEFAULT


def brick_offload_roles() -> tuple[str, ...]:
    raw = _schedule_reference_raw().get("brick_offload_roles") or ()
    if not isinstance(raw, list):
        return ()
    return tuple(str(r).strip() for r in raw if str(r).strip())


def worker_schedule_hosts() -> frozenset[str]:
    raw = _schedule_reference_raw().get("worker_schedule_hosts") or ()
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(h).strip() for h in raw if str(h).strip())


def brick_offload_migration_targets() -> tuple[str, ...]:
    """Preferred migration order: ControlTower and OGLaptop before Desk fallback."""
    desk = desk_fallback_host()
    return ("node-a", "node-b", desk)


def repo_overlay_for_role(role_name: str) -> list[str] | None:
    roles = _repo_routing_raw().get("roles") or {}
    if not isinstance(roles, dict):
        return None
    entry = roles.get((role_name or "").strip().lower())
    if not isinstance(entry, dict):
        return None
    raw_repos = entry.get("repos")
    if not isinstance(raw_repos, list):
        return None
    repos = [str(r).strip() for r in raw_repos if str(r).strip()]
    return repos or None


def enrich_role_repo_routing(data: dict[str, Any]) -> dict[str, Any]:
    """Copy-on-write: apply reference repo scope when configured for this role."""
    if not isinstance(data, dict):
        return data
    name = str(data.get("name") or "").strip().lower()
    overlay = repo_overlay_for_role(name)
    if overlay is None:
        return data
    out = dict(data)
    out["repos"] = list(overlay)
    return out


def validate_fleet_routing_policy() -> list[str]:
    """Return human-readable problems when reference configs violate #4582 policy."""
    problems: list[str] = []
    placements = load_reference_schedule_placements()
    desk = desk_fallback_host()
    brick = str(_schedule_reference_raw().get("brick_host") or BRICK_HOST).strip() or BRICK_HOST
    workers = worker_schedule_hosts()

    for role in brick_offload_roles():
        host = placements.get(role)
        if not host:
            problems.append(f"reference placements missing schedule home for '{role}'")
            continue
        if host == brick:
            problems.append(f"role '{role}' must not schedule on brick host '{brick}'")
        if host == desk:
            problems.append(f"role '{role}' must not use desk fallback '{desk}' as primary schedule home")
        if workers and host not in workers:
            problems.append(f"role '{role}' schedule home '{host}' is not in worker_schedule_hosts")

    pm_repos = repo_overlay_for_role("pm-sidekick")
    if pm_repos != ["Tools"]:
        problems.append(f"pm-sidekick reference repos must be ['Tools'], got {pm_repos!r}")

    return problems
