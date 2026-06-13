"""Resource sampling and scheduler integration for the runner auto-scaler.

Provides:
  - _sample()                  — one-shot CPU / memory / load / disk measurement
  - _scheduled_desired_count() — integrate with the runner-scheduler binary
  - _leased_runners()          — read active runner leases from the shared
                                 LeaseManager store (RUNNER_DASHBOARD_CONFIG_DIR)
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from autoscaler_config import (
    RUNNER_BASE_DIR,
    RUNNER_SCHEDULE_CONFIG,
    RUNNER_SCHEDULER_BIN,
    psutil,
)

log = logging.getLogger("runner-autoscaler")
_DEFAULT_RUNNER_SCHEDULER_BIN = RUNNER_SCHEDULER_BIN
_DEFAULT_PSUTIL = psutil
_POWERSHELL_CANDIDATES = (
    "powershell.exe",
    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
)
_PRESSURE_KEYS = ("avg10", "avg60", "avg300", "total")


def _runner_scheduler_bin() -> str:
    if RUNNER_SCHEDULER_BIN != _DEFAULT_RUNNER_SCHEDULER_BIN:
        return RUNNER_SCHEDULER_BIN
    runner_autoscaler = sys.modules.get("runner_autoscaler")
    if runner_autoscaler is not None:
        return str(getattr(runner_autoscaler, "RUNNER_SCHEDULER_BIN", RUNNER_SCHEDULER_BIN))
    return RUNNER_SCHEDULER_BIN


def _psutil_dep() -> Any:
    if psutil is not _DEFAULT_PSUTIL:
        return psutil
    runner_autoscaler = sys.modules.get("runner_autoscaler")
    if runner_autoscaler is not None:
        return getattr(runner_autoscaler, "psutil", psutil)
    return psutil


def _leases_path() -> Path:
    """Resolve the leases.yml path the LeaseManager writer actually uses.

    Issue #932: the autoscaler previously read ``<repo>/config/leases.yml``
    (repo-relative), but the only writer — ``runner_lease.LeaseManager`` —
    persists to ``$RUNNER_DASHBOARD_CONFIG_DIR`` (default
    ``~/.config/runner-dashboard``). Reader and writer must share one path
    helper or lease protection is a permanent no-op. We delegate to
    ``runner_lease._default_config_dir`` so the two can never drift again.
    """
    from runner_lease import _default_config_dir  # noqa: PLC0415

    return _default_config_dir() / "leases.yml"


def _leased_runners() -> set[str]:
    """Return the set of currently-leased runner_ids from the shared leases file.

    Reads the same ``leases.yml`` the LeaseManager writes (issue #932). Each
    record is validated independently: a single malformed entry is skipped and
    logged, but the remaining valid leases are still honoured (issue #937c) —
    one bad record must never disable protection for every leased runner.
    """
    path = _leases_path()
    if not path.exists():
        return set()
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        log.warning("Failed to read leases: %s", exc)
        return set()
    if not data or "leases" not in data:
        return set()

    now = time.time()
    leased: set[str] = set()
    for lease_rec in data["leases"]:
        try:
            runner_id = str(lease_rec["runner_id"])
            expires_at = lease_rec.get("expires_at")
            if expires_at is None or float(expires_at) > now:
                leased.add(runner_id)
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("Skipping malformed lease record %r: %s", lease_rec, exc)
            continue
    return leased


def _scheduled_desired_count(default: int) -> int:
    """Read the schedule service's current desired capacity when installed."""
    scheduler_bin = _runner_scheduler_bin()
    if not os.path.exists(scheduler_bin):
        return default
    try:
        env = os.environ.copy()
        env["RUNNER_SCHEDULE_CONFIG"] = RUNNER_SCHEDULE_CONFIG
        result = subprocess.run(
            [scheduler_bin, "--dry-run", "--json"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("scheduler desired lookup failed: %s", exc)
        return default
    if result.returncode != 0:
        log.debug("scheduler desired lookup failed: %s", result.stderr.strip()[:200])
        return default
    try:
        state = json.loads(result.stdout)
        return max(0, int(state.get("desired", default)))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _windows_host_resource_snapshot() -> tuple[float, float] | None:
    """Return (cpu_percent, memory_percent) for the Windows host from WSL."""
    if "microsoft" not in platform.uname().release.lower():
        return None

    command = r"""
$cpu = (Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'").PercentProcessorTime
$os = Get-CimInstance Win32_OperatingSystem
$total = [double]$os.TotalVisibleMemorySize
$free = [double]$os.FreePhysicalMemory
$used = $total - $free
[pscustomobject]@{
  cpu_percent = [math]::Round([double]$cpu, 1)
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
        return float(data["cpu_percent"]), float(data["memory_percent"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return None


def _parse_pressure_line(line: str) -> dict[str, float]:
    """Parse one Linux PSI line, for example ``full avg10=65.5 ...``."""
    parts = line.strip().split()
    if not parts:
        raise ValueError("pressure line is empty")
    parsed: dict[str, float] = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, raw = part.split("=", 1)
        if key in _PRESSURE_KEYS:
            parsed[key] = float(raw)
    return parsed


def _io_pressure_snapshot(path: str = "/proc/pressure/io") -> dict[str, float] | None:
    """Return Linux IO pressure stall metrics when available.

    The values are percentages over the kernel's rolling windows. ``full_avg10``
    is the critical ControlTower signal: during recent outages it stayed above
    60%, meaning all runnable work was blocked on I/O for most of the window.
    """
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    pressure: dict[str, float] = {}
    for line in lines:
        if line.startswith("some "):
            parsed = _parse_pressure_line(line)
            pressure.update({f"some_{key}": value for key, value in parsed.items()})
        elif line.startswith("full "):
            parsed = _parse_pressure_line(line)
            pressure.update({f"full_{key}": value for key, value in parsed.items()})
    return pressure or None


def _sample() -> tuple[float, float, float, float, float]:
    """Return (cpu_percent, mem_percent, load_per_core, disk_percent, disk_free_gb)."""
    psutil_mod = _psutil_dep()
    if psutil_mod is None:
        raise RuntimeError("psutil is required for runner-autoscaler")
    cpu = psutil_mod.cpu_percent(interval=1.0)
    mem = psutil_mod.virtual_memory().percent
    host_resources = _windows_host_resource_snapshot()
    if host_resources is not None:
        cpu, mem = host_resources
    try:
        load1 = os.getloadavg()[0]  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        load1 = 0.0
    cores = psutil_mod.cpu_count(logical=True) or 1
    disk_path = RUNNER_BASE_DIR if os.path.exists(RUNNER_BASE_DIR) else "/"
    usage = shutil.disk_usage(disk_path)
    disk_percent = usage.used / usage.total * 100
    disk_free_gb = usage.free / (1024**3)
    return cpu, mem, load1 / cores, disk_percent, disk_free_gb


_last_io_time: dict[str, float] = {}
_last_io_timestamp: dict[str, float] = {}


def _get_disk_io_time(device: str | None = None) -> float:
    """Return the total disk time (busy_time, or read_time + write_time if busy_time not available) in ms."""
    psutil_mod = _psutil_dep()
    if psutil_mod is None:
        return 0.0
    try:
        if device:
            counters = psutil_mod.disk_io_counters(perdisk=True)
            if counters and device in counters:
                c = counters[device]
                if hasattr(c, "busy_time"):
                    return float(c.busy_time)
                return float(c.read_time + c.write_time)
        else:
            c = psutil_mod.disk_io_counters(perdisk=False)
            if c:
                if hasattr(c, "busy_time"):
                    return float(c.busy_time)
                return float(c.read_time + c.write_time)
    except Exception as exc:
        log.debug("Failed to read disk IO counters: %s", exc)
    return 0.0


def get_disk_utilization_percent(device: str) -> float:
    """Calculate the disk utilization percentage since the last sample."""
    global _last_io_time, _last_io_timestamp
    now = time.time()
    curr_io = _get_disk_io_time(device)

    prev_io = _last_io_time.get(device, 0.0)
    prev_ts = _last_io_timestamp.get(device, 0.0)

    _last_io_time[device] = curr_io
    _last_io_timestamp[device] = now

    if prev_ts == 0.0 or now <= prev_ts:
        return 0.0

    delta_io_ms = curr_io - prev_io
    delta_time_ms = (now - prev_ts) * 1000.0

    if delta_time_ms <= 0:
        return 0.0

    percent = (delta_io_ms / delta_time_ms) * 100.0
    return max(0.0, min(100.0, percent))


def _sample_pool_disk(path: str) -> tuple[float, float]:
    """Return (disk_percent, disk_free_gb) for a given path."""
    try:
        resolved_path = Path(path).resolve()
        # Find the first existing parent directory if path doesn't exist yet
        while not resolved_path.exists():
            parent = resolved_path.parent
            if parent == resolved_path:
                break
            resolved_path = parent
        usage = shutil.disk_usage(str(resolved_path))
        disk_percent = usage.used / usage.total * 100
        disk_free_gb = usage.free / (1024**3)
        return disk_percent, disk_free_gb
    except Exception as exc:
        log.warning("Failed to get disk usage for path %s: %s", path, exc)
        return 0.0, 0.0
