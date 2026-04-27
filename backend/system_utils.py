"""System and hardware utility functions for runner-dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
from pathlib import Path

import psutil

log = logging.getLogger("dashboard.system")


def get_deployment_info(version: str, deployment_file: Path) -> dict:
    """Return the deployed dashboard revision."""
    fallback = {
        "app": "runner-dashboard",
        "version": version,
        "git_sha": os.environ.get("DASHBOARD_GIT_SHA", "unknown"),
        "git_branch": os.environ.get("DASHBOARD_GIT_BRANCH", "unknown"),
        "source": "environment",
    }
    try:
        if deployment_file.exists():
            payload = json.loads(deployment_file.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload.setdefault("app", "runner-dashboard")
                payload.setdefault("version", version)
                payload.setdefault("source", "deployment-file")
                return payload
    except (json.JSONDecodeError, OSError):
        pass
    return fallback


def get_local_hardware_specs(host_memory_gb: float | None = None) -> dict:
    """Return stable hardware facts."""
    mem = psutil.virtual_memory()
    # Note: GPU info usually requires more complex calls, keeping it simplified for now
    # or passing it in if already collected.
    return {
        "cpu_model": platform.processor() or platform.machine(),
        "cpu_physical_cores": psutil.cpu_count(logical=False),
        "cpu_logical_cores": psutil.cpu_count(logical=True),
        "memory_gb": host_memory_gb or round(mem.total / (1024**3), 1),
        "wsl_memory_gb": round(mem.total / (1024**3), 1),
        "platform": platform.platform(),
    }


def get_disk_pressure_snapshot(
    path: str,
    total_gb: float,
    used_gb: float,
    free_gb: float,
    percent: float,
    warn_percent: float,
    critical_percent: float,
    min_free_gb: float,
) -> dict:
    """Return dashboard-safe disk pressure state."""
    status = "healthy"
    reasons = []
    if percent >= critical_percent:
        status = "critical"
        reasons.append(f"disk usage >= {critical_percent:g}%")
    elif percent >= warn_percent:
        status = "warning"
        reasons.append(f"disk usage >= {warn_percent:g}%")
    if free_gb <= min_free_gb:
        free_space_status = "critical" if free_gb <= max(5.0, min_free_gb / 2) else "warning"
        if status != "critical":
            status = free_space_status
        reasons.append(f"free space <= {min_free_gb:g} GB")

    recommendations = []
    if status != "healthy":
        recommendations.extend(
            [
                "Run runner-dashboard/deploy/runner-cleanup.sh to clear stale runner work directories.",
                "Prune unused Docker images, volumes, and build caches if Docker is used in WSL.",
                "After cleanup, run wsl --shutdown from Windows and compact the distro VHDX.",
            ]
        )

    return {
        "status": status,
        "path": path,
        "total_gb": total_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "percent": percent,
        "warn_percent": warn_percent,
        "critical_percent": critical_percent,
        "min_free_gb": min_free_gb,
        "reasons": reasons,
        "recommendations": recommendations,
    }


async def run_cmd(cmd: list[str], timeout: int = 30, cwd: Path | None = None) -> tuple[int, str, str]:
    """Run a shell command asynchronously."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode if proc.returncode is not None else -1,
            stdout.decode(),
            stderr.decode(),
        )
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except TimeoutError:
        if "proc" in locals():
            proc.kill()
        return -1, "", "Command timed out"
