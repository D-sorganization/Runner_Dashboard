"""System metrics and hardware information routes."""

from __future__ import annotations

import datetime as _dt
import logging
import os
import platform
import shutil
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psutil
from dashboard_config import (
    CPU_HISTORY_MAXLEN,
    DISK_CRITICAL_PERCENT,
    DISK_MIN_FREE_GB,
    DISK_WARN_PERCENT,
    HOSTNAME,
    RUNNER_BASE_DIR,
)
from fastapi import APIRouter

if TYPE_CHECKING:
    from collections.abc import Callable

# Python 3.11+ has datetime.UTC; fall back to timezone.utc for 3.10
try:
    UTC = _dt.UTC  # type: ignore[attr-defined]
except AttributeError:
    UTC = _dt.timezone.utc  # noqa: UP017

log = logging.getLogger("dashboard.system")
router = APIRouter(tags=["system"])

# Will be set by server.py after import
_get_runner_capacity_snapshot: Callable[[], dict[str, Any]] | None = None
_boot_time: float | None = None
_host_memory_gb: float | None = None

# CPU history ring-buffer: bounded by CPU_HISTORY_MAXLEN (default 60 ≈ 1 min at 1 Hz)
assert CPU_HISTORY_MAXLEN > 0, "CPU_HISTORY_MAXLEN must be positive"  # DbC
_cpu_history: deque[float] = deque(maxlen=CPU_HISTORY_MAXLEN)


def _disk_pressure_snapshot(
    *,
    path: str,
    total_gb: float,
    used_gb: float,
    free_gb: float,
    percent: float,
) -> dict:
    """Return dashboard-safe disk pressure state for autoscaling and UI alerts."""
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


def _local_hardware_specs(gpu: dict | None = None) -> dict:
    """Return stable-enough hardware facts for fleet workload placement."""
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
        "memory_gb": _host_memory_gb or round(mem.total / (1024**3), 1),
        "wsl_memory_gb": round(mem.total / (1024**3), 1),
        "gpu_count": len(gpu_devices),
        "gpu_vram_gb": max(gpu_vram_values) if gpu_vram_values else None,
        "accelerators": [device.get("name") for device in gpu_devices if device.get("name")],
        "platform": platform.platform(),
    }


def _workload_capacity_from_specs(specs: dict) -> dict:
    """Calculate workload capacity from hardware specifications."""
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
        "memory_gb": memory_gb or None,
        "gpu_vram_gb": gpu_vram_gb or None,
        "tags": sorted(tags),
    }


def get_gpu_info() -> dict:
    """Query nvidia-smi for GPU metrics. Returns empty dict if no NVIDIA GPU."""
    try:

        def safe_subprocess_env() -> dict[str, str]:
            """Return a safe environment for subprocess calls."""
            safe_vars = ["PATH", "HOME", "USER", "SHELL", "LANG"]
            return {k: os.environ.get(k, "") for k in safe_vars}

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


def _runner_limit() -> int:
    """Return the hard runner capacity this dashboard is allowed to manage."""
    from dashboard_config import MAX_RUNNERS, NUM_RUNNERS

    return max(NUM_RUNNERS, MAX_RUNNERS)


def get_per_runner_resources() -> list[dict]:
    """Get CPU and memory usage for each runner's worker processes."""
    runner_procs = []
    for i in range(1, _runner_limit() + 1):
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


def set_runner_capacity_snapshot_func(func: Callable[[], dict[str, Any]]) -> None:
    """Set the runner capacity snapshot function (injected from server.py)."""
    global _get_runner_capacity_snapshot  # noqa: PLW0603
    _get_runner_capacity_snapshot = func


def set_boot_time(boot_time: float) -> None:
    """Set the boot time (injected from server.py)."""
    global _boot_time  # noqa: PLW0603
    _boot_time = boot_time


def set_host_memory_gb(host_memory_gb: float | None) -> None:
    """Set the host memory in GB (injected from server.py)."""
    global _host_memory_gb  # noqa: PLW0603
    _host_memory_gb = host_memory_gb


@router.get("/api/system")
async def get_system_metrics():
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

    if os.name == "nt":
        net = psutil.net_io_counters()
        per_cpu = psutil.cpu_percent(interval=0, percpu=True)
        current_cpu = psutil.cpu_percent(interval=0)
        _cpu_history.append(current_cpu)
        cpu_avg_1m = round(sum(_cpu_history) / len(_cpu_history), 1) if _cpu_history else current_cpu
        uptime_seconds = time.time() - psutil.boot_time()
        dashboard_uptime = time.time() - (_boot_time or time.time())
        disk_pressure = _disk_pressure_snapshot(
            path=disk_path,
            total_gb=disk_total_gb,
            used_gb=disk_used_gb,
            free_gb=disk_free_gb,
            percent=disk_percent,
        )
        hardware_specs = _local_hardware_specs(None)
        return {
            "hostname": HOSTNAME,
            "platform": platform.platform(),
            "timestamp": __import__("datetime").datetime.now(UTC).isoformat(),
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
                "load_avg_1m": 0,
                "load_avg_5m": 0,
                "load_avg_15m": 0,
            },
            "memory": {
                "host_total_gb": round(mem.total / (1024**3), 1),
                "wsl_total_gb": None,
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
                "windows_host": None,
            },
            "network": {
                "bytes_sent": net.bytes_sent,
                "bytes_recv": net.bytes_recv,
                "packets_sent": net.packets_sent,
                "packets_recv": net.packets_recv,
            },
            "gpu": None,
            "hardware_specs": hardware_specs,
            "workload_capacity": _workload_capacity_from_specs(hardware_specs),
            "runner_processes": [],
            "runner_capacity": {},
        }

    # On WSL, also report the Windows host disk (/mnt/c) where the VHDX lives.
    # The host disk is the binding constraint — if it fills up, WSL itself breaks.
    windows_disk = None
    wsl_host_path = Path("/mnt/c")
    if wsl_host_path.exists():
        try:
            wd = shutil.disk_usage(str(wsl_host_path))
            windows_disk = {
                "path": str(wsl_host_path),
                "total_gb": round(wd.total / (1024**3), 1),
                "used_gb": round(wd.used / (1024**3), 1),
                "free_gb": round(wd.free / (1024**3), 1),
                "percent": round(wd.used / wd.total * 100, 1),
                "pressure": _disk_pressure_snapshot(
                    path=str(wsl_host_path),
                    total_gb=round(wd.total / (1024**3), 1),
                    used_gb=round(wd.used / (1024**3), 1),
                    free_gb=round(wd.free / (1024**3), 1),
                    percent=round(wd.used / wd.total * 100, 1),
                ),
            }
        except OSError:
            pass

    # Use the more critical disk for the top-level pressure signal.
    windows_pct = (windows_disk or {}).get("percent", 0)
    effective_pct = max(disk_percent, windows_pct)
    effective_free = min(
        disk_free_gb,
        (windows_disk or {}).get("free_gb", disk_free_gb),
    )
    disk_pressure = _disk_pressure_snapshot(
        path=disk_path,
        total_gb=disk_total_gb,
        used_gb=disk_used_gb,
        free_gb=effective_free,
        percent=effective_pct,
    )

    # Load averages (1, 5, 15 min)
    try:
        load_avg = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)
    except OSError:
        load_avg = (0, 0, 0)

    # Network I/O
    net = psutil.net_io_counters()

    # Per-CPU usage
    per_cpu = psutil.cpu_percent(interval=0, percpu=True)
    current_cpu = psutil.cpu_percent(interval=0)
    _cpu_history.append(current_cpu)
    cpu_avg_1m = round(sum(_cpu_history) / len(_cpu_history), 1) if _cpu_history else current_cpu

    # Uptime
    uptime_seconds = time.time() - psutil.boot_time()
    dashboard_uptime = time.time() - (_boot_time or time.time())
    gpu = get_gpu_info()
    hardware_specs = _local_hardware_specs(gpu)

    return {
        "hostname": HOSTNAME,
        "platform": platform.platform(),
        "timestamp": __import__("datetime").datetime.now(UTC).isoformat(),
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
            "load_avg_1m": round(load_avg[0], 2),
            "load_avg_5m": round(load_avg[1], 2),
            "load_avg_15m": round(load_avg[2], 2),
        },
        "memory": {
            "host_total_gb": _host_memory_gb,
            "wsl_total_gb": round(mem.total / (1024**3), 1),
            "total_gb": _host_memory_gb or round(mem.total / (1024**3), 1),
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
            "windows_host": windows_disk,
        },
        "network": {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
            "packets_sent": net.packets_sent,
            "packets_recv": net.packets_recv,
        },
        "gpu": gpu,
        "hardware_specs": hardware_specs,
        "workload_capacity": _workload_capacity_from_specs(hardware_specs),
        "runner_processes": get_per_runner_resources(),
        "runner_capacity": _get_runner_capacity_snapshot() if _get_runner_capacity_snapshot else {},
    }
