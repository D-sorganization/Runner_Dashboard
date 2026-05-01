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
from system_utils import run_cmd

# Python 3.11+ has datetime.UTC; fall back to timezone.utc for 3.10
UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
log = logging.getLogger("dashboard.runners")


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
    """Extract local 1-based runner index from a GitHub runner dict's name.

    Args:
        runner_id: GitHub runner ID.
        runners: List of runner dicts from GitHub API.

    Returns:
        Local runner number (1-based), or None if not found.
    """
    for r in runners:
        if r.get("id") == runner_id:
            name = r.get("name", "")
            # Expecting names like "d-sorg-fleet-runner-1"
            if "runner-" in name:
                try:
                    return int(name.split("runner-")[-1])
                except (ValueError, IndexError):
                    pass
    return None


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

    return {
        "runner_id": runner_id,
        "runner_name": runner_name,
        "status": health_status,
        "issues": issues,
        "last_check": _dt_mod.datetime.now(UTC).isoformat(),
    }
