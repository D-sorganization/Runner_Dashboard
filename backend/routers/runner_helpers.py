"""Shared helper utilities for runner routes.

Extracted from runners.py to keep modules under the 500-line cap.
Provides utility functions used across all runner sub-routers.
"""

from __future__ import annotations

import datetime as _dt_mod
import logging
from pathlib import Path
from typing import Any

from dashboard_config import RUNNER_BASE_DIR
from models.github_payloads import GhRunner
from runners import service_control as _runner_service_control
from system_utils import run_cmd

# Python 3.11+ has datetime.UTC; fall back to timezone.utc for 3.10
UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
log = logging.getLogger("dashboard.runners")


def principal_log_id(principal: Any) -> str:
    """Return a stable identifier for logging authenticated runner-control calls."""
    return str(getattr(principal, "user_id", getattr(principal, "id", "unknown")))


def runner_svc_path(runner_num: int) -> Path:
    """Return the path to a runner's svc.sh script.

    Args:
        runner_num: Local 1-based runner index.

    Returns:
        Path object pointing to the service script.
    """
    return RUNNER_BASE_DIR / f"runner-{runner_num}" / "svc.sh"


async def run_runner_svc(runner_num: int, action: str, timeout: int = 30) -> tuple[int, str, str]:
    """Execute ./svc.sh <action> for a runner.

    Args:
        runner_num: Local 1-based runner index.
        action: Action to execute (start, stop, etc.).
        timeout: Command timeout in seconds.

    Returns:
        Tuple of (exit_code, stdout, stderr).
    """
    svc_path = runner_svc_path(runner_num)
    if not svc_path.exists():
        return 1, "", f"Service script not found: {svc_path}"
    # Use sudo if required, or run directly if permissions allow
    cmd = ["sudo", "-n", str(svc_path), action]
    return await run_cmd(cmd, timeout=timeout)


def runner_num_from_id(runner_id: int, runners: list[dict]) -> int | None:
    """Map a GitHub runner ID to a local slot only when the name matches this host (#2519)."""
    return _runner_service_control.runner_num_from_id(runner_id, runners)


def count_runner_capacity(runners: list[dict]) -> dict[str, int]:
    """Compute fleet capacity counts from a list of GitHub runner dicts.

    Single source of truth for the online/busy/idle/utilization arithmetic so
    both ``/api/runners/fleet/capacity`` and the Conductor orchestrator
    admission gate agree on what "idle" means (DRY — issue #1282).

    Args:
        runners: List of runner dicts from the GitHub Actions runners API.

    Returns:
        Flat dict of integer counts: ``total_runners``, ``online_runners``,
        ``offline_runners``, ``busy_runners``, ``idle_runners``,
        ``utilization_percent``.
    """
    total = len(runners)
    online = sum(1 for r in runners if r.get("status") == "online")
    busy = sum(1 for r in runners if r.get("busy"))
    idle = max(online - busy, 0)
    utilization_percent = int((busy / online * 100) if online > 0 else 0)
    return {
        "total_runners": total,
        "online_runners": online,
        "offline_runners": total - online,
        "busy_runners": busy,
        "idle_runners": idle,
        "utilization_percent": utilization_percent,
    }


def runner_sort_key(runner: dict) -> tuple[str, int, str]:
    """Sort key for runners: status (online first), then local index, then name.

    Args:
        runner: Runner dict from GitHub API.

    Returns:
        Tuple for sorting (status_rank, runner_number, name).
    """
    status_rank = "0" if runner.get("status") == "online" else "1"
    name = runner.get("name", "")
    try:
        num = int(name.split("-")[-1]) if "-" in name else 0
    except (ValueError, IndexError):
        num = 0
    return (status_rank, num, name)


def is_matlab_runner(runner: dict | GhRunner) -> bool:
    """Check if runner has MATLAB installed by examining labels.

    Accepts both raw GitHub API dicts and typed ``GhRunner`` instances.

    Args:
        runner: Runner dict or typed GhRunner from GitHub API.

    Returns:
        True if MATLAB label is present.
    """
    if isinstance(runner, GhRunner):
        label_names = [lbl.lower() for lbl in runner.label_names]
    else:
        label_names = [lbl.get("name", "").lower() for lbl in runner.get("labels", []) if isinstance(lbl, dict)]
    return "matlab" in label_names or "windows-matlab" in label_names


def matlab_runner_summary(runner: dict | GhRunner) -> dict[str, Any]:
    """Extract summary for MATLAB runners.

    Accepts both raw GitHub API dicts and typed ``GhRunner`` instances.

    Args:
        runner: Runner dict or typed GhRunner from GitHub API.

    Returns:
        Simplified runner dict with key fields.
    """
    if isinstance(runner, GhRunner):
        return {
            "id": runner.id,
            "name": runner.name,
            "status": runner.status,
            "busy": runner.busy,
            "labels": runner.label_names,
        }
    return {
        "id": runner.get("id"),
        "name": runner.get("name"),
        "status": runner.get("status"),
        "busy": runner.get("busy"),
        "labels": [lbl.get("name") for lbl in runner.get("labels", []) if isinstance(lbl, dict)],
    }


def runner_health_check(runner: dict | GhRunner, system_metrics: dict | None = None) -> dict[str, Any]:
    """Compute health status for a runner.

    Accepts both raw GitHub API dicts and typed ``GhRunner`` instances.

    Args:
        runner: Runner dict or typed GhRunner from GitHub API.
        system_metrics: Optional system metrics snapshot.

    Returns:
        Health check result dict.
    """
    health_status = "healthy"
    issues = []
    runner_id: int | None
    runner_name: str | None
    status: str | None

    if isinstance(runner, GhRunner):
        runner_id = runner.id
        runner_name = runner.name
        status = runner.status
        labels: list = runner.labels
    else:
        runner_id = runner.get("id")
        runner_name = runner.get("name")
        status = runner.get("status")
        labels = runner.get("labels", [])

    if status != "online":
        health_status = "offline"
        issues.append(f"runner offline ({status})")

    if not labels:
        issues.append("no labels assigned")

    if system_metrics and isinstance(system_metrics, dict):
        tc_issues = system_metrics.get("toolchain_issues") or system_metrics.get("issues") or []
        if isinstance(tc_issues, (list, tuple)):
            for issue in tc_issues:
                issues.append(str(issue))
                if health_status == "healthy":
                    health_status = "degraded"

    return {
        "runner_id": runner_id,
        "runner_name": runner_name,
        "status": health_status,
        "issues": issues,
        "last_check": _dt_mod.datetime.now(UTC).isoformat(),
    }


def check_toolchain_health(
    binaries: tuple[str, ...] = ("rustc", "cargo"),
    *,
    custom_lookup: Any = None,
) -> dict[str, Any]:
    """Check availability and executability of required toolchain binaries (#1945).

    Surfaces runner-state issues like 'toolchain binary missing' or 'rustc (never executed)'
    when toolchain binaries have vanished mid-build or cannot be executed (os error 2).
    """
    import shutil

    lookup = custom_lookup or shutil.which
    issues: list[str] = []
    missing: list[str] = []

    for binary in binaries:
        path = lookup(binary)
        if not path:
            msg = f"toolchain binary missing: '{binary}' not found in PATH"
            issues.append(msg)
            missing.append(binary)

    status = "healthy" if not issues else "degraded"
    return {
        "status": status,
        "healthy": len(issues) == 0,
        "issues": issues,
        "missing": missing,
        "checked": list(binaries),
        "timestamp": _dt_mod.datetime.now(UTC).isoformat(),
    }
