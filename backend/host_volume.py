"""Host volume disk guard and metrics collection (Issue #1168).

WSL2 and guest distros cannot see the host disk volume into which their backing
VHDX expands. When a host volume runs out of free space mid-write, the distro's
filesystem and package databases are corrupted (near-miss 2026-09-05 on
ControlTower F:; prior total pool losses on NVMe and SSD).

This module:
1. Identifies the host backing volume (`runner_backing_drive`) per host/pool from
   the machine registry or configuration.
2. Probes the host volume capacity (total bytes, used bytes, free bytes, and free %).
3. Enforces a hard floor alarm (default: < 5% or < 30 GB free) that marks the host
   volume as critical and flags scheduling inhibition so new jobs are not assigned
   to a host whose VHDX would expand into disk exhaustion.
4. Exposes `host_volume` distinctly from guest distro-root disk metrics so they
   are never conflated.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("dashboard.host_volume")

# Default hard floor thresholds (Issue #1168)
DEFAULT_HOST_VOLUME_MIN_FREE_GB: float = 30.0
DEFAULT_HOST_VOLUME_MIN_FREE_PERCENT: float = 5.0
DEFAULT_HOST_VOLUME_WARN_FREE_GB: float = 50.0
DEFAULT_HOST_VOLUME_WARN_FREE_PERCENT: float = 10.0


def normalize_drive_letter(drive: str) -> str:
    """Normalize a drive identifier to canonical 'X:' format.

    Supports inputs like 'F:', 'f:', 'F', 'F:\\', '/mnt/f'.
    """
    if not drive:
        return "C:"

    cleaned = drive.strip()
    if cleaned.lower().startswith("/mnt/") and len(cleaned) >= 6:
        letter = cleaned[5].upper()
        if letter.isalpha():
            return f"{letter}:"

    # Windows-style drive: take the first alphabetic character
    for ch in cleaned:
        if ch.isalpha():
            return f"{ch.upper()}:"

    return "C:"


def resolve_runner_backing_drive(
    machine_name: str | None = None,
    pool_name: str | None = None,
    registry: dict[str, Any] | None = None,
) -> str:
    """Resolve the host volume drive letter for a machine or pool.

    Lookup order:
    1. RUNNER_BACKING_DRIVE environment variable.
    2. Explicit storage.runner_backing_drive in the machine registry for the
       matching pool or machine.
    3. Explicit storage.host_drive in the machine registry.
    4. Drive letter from storage.windows_host_path or storage.vhdx_path.
    5. RUNNER_WINDOWS_HOST_PATH environment variable.
    6. Safe fallback: 'C:'.
    """
    env_drive = os.environ.get("RUNNER_BACKING_DRIVE")
    if env_drive:
        return normalize_drive_letter(env_drive)

    if registry is None:
        try:
            from machine_registry import load_machine_registry

            registry = load_machine_registry()
        except Exception as exc:  # noqa: BLE001 - safe fallback
            log.debug("Failed to load machine registry for drive resolution: %s", exc)
            registry = {}

    machines = registry.get("machines", []) if isinstance(registry, dict) else []

    def _extract_drive(storage_dict: dict[str, Any] | None) -> str | None:
        if not isinstance(storage_dict, dict):
            return None
        candidate = storage_dict.get("runner_backing_drive") or storage_dict.get("host_drive")
        if candidate and isinstance(candidate, str):
            return normalize_drive_letter(candidate)
        win_path = storage_dict.get("windows_host_path") or storage_dict.get("vhdx_path")
        if win_path and isinstance(win_path, str) and len(win_path) >= 2 and win_path[1] == ":":
            return normalize_drive_letter(win_path[:2])
        return None

    # Search for matching pool name first
    if pool_name:
        norm_pool = pool_name.strip().lower()
        for machine in machines:
            for pool in machine.get("runner_pools", []):
                pname = str(pool.get("name", "")).strip().lower()
                aliases = [str(a).strip().lower() for a in pool.get("aliases", [])]
                if norm_pool == pname or norm_pool in aliases:
                    found = _extract_drive(pool.get("storage"))
                    if found:
                        return found
                    # Fallback to parent machine storage
                    found_parent = _extract_drive(machine.get("storage"))
                    if found_parent:
                        return found_parent

    # Search for matching machine name
    if machine_name:
        norm_mach = machine_name.strip().lower()
        for machine in machines:
            mname = str(machine.get("name", "")).strip().lower()
            aliases = [str(a).strip().lower() for a in machine.get("aliases", [])]
            if norm_mach == mname or norm_mach in aliases:
                found = _extract_drive(machine.get("storage"))
                if found:
                    return found
                # Check machine's pools if top-level storage is empty
                for pool in machine.get("runner_pools", []):
                    found_pool = _extract_drive(pool.get("storage"))
                    if found_pool:
                        return found_pool

    # Check hostname if machine_name was not provided
    from dashboard_config import HOSTNAME

    norm_host = HOSTNAME.strip().lower()
    for machine in machines:
        mname = str(machine.get("name", "")).strip().lower()
        aliases = [str(a).strip().lower() for a in machine.get("aliases", [])]
        if norm_host == mname or norm_host in aliases:
            found = _extract_drive(machine.get("storage"))
            if found:
                return found
        for pool in machine.get("runner_pools", []):
            pname = str(pool.get("name", "")).strip().lower()
            paliases = [str(a).strip().lower() for a in pool.get("aliases", [])]
            if norm_host == pname or norm_host in paliases:
                found = _extract_drive(pool.get("storage"))
                if found:
                    return found

    # Fallback to RUNNER_WINDOWS_HOST_PATH
    win_host_path = os.environ.get("RUNNER_WINDOWS_HOST_PATH", "").strip()
    if len(win_host_path) >= 2 and win_host_path[1] == ":":
        return normalize_drive_letter(win_host_path[:2])

    return "C:"


def evaluate_host_volume_alarm(
    total_bytes: int,
    free_bytes: int,
    drive: str = "C:",
    min_free_percent: float | None = None,
    min_free_gb: float | None = None,
) -> dict[str, Any]:
    """Evaluate hard floor alarms and return host volume health metrics.

    Design by Contract:
    - Precondition: total_bytes >= 0, free_bytes >= 0.
    - If free_percent < min_free_percent or free_gb < min_free_gb, status MUST be
      'critical', hard_floor_hit MUST be True, and scheduling_inhibited MUST be True.
    """
    assert total_bytes >= 0, "total_bytes must be non-negative"
    assert free_bytes >= 0, "free_bytes must be non-negative"

    effective_min_gb = (
        min_free_gb
        if min_free_gb is not None
        else float(
            os.environ.get(
                "DASHBOARD_HOST_VOLUME_MIN_FREE_GB",
                str(DEFAULT_HOST_VOLUME_MIN_FREE_GB),
            )
        )
    )
    effective_min_pct = (
        min_free_percent
        if min_free_percent is not None
        else float(
            os.environ.get(
                "DASHBOARD_HOST_VOLUME_MIN_FREE_PERCENT",
                str(DEFAULT_HOST_VOLUME_MIN_FREE_PERCENT),
            )
        )
    )

    used_bytes = max(0, total_bytes - free_bytes)
    total_gb = round(total_bytes / (1024**3), 2)
    used_gb = round(used_bytes / (1024**3), 2)
    free_gb = round(free_bytes / (1024**3), 2)
    percent = round((used_bytes / total_bytes) * 100, 1) if total_bytes > 0 else 0.0
    free_percent = round((free_bytes / total_bytes) * 100, 1) if total_bytes > 0 else 0.0

    status = "healthy"
    scheduling_inhibited = False
    hard_floor_hit = False
    reasons: list[str] = []

    # Hard floor alarm evaluation
    if total_bytes == 0:
        status = "critical"
        scheduling_inhibited = True
        hard_floor_hit = True
        reasons.append(f"Host volume {drive} has zero capacity or is unreadable")
    elif free_percent < effective_min_pct or free_gb < effective_min_gb:
        status = "critical"
        scheduling_inhibited = True
        hard_floor_hit = True
        if free_percent < effective_min_pct:
            reasons.append(
                f"Host volume {drive} free space {free_percent:.1f}% is below hard floor {effective_min_pct:.1f}%"
            )
        if free_gb < effective_min_gb:
            reasons.append(
                f"Host volume {drive} free space {free_gb:.1f} GB is below hard floor {effective_min_gb:.1f} GB"
            )
        reasons.append("Scheduling inhibited to prevent host volume exhaustion and vhdx corruption (Issue #1168)")
    elif free_percent < DEFAULT_HOST_VOLUME_WARN_FREE_PERCENT or free_gb < DEFAULT_HOST_VOLUME_WARN_FREE_GB:
        status = "warning"
        if free_percent < DEFAULT_HOST_VOLUME_WARN_FREE_PERCENT:
            reasons.append(
                f"Host volume {drive} free space {free_percent:.1f}% is approaching low threshold "
                f"{DEFAULT_HOST_VOLUME_WARN_FREE_PERCENT:.1f}%"
            )
        if free_gb < DEFAULT_HOST_VOLUME_WARN_FREE_GB:
            reasons.append(
                f"Host volume {drive} free space {free_gb:.1f} GB is approaching low threshold "
                f"{DEFAULT_HOST_VOLUME_WARN_FREE_GB:.1f} GB"
            )

    return {
        "drive": drive,
        "total_bytes": total_bytes,
        "used_bytes": used_bytes,
        "free_bytes": free_bytes,
        "total_gb": total_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "percent": percent,
        "free_percent": free_percent,
        "min_free_gb_floor": effective_min_gb,
        "min_free_percent_floor": effective_min_pct,
        "status": status,
        "scheduling_inhibited": scheduling_inhibited,
        "hard_floor_hit": hard_floor_hit,
        "reasons": reasons,
    }


def _resolve_probe_path(drive: str) -> str:
    """Resolve the filesystem path to probe for the host volume."""
    norm_drive = normalize_drive_letter(drive)
    letter = norm_drive[0].upper()

    if os.name == "nt":
        return f"{letter}:\\"

    # WSL environment: Windows drives are mapped to /mnt/<letter>
    wsl_mnt = Path(f"/mnt/{letter.lower()}")
    if wsl_mnt.exists():
        return str(wsl_mnt)

    # Fallback to root or current path on pure Linux
    return "/"


def probe_host_volume(
    drive: str | None = None,
    disk_usage_fn: Callable[[str], Any] = shutil.disk_usage,
    min_free_percent: float | None = None,
    min_free_gb: float | None = None,
) -> dict[str, Any]:
    """Probe host volume disk usage and evaluate hard floor alarms.

    DbC: Never raises uncaught exceptions into the callers; degrades gracefully
    to critical state with informative diagnostics if disk probing fails.
    """
    resolved_drive = normalize_drive_letter(drive) if drive else resolve_runner_backing_drive()
    measure_path = _resolve_probe_path(resolved_drive)

    try:
        du = disk_usage_fn(measure_path)
        metrics = evaluate_host_volume_alarm(
            total_bytes=int(du.total),
            free_bytes=int(du.free),
            drive=resolved_drive,
            min_free_percent=min_free_percent,
            min_free_gb=min_free_gb,
        )
        metrics["path"] = measure_path
        metrics["measured_at"] = datetime.now(UTC).isoformat()
        return metrics
    except OSError as exc:
        log.warning(
            "Failed to probe host volume disk usage at %s (%s): %s",
            measure_path,
            resolved_drive,
            exc,
        )
        return {
            "drive": resolved_drive,
            "path": measure_path,
            "total_bytes": 0,
            "used_bytes": 0,
            "free_bytes": 0,
            "total_gb": 0.0,
            "used_gb": 0.0,
            "free_gb": 0.0,
            "percent": 0.0,
            "free_percent": 0.0,
            "min_free_gb_floor": min_free_gb or DEFAULT_HOST_VOLUME_MIN_FREE_GB,
            "min_free_percent_floor": min_free_percent or DEFAULT_HOST_VOLUME_MIN_FREE_PERCENT,
            "status": "critical",
            "scheduling_inhibited": True,
            "hard_floor_hit": True,
            "reasons": [f"Host volume {resolved_drive} inaccessible: {exc}"],
            "measured_at": datetime.now(UTC).isoformat(),
        }


def get_host_volume_metrics(
    drive: str | None = None,
    disk_usage_fn: Callable[[str], Any] = shutil.disk_usage,
    min_free_percent: float | None = None,
    min_free_gb: float | None = None,
) -> dict[str, Any]:
    """Return high-level host volume disk metrics for system telemetry and fleet status."""
    return probe_host_volume(
        drive=drive,
        disk_usage_fn=disk_usage_fn,
        min_free_percent=min_free_percent,
        min_free_gb=min_free_gb,
    )
