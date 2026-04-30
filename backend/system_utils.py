"""System and hardware utility functions for runner-dashboard."""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import platform
import shutil
import subprocess
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import psutil
from dashboard_config import (
    CPU_HISTORY_MAXLEN,
    DISK_CRITICAL_PERCENT,
    DISK_MIN_FREE_GB,
    DISK_WARN_PERCENT,
    HOSTNAME,
    RUNNER_BASE_DIR,
)
from security import safe_subprocess_env

UTC = timezone.utc  # noqa: UP017
log = logging.getLogger("dashboard.system")

# CPU history ring-buffer: bounded by CPU_HISTORY_MAXLEN (default 60 ≈ 1 min at 1 Hz)
_cpu_history: deque[float] = deque(maxlen=CPU_HISTORY_MAXLEN)
BOOT_TIME = time.time()

# Host memory cache for WSL
HOST_MEMORY_GB: float | None = None
try:
    if "microsoft-standard" in platform.uname().release.lower():
        # Running in WSL -> try interop to get physical hardware capacity
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            env=safe_subprocess_env(),
        )
        if result.returncode == 0:
            HOST_MEMORY_GB = round(int(result.stdout.strip()) / (1024**3), 1)
except Exception:  # noqa: BLE001
    pass


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


def get_gpu_info() -> dict:
    """Query nvidia-smi for GPU metrics. Returns empty dict if no NVIDIA GPU."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw,power.limit",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            env=safe_subprocess_env(),
        )
        if result.returncode != 0:
            return {}

        gpus = []
        for line in result.stdout.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 8:
                total = float(parts[1])
                used = float(parts[2])
                vram_pct = round(used / total * 100, 1) if total > 0 else 0
                gpus.append(
                    {
                        "name": parts[0],
                        "vram_total_mb": total,
                        "vram_used_mb": used,
                        "vram_free_mb": float(parts[3]),
                        "vram_percent": vram_pct,
                        "gpu_util_percent": float(parts[4]),
                        "temp_c": float(parts[5]),
                        "power_draw_w": (float(parts[6]) if parts[6] != "[N/A]" else None),
                        "power_limit_w": (float(parts[7]) if parts[7] != "[N/A]" else None),
                    }
                )
        return {"gpus": gpus, "count": len(gpus)}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}


def get_local_hardware_specs(gpu: dict | None = None) -> dict:
    """Return stable hardware facts."""
    mem = psutil.virtual_memory()
    gpu = gpu if gpu is not None else get_gpu_info()
    gpu_devices = gpu.get("gpus", []) if isinstance(gpu, dict) else []
    gpu_vram_values = [
        round(device.get("vram_total_mb", 0) / 1024, 1)
        for device in gpu_devices
        if isinstance(device, dict) and device.get("vram_total_mb") is not None
    ]
    return {
        "cpu_model": platform.processor() or platform.machine(),
        "cpu_physical_cores": psutil.cpu_count(logical=False),
        "cpu_logical_cores": psutil.cpu_count(logical=True),
        "memory_gb": HOST_MEMORY_GB or round(mem.total / (1024**3), 1),
        "wsl_memory_gb": round(mem.total / (1024**3), 1),
        "gpu_count": len(gpu_devices),
        "gpu_vram_gb": max(gpu_vram_values) if gpu_vram_values else None,
        "accelerators": [device.get("name") for device in gpu_devices if device.get("name")],
        "platform": platform.platform(),
    }


def get_workload_capacity_from_specs(specs: dict) -> dict:
    """Estimate workload capacity based on hardware specs."""
    logical = specs.get("cpu_logical_cores") or 0
    memory_gb = specs.get("memory_gb") or 0
    gpu_vram_gb = specs.get("gpu_vram_gb") or 0
    tags = set(specs.get("workload_tags") or [])
    if gpu_vram_gb:
        tags.add("gpu")
    if logical and logical >= 8:
        tags.add("parallel-ci")
    if memory_gb and memory_gb >= 32:
        tags.add("memory-heavy")
    if logical and logical <= 4:
        tags.add("small-ci")
    return {
        "cpu_slots": max(1, int(logical // 2)) if logical else None,
        "memory_slots": max(1, int(memory_gb // 8)) if memory_gb else None,
        "gpu_slots": specs.get("gpu_count", 0),
        "tags": sorted(list(tags)),
    }


def get_disk_pressure_snapshot(
    path: str,
    total_gb: float,
    used_gb: float,
    free_gb: float,
    percent: float,
) -> dict:
    """Return dashboard-safe disk pressure state."""
    status = "healthy"
    reasons = []
    if percent >= DISK_CRITICAL_PERCENT:
        status = "critical"
        reasons.append(f"disk usage >= {DISK_CRITICAL_PERCENT:g}%")
    elif percent >= DISK_WARN_PERCENT:
        status = "warning"
        reasons.append(f"disk usage >= {DISK_WARN_PERCENT:g}%")
    if free_gb <= DISK_MIN_FREE_GB:
        free_space_status = "critical" if free_gb <= max(5.0, DISK_MIN_FREE_GB / 2) else "warning"
        if status != "critical":
            status = free_space_status
        reasons.append(f"free space <= {DISK_MIN_FREE_GB:g} GB")

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
        "warn_percent": DISK_WARN_PERCENT,
        "critical_percent": DISK_CRITICAL_PERCENT,
        "min_free_gb": DISK_MIN_FREE_GB,
        "reasons": reasons,
        "recommendations": recommendations,
    }


def get_per_runner_resources(runner_limit: int) -> list[dict]:
    """Get CPU and memory usage for each runner's worker processes."""
    runner_procs = []
    for i in range(1, runner_limit + 1):
        runner_info: dict[str, Any] = {
            "runner_num": i,
            "cpu_percent": 0.0,
            "memory_mb": 0.0,
            "process_count": 0,
            "status": "stopped",
        }

        # Find runner processes by looking at the runner directory
        runner_dir = str(RUNNER_BASE_DIR / f"runner-{i}")
        proc_fields = [
            "pid",
            "name",
            "cmdline",
            "cpu_percent",
            "memory_info",
        ]
        for proc in psutil.process_iter(proc_fields):
            try:
                cmdline = " ".join(proc.info.get("cmdline") or [])
                is_runner = runner_dir in cmdline or ("Runner.Listener" in cmdline and f"runner-{i}" in cmdline)
                if is_runner:
                    runner_info["cpu_percent"] += proc.info.get("cpu_percent", 0) or 0
                    mem = proc.info.get("memory_info")
                    if mem:
                        runner_info["memory_mb"] += mem.rss / (1024 * 1024)
                    runner_info["process_count"] += 1
                    runner_info["status"] = "running"
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        runner_info["cpu_percent"] = round(runner_info["cpu_percent"], 1)
        runner_info["memory_mb"] = round(runner_info["memory_mb"], 1)
        runner_procs.append(runner_info)
    return runner_procs


async def get_system_metrics_snapshot(runner_limit: int | None = None) -> dict:
    """Real-time system resource metrics."""
    cpu_freq = psutil.cpu_freq()
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk_path = str(RUNNER_BASE_DIR) if RUNNER_BASE_DIR.exists() else "/"
    disk = shutil.disk_usage(disk_path)
    disk_total_gb = round(disk.total / (1024**3), 1)
    disk_used_gb = round(disk.used / (1024**3), 1)
    disk_free_gb = round(disk.free / (1024**3), 1)
    disk_percent = round(disk.used / disk.total * 100, 1)

    net = psutil.net_io_counters()
    per_cpu = psutil.cpu_percent(interval=0, percpu=True)
    current_cpu = psutil.cpu_percent(interval=0)
    _cpu_history.append(current_cpu)
    cpu_avg_1m = round(sum(_cpu_history) / len(_cpu_history), 1) if _cpu_history else current_cpu

    try:
        uptime_seconds = time.time() - psutil.boot_time()
    except Exception:
        uptime_seconds = 0
    dashboard_uptime = time.time() - BOOT_TIME

    gpu_info = get_gpu_info()
    disk_pressure = get_disk_pressure_snapshot(
        path=disk_path,
        total_gb=disk_total_gb,
        used_gb=disk_used_gb,
        free_gb=disk_free_gb,
        percent=disk_percent,
    )
    hardware_specs = get_local_hardware_specs(gpu_info)

    metrics = {
        "hostname": HOSTNAME,
        "platform": platform.platform(),
        "timestamp": datetime.now(UTC).isoformat(),
        "uptime_seconds": int(uptime_seconds),
        "dashboard_uptime_seconds": int(dashboard_uptime),
        "cpu": {
            "cores_physical": psutil.cpu_count(logical=False),
            "cores_logical": psutil.cpu_count(logical=True),
            "percent": current_cpu,
            "percent_1m_avg": cpu_avg_1m,
            "per_cpu_percent": per_cpu,
            "freq_current_mhz": round(cpu_freq.current, 0) if cpu_freq else None,
            "freq_max_mhz": round(cpu_freq.max, 0) if cpu_freq else None,
        },
        "memory": {
            "host_total_gb": HOST_MEMORY_GB,
            "total_gb": round(mem.total / (1024**3), 1),
            "used_gb": round(mem.used / (1024**3), 1),
            "available_gb": round(mem.available / (1024**3), 1),
            "percent": mem.percent,
            "swap_total_gb": round(swap.total / (1024**3), 1),
            "swap_used_gb": round(swap.used / (1024**3), 1),
            "swap_percent": swap.percent,
        },
        "disk": {
            "path": disk_path,
            "total_gb": disk_total_gb,
            "used_gb": disk_used_gb,
            "free_gb": disk_free_gb,
            "percent": disk_percent,
            "pressure": disk_pressure,
        },
        "network": {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
            "packets_sent": net.packets_sent,
            "packets_recv": net.packets_recv,
        },
        "gpu": gpu_info,
        "hardware_specs": hardware_specs,
        "workload_capacity": get_workload_capacity_from_specs(hardware_specs),
        "runner_processes": get_per_runner_resources(runner_limit) if runner_limit else [],
    }

    # On WSL, also report the Windows host disk (/mnt/c)
    wsl_host_path = Path("/mnt/c")
    if wsl_host_path.exists():
        try:
            wd = shutil.disk_usage(str(wsl_host_path))
            metrics["disk"]["windows_host"] = {
                "total_gb": round(wd.total / (1024**3), 1),
                "used_gb": round(wd.used / (1024**3), 1),
                "free_gb": round(wd.free / (1024**3), 1),
                "percent": round(wd.used / wd.total * 100, 1),
            }
        except Exception:  # noqa: BLE001
            pass

    return metrics


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


def classify_node_offline(exc: Exception | None = None, *, status_code: int | None = None) -> dict:
    """Classify why a fleet node is unreachable.

    Uses typed exception checks (httpx exception hierarchy and OSError.errno)
    rather than fragile substring matching on str(exc).
    """
    if status_code:
        if status_code == 401:
            return {"offline_reason": "auth", "offline_detail": "401 Unauthorized"}
        if status_code == 403:
            return {"offline_reason": "auth", "offline_detail": "403 Forbidden"}
        if status_code >= 500:
            return {"offline_reason": "error", "offline_detail": f"HTTP {status_code}"}
        return {"offline_reason": "other", "offline_detail": f"HTTP {status_code}"}

    if exc is None:
        return {"offline_reason": "unknown", "offline_detail": "Unknown"}

    if isinstance(exc, httpx.TimeoutException):
        return {"offline_reason": "timeout", "offline_detail": "Connection timed out"}

    if isinstance(exc, httpx.ConnectError):
        cause = exc.__cause__ or exc
        os_err = cause if isinstance(cause, OSError) else None
        if os_err and os_err.errno == errno.ECONNREFUSED:
            return {"offline_reason": "refused", "offline_detail": "Connection refused"}
        if os_err and os_err.errno in {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ECONNRESET}:
            return {"offline_reason": "network", "offline_detail": "No route to host"}
        return {"offline_reason": "refused", "offline_detail": "Connection refused"}

    return {"offline_reason": "other", "offline_detail": str(exc)[:50]}


def resource_offline_reason(system: dict) -> dict | None:
    """Classify if a node is 'offline' due to resource pressure."""
    disk = system.get("disk", {})
    pressure = disk.get("pressure", {})
    if pressure.get("status") == "critical":
        return {"offline_reason": "disk-pressure", "offline_detail": pressure.get("reasons", ["Disk critical"])[0]}

    mem = system.get("memory", {})
    if mem.get("percent", 0) >= 98:
        return {"offline_reason": "oom-pressure", "offline_detail": "Memory usage >= 98%"}

    return None
