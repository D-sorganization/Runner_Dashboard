"""System metrics and hardware information routes."""

from __future__ import annotations

import json
import logging
import platform
import subprocess
from collections import deque
from typing import TYPE_CHECKING, Any

import psutil
from dashboard_config import (
    CPU_HISTORY_MAXLEN,
    DISK_CRITICAL_PERCENT,
    DISK_MIN_FREE_GB,
    DISK_WARN_PERCENT,
    RUNNER_BASE_DIR,
)
from fastapi import APIRouter
from security import safe_subprocess_env

if TYPE_CHECKING:
    from collections.abc import Callable

import datetime as _dt_mod

# Python 3.11+ has datetime.UTC; fall back to timezone.utc for 3.10
UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017

log = logging.getLogger("dashboard.system")
router = APIRouter(tags=["system"])

# Will be set by server.py after import
_get_runner_capacity_snapshot: Callable[[], dict[str, Any]] | None = None
_boot_time: float | None = None
_host_memory_gb: float | None = None

# CPU history ring-buffer: bounded by CPU_HISTORY_MAXLEN (default 60 ≈ 1 min at 1 Hz)
assert CPU_HISTORY_MAXLEN > 0, "CPU_HISTORY_MAXLEN must be positive"  # DbC
_cpu_history: deque[float] = deque(maxlen=CPU_HISTORY_MAXLEN)
_POWERSHELL_CANDIDATES = (
    "powershell.exe",
    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
)


def _float_metric(value: object, default: float) -> float:
    if isinstance(value, int | float):
        return float(value)
    return default


def _windows_host_resource_snapshot() -> dict | None:
    """Return Windows host CPU/RAM metrics when this backend is running under WSL."""
    if "microsoft" not in platform.uname().release.lower():
        return None

    command = r"""
$cpu = (Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'").PercentProcessorTime
$os = Get-CimInstance Win32_OperatingSystem
$total = [double]$os.TotalVisibleMemorySize * 1KB
$free = [double]$os.FreePhysicalMemory * 1KB
$used = $total - $free
[pscustomobject]@{
  cpu_percent = [math]::Round([double]$cpu, 1)
  memory_total_gb = [math]::Round($total / 1GB, 1)
  memory_used_gb = [math]::Round($used / 1GB, 1)
  memory_available_gb = [math]::Round($free / 1GB, 1)
  memory_percent = [math]::Round(($used / $total) * 100, 1)
} | ConvertTo-Json -Compress
"""
    result = None
    for powershell in _POWERSHELL_CANDIDATES:
        try:
            result = subprocess.run(
                [powershell, "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=5,
                env=safe_subprocess_env(),
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0 and result.stdout.strip():
            break
    if result is None:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


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
    limit = _runner_limit()
    runner_procs: list[dict[str, Any]] = [
        {
            "runner_num": i,
            "cpu_percent": 0.0,
            "memory_mb": 0.0,
            "process_count": 0,
            "status": "stopped",
        }
        for i in range(1, limit + 1)
    ]

    # Precompute path patterns to avoid redundant filesystem or string operations
    runner_dirs = {i: str(RUNNER_BASE_DIR / f"runner-{i}") for i in range(1, limit + 1)}

    proc_fields = [
        "pid",
        "name",
        "cmdline",
        "cpu_percent",
        "memory_info",
    ]
    for proc in psutil.process_iter(proc_fields):
        try:
            cmdline_list = proc.info.get("cmdline") or []
            cmdline = " ".join(cmdline_list)
            if not cmdline:
                continue
            for i in range(1, limit + 1):
                runner_dir = runner_dirs[i]
                is_runner = runner_dir in cmdline or ("Runner.Listener" in cmdline and f"runner-{i}" in cmdline)
                if is_runner:
                    runner_info = runner_procs[i - 1]
                    runner_info["cpu_percent"] += proc.info.get("cpu_percent", 0) or 0
                    mem = proc.info.get("memory_info")
                    if mem:
                        runner_info["memory_mb"] += mem.rss / (1024 * 1024)
                    runner_info["process_count"] += 1
                    runner_info["status"] = "running"
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    for runner_info in runner_procs:
        runner_info["cpu_percent"] = round(runner_info["cpu_percent"], 1)
        runner_info["memory_mb"] = round(runner_info["memory_mb"], 1)

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
async def get_system_metrics() -> dict[str, Any]:
    """Real-time system resource metrics."""
    from system_utils import get_system_metrics_snapshot

    return await get_system_metrics_snapshot(
        runner_limit=_runner_limit(),
        boot_time=_boot_time,
        host_memory_gb=_host_memory_gb,
        get_runner_capacity_snapshot=_get_runner_capacity_snapshot,
    )
