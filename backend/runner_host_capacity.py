"""Per-host runner capacity caps and schedule seed drift (#4307)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_DIR = _REPO_ROOT / "config"

# Agreed fleet caps (panel th_ce37073f1b17): hub and OG ≤6, control node ≤8.
_HOST_HARD_CAPS: dict[str, int] = {
    "huba": 6,
    "deskcomputer": 6,
    "desk": 6,
    "nodeb": 6,
    "oglaptop": 6,
    "og": 6,
    "nodea": 8,
    "controltower": 8,
}

_SCHEDULE_SEED_BY_HOST: dict[str, str] = {
    "huba": "runner-schedule.json",
    "deskcomputer": "runner-schedule.json",
    "desk": "runner-schedule.json",
    "nodeb": "runner-schedule-oglaptop.json",
    "oglaptop": "runner-schedule-oglaptop.json",
    "og": "runner-schedule-oglaptop.json",
    "nodea": "runner-schedule-node-b.json",
    "controltower": "runner-schedule-node-b.json",
}

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _normalize_host_token(value: str) -> str:
    return _TOKEN_SPLIT.sub("", value.strip().lower())


RESOURCE_FLOOR_NUMERIC_KEYS = (
    "min_disk_free_gb",
    "readmit_disk_free_gb",
    "min_mem_available_gb",
    "max_memory_pressure_avg60",
    "runners",
)


def validate_resource_floor(floor: object) -> None:
    """Mirror of ``deploy/runner-scheduler.py`` ``validate_resource_floor`` (#2000, #4307)."""
    if floor is None:
        return
    if not isinstance(floor, dict):
        raise ValueError("resource_floor must be an object")
    for key in RESOURCE_FLOOR_NUMERIC_KEYS:
        if key not in floor:
            continue
        value = floor[key]
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            raise ValueError(f"resource_floor.{key} must be a nonnegative number")
    if "disk_path" in floor and not isinstance(floor["disk_path"], str):
        raise ValueError("resource_floor.disk_path must be a string")
    if "readmit_disk_free_gb" in floor and floor["readmit_disk_free_gb"] < floor.get("min_disk_free_gb", 0):
        raise ValueError("resource_floor.readmit_disk_free_gb must be >= min_disk_free_gb")


def _host_tokens(hostname: str, aliases: list[str] | None = None) -> list[str]:
    tokens = [_normalize_host_token(hostname)]
    for alias in aliases or []:
        norm = _normalize_host_token(alias)
        if norm and norm not in tokens:
            tokens.append(norm)
    return tokens


def _lookup_by_tokens(mapping: dict[str, Any], hostname: str, aliases: list[str] | None) -> Any | None:
    for token in _host_tokens(hostname, aliases):
        if token in mapping:
            return mapping[token]
    return None


def host_runner_hard_cap(hostname: str, aliases: list[str] | None = None) -> int | None:
    """Return the fleet-agreed runner ceiling for this host, if known."""
    return _lookup_by_tokens(_HOST_HARD_CAPS, hostname, aliases)


def schedule_seed_path(hostname: str, aliases: list[str] | None = None) -> Path | None:
    """Repo-relative canonical schedule JSON for this host (reference for drift)."""
    seed_name = _lookup_by_tokens(_SCHEDULE_SEED_BY_HOST, hostname, aliases)
    if not seed_name:
        return None
    path = _CONFIG_DIR / seed_name
    return path if path.is_file() else None


def schedule_capacity_ceiling(hostname: str, aliases: list[str] | None, host_runner_limit: int) -> int:
    """Effective max runners for schedule validation (cap wins over NUM_RUNNERS install limit)."""
    hard = host_runner_hard_cap(hostname, aliases)
    if hard is not None:
        return hard
    return max(0, host_runner_limit)


def _schedule_slice_for_drift(config: dict[str, Any]) -> dict[str, Any]:
    """Fields compared between live schedule JSON and the repo seed."""
    out: dict[str, Any] = {
        "enabled": bool(config.get("enabled", True)),
        "default_count": int(config.get("default_count", 0)),
        "max_count": int(config.get("max_count", 0)),
        "schedules": [],
    }
    floor = config.get("resource_floor")
    if isinstance(floor, dict):
        out["resource_floor"] = floor
    for entry in config.get("schedules", []):
        if not isinstance(entry, dict):
            continue
        out["schedules"].append(
            {
                "name": str(entry.get("name") or ""),
                "days": [str(day).lower() for day in entry.get("days", [])],
                "start": str(entry.get("start") or ""),
                "end": str(entry.get("end") or ""),
                "runners": int(entry.get("runners", 0)),
            }
        )
    return out


def schedule_config_drift(
    live_config: dict[str, Any],
    *,
    hostname: str,
    aliases: list[str] | None,
    live_path: Path | None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Compare live schedule JSON to the repo seed for this host."""
    seed_path = schedule_seed_path(hostname, aliases)
    if seed_path is None:
        return {"checked": False, "reason": "no canonical seed for host"}
    try:
        seed_raw = json.loads(seed_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"checked": False, "reason": f"seed unreadable: {exc}", "seed_path": str(seed_path)}

    if not isinstance(seed_raw, dict):
        return {"checked": False, "reason": "seed is not an object", "seed_path": str(seed_path)}

    live_slice = _schedule_slice_for_drift(live_config)
    seed_slice = _schedule_slice_for_drift(seed_raw)
    drifted = live_slice != seed_slice
    payload: dict[str, Any] = {
        "checked": True,
        "drifted": drifted,
        "seed_path": str(seed_path),
        "live_path": str(live_path) if live_path else None,
        "authoritative": "live",
    }
    if drifted:
        payload["live_max_count"] = live_slice.get("max_count")
        payload["seed_max_count"] = seed_slice.get("max_count")
        if live_slice.get("max_count") != seed_slice.get("max_count"):
            payload["detail"] = (
                f"live max_count={live_slice.get('max_count')} "
                f"differs from seed max_count={seed_slice.get('max_count')}"
            )
        else:
            payload["detail"] = "live schedule JSON differs from repo seed (non-max_count fields)"
    return payload
