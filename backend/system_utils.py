"""System and hardware utility functions for runner-dashboard."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import logging
import os
import platform
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
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
_PHYSICAL_DISK_CACHE: dict[str, dict[str, str]] = {}
_WSL_BASE_PATH_CACHE: str | None = None
_WSL_BASE_PATH_LOOKED_UP: bool = False

# ---------------------------------------------------------------------------
# Persistent hardware-facts cache (issue: cold-start /api/fleet/status 504)
# ---------------------------------------------------------------------------
# ``get_storage_pools`` queries Windows ``Get-PhysicalDisk`` (media/bus type)
# and the WSL distro registry (BasePath) via PowerShell. These cold probes
# take ~10 s and ~1.3 s respectively on the WSL host, and together with the
# 2.8 s Windows host-resource snapshot they blow the 15 s ``PROXY_TO_HUB_S``
# budget on the FIRST request after every ``systemctl restart`` — surfacing as
# HTTP 504 "Hub timeout" on ``/api/fleet/status``.
#
# The values are *static hardware facts* (a drive's media type / bus type and
# the distro's VHDX base path do not change between restarts), so we persist
# them to a small JSON file. After the first warm-up the cold path is served
# from disk in microseconds, and the live PowerShell probe only re-runs when
# the persisted value is missing. Persisted facts are reused even across
# process restarts, so a fresh service never pays the 13 s cold tax again.
_HARDWARE_FACTS_DIR = Path(
    os.environ.get(
        "RUNNER_DASHBOARD_STATE_DIR",
        Path.home() / ".config" / "runner-dashboard",
    )
)
_HARDWARE_FACTS_FILE = _HARDWARE_FACTS_DIR / "hardware_facts.json"

# Hard wall-clock budget for any single blocking PowerShell hardware probe.
# Kept well under PROXY_TO_HUB_S (15 s) so even a pathologically slow cold
# probe degrades to "Unknown" rather than timing out the whole endpoint.
_HW_PROBE_TIMEOUT_S = 4.0

# How long a probe result (including an "Unknown" failure) stays valid in the
# persistent cache before we re-probe. On some hosts ``Get-PhysicalDisk`` is
# both slow (~10 s) and returns nothing useful, so persisting the negative
# result for a day keeps every restart fast instead of re-paying the cold tax;
# we still re-probe daily in case the host configuration changes.
_HW_FACTS_TTL_S = 24 * 3600

# Live Windows host CPU/RAM is fetched via a ~2 s PowerShell ``Get-CimInstance``
# fork on EVERY ``/api/system`` and ``/api/fleet/status`` call (the latter also
# nests a local ``/api/system``). On a busy host that fork balloons to 5-7 s, and
# the frontend's steady polling means almost every poll pays it fresh — pushing
# the endpoint past its 15 s budget and surfacing as HTTP 504. A freshness window
# lets all callers within the window share a single fork. 10 s keeps the figures
# live enough for a fleet dashboard (CPU is also shown as a 1-minute rolling
# average) while collapsing the fork rate by an order of magnitude. Tunable via
# RUNNER_DASHBOARD_HOST_SNAPSHOT_TTL_S.
_HOST_SNAPSHOT_TTL_S = float(os.environ.get("RUNNER_DASHBOARD_HOST_SNAPSHOT_TTL_S", "10.0"))
_host_snapshot_cache: tuple[dict[str, float] | None, float] | None = None
_host_snapshot_lock = threading.Lock()


def _load_hardware_facts() -> dict[str, Any]:
    """Return persisted static hardware facts (best-effort, never raises)."""
    try:
        if _HARDWARE_FACTS_FILE.exists():
            payload = json.loads(_HARDWARE_FACTS_FILE.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
    except (OSError, json.JSONDecodeError) as exc:
        log.debug("hardware_facts: load failed: %s", exc)
    return {}


def _save_hardware_facts(facts: dict[str, Any]) -> None:
    """Persist static hardware facts atomically (best-effort, never raises)."""
    try:
        _HARDWARE_FACTS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _HARDWARE_FACTS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(facts), encoding="utf-8")
        tmp.replace(_HARDWARE_FACTS_FILE)
    except OSError as exc:
        log.debug("hardware_facts: save failed: %s", exc)


def get_powershell_candidates() -> list[str]:
    """Dynamically build list of PowerShell executable candidates."""
    candidates = ["powershell.exe"]
    mnt_path = Path("/mnt")
    if mnt_path.exists() and mnt_path.is_dir():
        try:
            for item in mnt_path.iterdir():
                if item.is_dir() and len(item.name) == 1 and item.name.isalpha():
                    candidates.append(str(item / "Windows/System32/WindowsPowerShell/v1.0/powershell.exe"))
        except OSError:
            pass
    for letter in ("c", "d"):
        p = f"/mnt/{letter}/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        if p not in candidates:
            candidates.append(p)
    return candidates


def _run_windows_powershell(command: str, *, deadline: float | None = None) -> str | None:
    """Run a PowerShell command on the Windows host and return its stdout.

    ``deadline`` is an optional ``time.monotonic()`` wall-clock budget for the
    whole call. Without it, a fully-failing probe iterates every PowerShell
    candidate at 5 s each (~10 s observed cold), which on its own can blow the
    15 s endpoint budget. With a deadline, each candidate's subprocess timeout
    is clamped to the remaining budget and the loop stops once it is exhausted,
    so the call degrades fast instead of hanging the request.
    """
    if "microsoft" not in platform.uname().release.lower():
        return None
    candidates = get_powershell_candidates()
    for powershell in candidates:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            timeout = min(5.0, remaining)
        else:
            timeout = 5.0
        try:
            result = subprocess.run(
                [powershell, "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=safe_subprocess_env(),
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def _run_windows_powershell_json(command: str, *, deadline: float | None = None) -> Any:
    """Run a PowerShell command on the Windows host and parse its stdout as JSON."""
    raw = _run_windows_powershell(command, deadline=deadline)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def get_wsl_distro_registry_info() -> dict[str, Any] | None:
    """Query Registry from WSL to find active distro details."""
    command = r"""
$distro = $env:WSL_DISTRO_NAME
$lxssPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss"
$guid = (Get-ItemProperty -Path $lxssPath -ErrorAction SilentlyContinue).DefaultDistribution
$basePath = $null
if (Test-Path $lxssPath) {
    Get-ChildItem -Path $lxssPath -ErrorAction SilentlyContinue | ForEach-Object {
        $subKey = $_.PSChildName
        $props = Get-ItemProperty -Path "$lxssPath\$subKey" -ErrorAction SilentlyContinue
        if ($props) {
            if ($distro -and $props.DistributionName -eq $distro) {
                $basePath = $props.BasePath
            }
            elseif (-not $basePath -and $guid -and $subKey -eq $guid) {
                $basePath = $props.BasePath
            }
        }
    }
    if (-not $basePath) {
        $subkeys = Get-ChildItem -Path $lxssPath -ErrorAction SilentlyContinue
        if ($subkeys -and $subkeys.Count -eq 1) {
            $props = Get-ItemProperty -Path "$lxssPath\$($subkeys[0].PSChildName)" -ErrorAction SilentlyContinue
            if ($props) {
                $basePath = $props.BasePath
            }
        }
    }
}
[pscustomobject]@{ BasePath = $basePath } | ConvertTo-Json -Compress
"""
    data = _run_windows_powershell_json(command, deadline=time.monotonic() + _HW_PROBE_TIMEOUT_S)
    if isinstance(data, dict):
        return data
    return None


def get_wsl_vhdx_path(base_path: str | None) -> str | None:
    """Return the Windows VHDX path for a WSL distribution base path."""
    if not base_path:
        return None
    return base_path.rstrip("\\") + "\\ext4.vhdx"


def get_wsl_host_disk_path(windows_path: str | None) -> str:
    """Extract host disk drive letter and convert to WSL mount path."""
    if windows_path and len(windows_path) >= 2 and windows_path[1] == ":":
        drive_letter = windows_path[0].lower()
        return f"/mnt/{drive_letter}"
    return "/mnt/c"


def get_windows_drive_physical_properties(drive_letter: str) -> dict[str, str]:
    """Query physical disk properties (MediaType, BusType) for a drive letter."""
    if not drive_letter or not drive_letter.isalpha():
        return {"media_type": "Unknown", "bus_type": "Unknown"}
    command = f"""
$letter = "{drive_letter}"
try {{
    $partition = Get-Partition -DriveLetter $letter -ErrorAction SilentlyContinue
    if ($partition) {{
        $disk = Get-Disk -Number $partition.DiskNumber -ErrorAction SilentlyContinue
        if ($disk) {{
            $phys = Get-PhysicalDisk | Where-Object {{ $_.DeviceNumber -eq $disk.Number }} -ErrorAction SilentlyContinue
            if ($phys) {{
                [pscustomobject]@{{
                    MediaType = $phys.MediaType.ToString()
                    BusType = $phys.BusType.ToString()
                }} | ConvertTo-Json -Compress
                exit
            }}
            [pscustomobject]@{{
                MediaType = $disk.MediaType.ToString()
                BusType = $disk.BusType.ToString()
            }} | ConvertTo-Json -Compress
            exit
        }}
    }}
}} catch {{}}
try {{
    $disk = Get-Disk -Number 0 -ErrorAction SilentlyContinue
    if ($disk) {{
        [pscustomobject]@{{
            MediaType = $disk.MediaType.ToString()
            BusType = $disk.BusType.ToString()
        }} | ConvertTo-Json -Compress
        exit
    }}
}} catch {{}}
"@{{MediaType='Unknown'; BusType='Unknown'}}"
"""
    data = _run_windows_powershell_json(command, deadline=time.monotonic() + _HW_PROBE_TIMEOUT_S)
    res = {"media_type": "Unknown", "bus_type": "Unknown"}
    if isinstance(data, dict):
        if data.get("MediaType"):
            res["media_type"] = str(data["MediaType"])
        if data.get("BusType"):
            res["bus_type"] = str(data["BusType"])
    return res


def get_cached_windows_drive_physical_properties(drive_letter: str) -> dict[str, str]:
    """Get windows drive physical properties with caching.

    Three-tier lookup so a cold process never pays the ~10 s ``Get-PhysicalDisk``
    probe inside the request budget more than once per host lifetime:

    1. In-memory cache (fastest, per-process).
    2. Persistent ``hardware_facts.json`` — media/bus type is static hardware,
       so a value learned before the last restart is still valid.
    3. Live PowerShell probe (bounded by ``_HW_PROBE_TIMEOUT_S``); only reached
       on a genuine cache miss. A successful probe is persisted for next time.

    A probe that times out or fails returns ``Unknown`` and is NOT persisted, so
    it will be retried (and the endpoint stays responsive in the meantime).
    """
    letter = drive_letter.upper()
    if letter in _PHYSICAL_DISK_CACHE:
        return _PHYSICAL_DISK_CACHE[letter]

    facts = _load_hardware_facts()
    disks = facts.get("physical_disks")
    if isinstance(disks, dict) and isinstance(disks.get(letter), dict):
        entry = disks[letter]
        fetched_at = entry.get("_fetched_at", 0)
        if isinstance(fetched_at, int | float) and (time.time() - fetched_at) < _HW_FACTS_TTL_S:
            cached = {
                "media_type": str(entry.get("media_type", "Unknown")),
                "bus_type": str(entry.get("bus_type", "Unknown")),
            }
            _PHYSICAL_DISK_CACHE[letter] = cached
            return cached

    props = get_windows_drive_physical_properties(letter)
    _PHYSICAL_DISK_CACHE[letter] = props
    # Persist the result — including an "Unknown" failure — with a timestamp so
    # restarts stay fast. The TTL (see _HW_FACTS_TTL_S) ensures we re-probe
    # periodically in case the host's disk topology changes.
    if not isinstance(disks, dict):
        disks = {}
    disks[letter] = {**props, "_fetched_at": time.time()}
    facts["physical_disks"] = disks
    _save_hardware_facts(facts)
    return props


def get_cached_wsl_base_path() -> str | None:
    """Retrieve and cache active WSL BasePath from registry.

    Mirrors :func:`get_cached_windows_drive_physical_properties`: the distro's
    VHDX base path is static, so it is served from the persistent
    ``hardware_facts.json`` before falling back to the ~1.3 s registry probe.
    """
    global _WSL_BASE_PATH_CACHE, _WSL_BASE_PATH_LOOKED_UP
    if _WSL_BASE_PATH_LOOKED_UP:
        return _WSL_BASE_PATH_CACHE

    facts = _load_hardware_facts()
    persisted = facts.get("wsl_base_path")
    if isinstance(persisted, str) and persisted:
        _WSL_BASE_PATH_CACHE = persisted
        _WSL_BASE_PATH_LOOKED_UP = True
        return _WSL_BASE_PATH_CACHE

    info = get_wsl_distro_registry_info()
    if info and "BasePath" in info:
        _WSL_BASE_PATH_CACHE = info["BasePath"]
    _WSL_BASE_PATH_LOOKED_UP = True
    if isinstance(_WSL_BASE_PATH_CACHE, str) and _WSL_BASE_PATH_CACHE:
        facts["wsl_base_path"] = _WSL_BASE_PATH_CACHE
        _save_hardware_facts(facts)
    return _WSL_BASE_PATH_CACHE


def _windows_host_resource_snapshot_uncached() -> dict[str, float] | None:
    """Fork PowerShell to read live Windows host CPU/RAM (no caching)."""
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
    data = _run_windows_powershell_json(command, deadline=time.monotonic() + _HW_PROBE_TIMEOUT_S)
    if not data or not isinstance(data, dict):
        return None
    try:
        return {
            "cpu_percent": float(data["cpu_percent"]),
            "memory_total_gb": float(data["memory_total_gb"]),
            "memory_used_gb": float(data["memory_used_gb"]),
            "memory_available_gb": float(data["memory_available_gb"]),
            "memory_percent": float(data["memory_percent"]),
        }
    except (TypeError, ValueError, KeyError):
        return None


def _windows_host_resource_snapshot() -> dict[str, float] | None:
    """Return live Windows host CPU/RAM metrics with a short freshness cache.

    The underlying PowerShell fork costs ~2 s and runs on every metrics request.
    A ``_HOST_SNAPSHOT_TTL_S`` window lets concurrent/rapid callers (frontend
    polling + the fleet-status peer fan-out) share one fork, which keeps
    ``/api/system`` and ``/api/fleet/status`` comfortably inside the 15 s budget
    on a busy host. A single in-process lock collapses a concurrent miss into one
    fork; if the fresh probe fails it returns ``None`` (callers fall back to
    psutil/WSL figures) without poisoning the cache.
    """
    global _host_snapshot_cache  # noqa: PLW0603
    now = time.monotonic()
    cached = _host_snapshot_cache
    if cached is not None and (now - cached[1]) < _HOST_SNAPSHOT_TTL_S:
        return cached[0]

    with _host_snapshot_lock:
        # Re-check: another thread may have refreshed while we waited.
        cached = _host_snapshot_cache
        now = time.monotonic()
        if cached is not None and (now - cached[1]) < _HOST_SNAPSHOT_TTL_S:
            return cached[0]
        data = _windows_host_resource_snapshot_uncached()
        if data is not None:
            _host_snapshot_cache = (data, time.monotonic())
            return data
        # Probe failed: serve a slightly-stale prior value if we have one rather
        # than dropping to the WSL fallback mid-spike; otherwise return None.
        return cached[0] if cached is not None else None


# Host memory cache for WSL
HOST_MEMORY_GB: float | None = None
try:
    if "microsoft-standard" in platform.uname().release.lower():
        data = _run_windows_powershell_json(
            "[pscustomobject]@{ total = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory } "
            "| ConvertTo-Json -Compress"
        )
        if data and isinstance(data, dict) and "total" in data:
            HOST_MEMORY_GB = round(int(data["total"]) / (1024**3), 1)
except (OSError, subprocess.SubprocessError, TimeoutError, ValueError):
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
    runner_procs: list[dict[str, Any]] = [
        {
            "runner_num": i,
            "cpu_percent": 0.0,
            "memory_mb": 0.0,
            "process_count": 0,
            "status": "stopped",
        }
        for i in range(1, runner_limit + 1)
    ]

    # Precompute path patterns to avoid redundant filesystem or string operations
    runner_dirs = {i: str(RUNNER_BASE_DIR / f"runner-{i}") for i in range(1, runner_limit + 1)}

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
            for i in range(1, runner_limit + 1):
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


def get_io_pressure_snapshot() -> dict[str, Any] | None:
    """Read and parse Linux PSI IO pressure from /proc/pressure/io."""
    path = Path("/proc/pressure/io")
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8")
        result = {}
        for line in content.strip().split("\n"):
            parts = line.split()
            if not parts:
                continue
            kind = parts[0]  # "some" or "full"
            metrics: dict[str, int | float] = {}
            for part in parts[1:]:
                if "=" in part:
                    k, v = part.split("=", 1)
                    if k == "total":
                        metrics[k] = int(v)
                    else:
                        metrics[k] = float(v)
            if metrics:
                result[kind] = metrics
        return result if result else None
    except Exception as exc:
        log.warning("Failed to parse /proc/pressure/io: %s", exc)
        return None


def get_storage_pools() -> list[dict[str, Any]]:
    """Get system storage pools (WSL virtual disk + host disk, or native drive)."""
    pools = []
    disk_path = str(RUNNER_BASE_DIR) if RUNNER_BASE_DIR.exists() else "/"
    try:
        du = shutil.disk_usage(disk_path)
        total_gb = round(du.total / (1024**3), 1)
        used_gb = round(du.used / (1024**3), 1)
        free_gb = round(du.free / (1024**3), 1)
        percent = round(du.used / du.total * 100, 1) if du.total > 0 else 0.0
    except OSError:
        total_gb = used_gb = free_gb = percent = 0.0

    pressure_state = get_disk_pressure_snapshot(
        path=disk_path,
        total_gb=total_gb,
        used_gb=used_gb,
        free_gb=free_gb,
        percent=percent,
    )

    is_wsl = "microsoft" in platform.uname().release.lower()

    if is_wsl:
        base_path = get_cached_wsl_base_path()
        vhdx_path = get_wsl_vhdx_path(base_path) if base_path else None

        host_disk_path = "/mnt/c"
        drive_letter = "C"
        if base_path and len(base_path) >= 2 and base_path[1] == ":":
            drive_letter = base_path[0].upper()
            host_disk_path = f"/mnt/{drive_letter.lower()}"

        phys = get_cached_windows_drive_physical_properties(drive_letter)
        media_type = phys.get("media_type", "Unknown")
        bus_type = phys.get("bus_type", "Unknown")

        local_pool = {
            "backing_disk_path": host_disk_path,
            "vhdx_path": vhdx_path,
            "bus_type": bus_type,
            "media_type": media_type,
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "percent": percent,
            "pressure": pressure_state,
        }
        pools.append(local_pool)

        if os.path.exists(host_disk_path):
            try:
                hdu = shutil.disk_usage(host_disk_path)
                htotal_gb = round(hdu.total / (1024**3), 1)
                hused_gb = round(hdu.used / (1024**3), 1)
                hfree_gb = round(hdu.free / (1024**3), 1)
                hpercent = round(hdu.used / hdu.total * 100, 1) if hdu.total > 0 else 0.0
            except OSError:
                htotal_gb = hused_gb = hfree_gb = hpercent = 0.0

            hpressure_state = get_disk_pressure_snapshot(
                path=host_disk_path,
                total_gb=htotal_gb,
                used_gb=hused_gb,
                free_gb=hfree_gb,
                percent=hpercent,
            )

            host_pool = {
                "backing_disk_path": host_disk_path,
                "vhdx_path": None,
                "bus_type": bus_type,
                "media_type": media_type,
                "total_gb": htotal_gb,
                "used_gb": hused_gb,
                "free_gb": hfree_gb,
                "percent": hpercent,
                "pressure": hpressure_state,
            }
            pools.append(host_pool)
    else:
        media_type = "Unknown"
        bus_type = "Unknown"
        backing_disk_path = disk_path

        if os.name == "nt":
            drive_letter = "C"
            if len(disk_path) >= 2 and disk_path[1] == ":":
                drive_letter = disk_path[0].upper()
            backing_disk_path = f"{drive_letter}:"
            phys = get_cached_windows_drive_physical_properties(drive_letter)
            media_type = phys.get("media_type", "Unknown")
            bus_type = phys.get("bus_type", "Unknown")

        local_pool = {
            "backing_disk_path": backing_disk_path,
            "vhdx_path": None,
            "bus_type": bus_type,
            "media_type": media_type,
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "percent": percent,
            "pressure": pressure_state,
        }
        pools.append(local_pool)

    return pools


def get_host_disk_for_pool(pool: dict[str, Any]) -> str:
    """Return the WSL host mount path for a runner pool's backing disk.

    Prefers the explicit ``storage.host_drive`` field (e.g. ``"D:"``).
    Falls back to the drive letter embedded in ``storage.vhdx_path`` (e.g.
    ``"D:\\WSL\\ext4.vhdx"`` → ``/mnt/d``).  Returns ``/mnt/c`` as a safe
    default when neither field is present.

    This corrects the prior assumption that all WSL distros live on C:, which
    caused the D: HDD-backed ext4.vhdx incident to go undetected (issue #754).
    """
    storage = pool.get("storage") or {}

    # Prefer explicitly declared host_drive
    host_drive: str | None = storage.get("host_drive")
    if host_drive and len(host_drive) >= 1 and host_drive[0].isalpha():
        return f"/mnt/{host_drive[0].lower()}"

    # Fall back to drive letter from vhdx_path
    vhdx_path: str | None = storage.get("vhdx_path")
    if vhdx_path and len(vhdx_path) >= 2 and vhdx_path[1] == ":" and vhdx_path[0].isalpha():
        return f"/mnt/{vhdx_path[0].lower()}"

    return "/mnt/c"


# Tier-aware pressure thresholds
_TIER_THRESHOLDS: dict[str, dict[str, float]] = {
    "nvme": {
        # NVMe: IO saturation is the primary failure mode.  Capacity thresholds
        # are slightly relaxed vs HDD because NVMe handles high fill better.
        "capacity_warn_percent": 85.0,
        "capacity_critical_percent": 93.0,
        "io_medium_threshold": 20.0,  # full avg10 >= 20 → medium
        "io_high_threshold": 50.0,  # full avg10 >= 50 → high
    },
    "hdd": {
        # HDD: capacity is the primary failure mode; IO saturation is a
        # secondary signal because HDDs are inherently slower.
        "capacity_warn_percent": 85.0,
        "capacity_critical_percent": 93.0,
        "io_medium_threshold": 50.0,  # higher threshold for HDD
        "io_high_threshold": 999.0,  # IO alone cannot escalate HDD to high
    },
    "ssd": {
        # SSD: same capacity-first model as HDD with slightly tighter IO.
        "capacity_warn_percent": 85.0,
        "capacity_critical_percent": 93.0,
        "io_medium_threshold": 35.0,
        "io_high_threshold": 999.0,
    },
}
_DEFAULT_TIER_THRESHOLDS = _TIER_THRESHOLDS["hdd"]

# Pressure level order for comparisons
_PRESSURE_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def classify_disk_pressure_by_tier(
    *,
    storage_tier: str | None,
    percent: float,
    free_gb: float,
    io_pressure_full_avg10: float,
) -> dict[str, Any]:
    """Classify disk pressure for a storage pool, tier-aware.

    NVMe pools treat IO saturation as the primary pressure signal; HDD/SSD
    pools treat capacity as the primary signal.

    Returns a dict with:
      - ``tier`` — the normalised tier name used
      - ``status`` — one of ``low`` / ``medium`` / ``high`` / ``critical``
      - ``binding_constraint`` — ``"io"``, ``"capacity"``, or ``"none"``
      - ``capacity_warn_percent``, ``capacity_critical_percent``, ``io_high_threshold``
        (thresholds used, for transparency)
      - ``reasons`` — human-readable list of triggered constraints
    """
    normalised_tier = (storage_tier or "").strip().lower()
    thresholds = _TIER_THRESHOLDS.get(normalised_tier, _DEFAULT_TIER_THRESHOLDS)

    cap_warn = thresholds["capacity_warn_percent"]
    cap_crit = thresholds["capacity_critical_percent"]
    io_medium = thresholds["io_medium_threshold"]
    io_high = thresholds["io_high_threshold"]

    status = "low"
    binding_constraint = "none"
    reasons: list[str] = []

    # --- Capacity pressure ---
    cap_status = "low"
    if percent >= cap_crit:
        cap_status = "critical"
        reasons.append(f"capacity {percent:.1f}% >= critical threshold {cap_crit:g}%")
    elif percent >= cap_warn:
        cap_status = "medium"
        reasons.append(f"capacity {percent:.1f}% >= warn threshold {cap_warn:g}%")
    elif free_gb <= DISK_MIN_FREE_GB:
        cap_status = "medium"
        reasons.append(f"free space {free_gb:.1f} GB <= minimum {DISK_MIN_FREE_GB:g} GB")

    # --- IO pressure ---
    io_status = "low"
    if normalised_tier == "nvme":
        # NVMe: IO is the primary constraint, can escalate all the way to critical.
        if io_pressure_full_avg10 >= io_high:
            io_status = "high"
            reasons.append(f"IO saturation full.avg10={io_pressure_full_avg10:.1f} >= {io_high:g}")
        elif io_pressure_full_avg10 >= io_medium:
            io_status = "medium"
            reasons.append(f"IO pressure full.avg10={io_pressure_full_avg10:.1f} >= {io_medium:g}")
    else:
        # HDD/SSD: IO can raise pressure to medium at most.
        if io_pressure_full_avg10 >= io_medium:
            io_status = "medium"
            reasons.append(f"IO pressure full.avg10={io_pressure_full_avg10:.1f} >= {io_medium:g}")

    # Combine: take the worst of capacity vs IO, then apply tier logic.
    cap_rank = _PRESSURE_RANK.get(cap_status, 0)
    io_rank = _PRESSURE_RANK.get(io_status, 0)

    if cap_rank >= io_rank:
        status = cap_status
        binding_constraint = "capacity" if cap_status != "low" else "none"
    else:
        status = io_status
        binding_constraint = "io" if io_status != "low" else "none"

    # NVMe: critical capacity AND high IO => critical
    if normalised_tier == "nvme" and cap_status == "critical" and io_status in ("high", "critical"):
        status = "critical"
        binding_constraint = "io"

    # Map old 4-level scheme: HDD status at cap_crit is "critical" already
    # but we use "high" as a discrete level for NVMe when only IO triggers.
    # Ensure "high" is only emitted for NVMe IO pressure with healthy-ish capacity.
    if status == "high" and normalised_tier != "nvme":
        status = "critical"  # non-NVMe tiers go straight to critical at high IO

    return {
        "tier": normalised_tier or "hdd",
        "status": status,
        "binding_constraint": binding_constraint,
        "reasons": reasons,
        "capacity_warn_percent": cap_warn,
        "capacity_critical_percent": cap_crit,
        "io_high_threshold": io_high,
    }


def get_overall_disk_pressure(pools: list[dict[str, Any]]) -> dict[str, Any]:
    """Get the overall disk pressure based on the most constrained pool."""
    if not pools:
        return {
            "status": "healthy",
            "path": "/",
            "total_gb": 0.0,
            "used_gb": 0.0,
            "free_gb": 0.0,
            "percent": 0.0,
            "warn_percent": DISK_WARN_PERCENT,
            "critical_percent": DISK_CRITICAL_PERCENT,
            "min_free_gb": DISK_MIN_FREE_GB,
            "reasons": [],
            "recommendations": [],
        }

    def status_rank(status: str) -> int:
        if status == "critical":
            return 3
        if status == "warning":
            return 2
        return 1

    most_constrained = pools[0]
    for pool in pools[1:]:
        rank_curr = status_rank(pool["pressure"]["status"])
        rank_best = status_rank(most_constrained["pressure"]["status"])
        if rank_curr > rank_best:
            most_constrained = pool
        elif rank_curr == rank_best:
            if pool["percent"] > most_constrained["percent"]:
                most_constrained = pool

    return most_constrained["pressure"]


async def get_system_metrics_snapshot(
    runner_limit: int | None = None,
    boot_time: float | None = None,
    host_memory_gb: float | None = None,
    get_runner_capacity_snapshot: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Real-time system resource metrics."""

    def _sync() -> dict[str, Any]:
        cpu_freq = psutil.cpu_freq()
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk_path = str(RUNNER_BASE_DIR) if RUNNER_BASE_DIR.exists() else "/"

        net = psutil.net_io_counters()
        per_cpu = psutil.cpu_percent(interval=0, percpu=True)
        current_cpu = psutil.cpu_percent(interval=0)
        host_resources = _windows_host_resource_snapshot()
        if host_resources:
            current_cpu = host_resources["cpu_percent"]
        _cpu_history.append(current_cpu)
        cpu_avg_1m = round(sum(_cpu_history) / len(_cpu_history), 1) if _cpu_history else current_cpu

        try:
            uptime_seconds = time.time() - psutil.boot_time()
        except OSError:
            uptime_seconds = 0
        ref_boot = boot_time or BOOT_TIME
        dashboard_uptime = time.time() - ref_boot

        try:
            load_avg = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
        except OSError:
            load_avg = (0.0, 0.0, 0.0)

        gpu_info = get_gpu_info()
        hardware_specs = get_local_hardware_specs(gpu_info)

        # Storage Pools and Capacity Disk Pressure
        pools = get_storage_pools()
        overall_pressure = get_overall_disk_pressure(pools)
        local_pool = pools[0]

        windows_host = None
        is_wsl = "microsoft" in platform.uname().release.lower()
        if is_wsl:
            for pool in pools:
                if pool.get("vhdx_path") is None:
                    windows_host = {
                        "path": pool["backing_disk_path"],
                        "total_gb": pool["total_gb"],
                        "used_gb": pool["used_gb"],
                        "free_gb": pool["free_gb"],
                        "percent": pool["percent"],
                        "pressure": pool["pressure"],
                    }

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
                "load_avg_1m": round(load_avg[0], 2),
                "load_avg_5m": round(load_avg[1], 2),
                "load_avg_15m": round(load_avg[2], 2),
            },
            "memory": {
                "host_total_gb": host_memory_gb or HOST_MEMORY_GB,
                "wsl_total_gb": round(mem.total / (1024**3), 1),
                "total_gb": (
                    host_resources["memory_total_gb"]
                    if host_resources
                    else (host_memory_gb or HOST_MEMORY_GB) or round(mem.total / (1024**3), 1)
                ),
                "used_gb": host_resources["memory_used_gb"] if host_resources else round(mem.used / (1024**3), 1),
                "available_gb": (
                    host_resources["memory_available_gb"] if host_resources else round(mem.available / (1024**3), 1)
                ),
                "percent": host_resources["memory_percent"] if host_resources else mem.percent,
                "source": "windows-host" if host_resources else "wsl",
                "swap_total_gb": round(swap.total / (1024**3), 1),
                "swap_used_gb": round(swap.used / (1024**3), 1),
                "swap_percent": swap.percent,
            },
            "disk": {
                "path": disk_path,
                "total_gb": local_pool["total_gb"],
                "used_gb": local_pool["used_gb"],
                "free_gb": local_pool["free_gb"],
                "percent": local_pool["percent"],
                "pressure": overall_pressure,
                "pools": pools,
                "windows_host": windows_host,
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
            "runner_capacity": get_runner_capacity_snapshot() if get_runner_capacity_snapshot else {},
            "io_pressure": get_io_pressure_snapshot(),
        }

        return metrics

    return await asyncio.to_thread(_sync)


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
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except TimeoutError:
        # Issue #939d: kill THEN reap. A bare proc.kill() without awaiting wait()
        # leaks a zombie/transport; kill() also raises ProcessLookupError if the
        # process already exited between the timeout and the kill.
        if "proc" in locals():
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(ProcessLookupError):
                await proc.wait()
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
