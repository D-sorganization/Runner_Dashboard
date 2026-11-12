"""Cached low-overhead host metrics (#4309).

A background ``host-metrics-probe`` samples about every 30 s and atomically writes
``host_metrics.json`` under ``RUNNER_DASHBOARD_STATE_DIR``. Dashboard, fleet
status, and ``runner-scheduler`` read that file only — they never fork
``powershell.exe`` on the request or scheduler tick path.

Missing or stale cache is reported as ``unknown``; consumers must not treat WSL
psutil figures as a healthy Windows host.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from dashboard_config import HOSTNAME, RUNNER_BASE_DIR
from host_metrics import normalize_host_key

logger = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = 1
CACHE_BASENAME = "host_metrics.json"
DEFAULT_SAMPLE_INTERVAL_S = 30.0
DEFAULT_STALE_AFTER_S = float(os.environ.get("RUNNER_HOST_METRICS_STALE_S", "90"))

CacheStatus = Literal["fresh", "stale", "missing"]


def _default_state_dir() -> Path:
    override = os.environ.get("RUNNER_DASHBOARD_STATE_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".config" / "runner-dashboard"


def cache_file_path() -> Path:
    """Return the canonical host-metrics cache path for this node."""
    return _default_state_dir() / CACHE_BASENAME


def _parse_sampled_at(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _cache_age_seconds(sampled_at: datetime) -> float:
    return max(0.0, (datetime.now(UTC) - sampled_at).total_seconds())


@dataclass(frozen=True)
class CacheReadResult:
    status: CacheStatus
    payload: dict[str, Any] | None
    age_seconds: float | None
    path: Path

    @property
    def is_usable(self) -> bool:
        return self.status == "fresh" and isinstance(self.payload, dict)


def read_cache(*, stale_after_s: float = DEFAULT_STALE_AFTER_S) -> CacheReadResult:
    """Load the on-disk host metrics cache and classify freshness."""
    path = cache_file_path()
    if not path.is_file():
        return CacheReadResult(status="missing", payload=None, age_seconds=None, path=path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("host_metrics cache unreadable: %s", exc)
        return CacheReadResult(status="missing", payload=None, age_seconds=None, path=path)
    if not isinstance(payload, dict):
        return CacheReadResult(status="missing", payload=None, age_seconds=None, path=path)
    sampled_at = _parse_sampled_at(payload.get("sampled_at"))
    if sampled_at is None:
        return CacheReadResult(status="stale", payload=payload, age_seconds=None, path=path)
    age = _cache_age_seconds(sampled_at)
    if age > stale_after_s:
        return CacheReadResult(status="stale", payload=payload, age_seconds=age, path=path)
    return CacheReadResult(status="fresh", payload=payload, age_seconds=age, path=path)


def write_cache_atomic(payload: dict[str, Any], *, path: Path | None = None) -> Path:
    """Persist ``payload`` with a tmp+rename write (probe-only)."""
    target = path or cache_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


def _read_proc_mem_available_gb(proc_root: Path | None = None) -> float | None:
    root = proc_root or Path("/proc")
    try:
        for line in (root / "meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        return None
    return None


def _read_proc_wsl_memory_used_gb(proc_root: Path | None = None) -> float | None:
    root = proc_root or Path("/proc")
    try:
        mem_total_kb = None
        mem_available_kb = None
        for line in (root / "meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                mem_total_kb = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                mem_available_kb = int(line.split()[1])
        if mem_total_kb is None or mem_available_kb is None:
            return None
        used_kb = mem_total_kb - mem_available_kb
        return round(used_kb / (1024 * 1024), 2)
    except (OSError, ValueError, IndexError):
        return None


def _pressure_some_avg60(resource: str, proc_root: Path | None = None) -> float | None:
    root = proc_root or Path("/proc")
    try:
        for line in (root / "pressure" / resource).read_text(encoding="utf-8").splitlines():
            if line.startswith("some "):
                fields = dict(part.split("=", 1) for part in line.split()[1:])
                return float(fields["avg60"])
    except (OSError, ValueError, KeyError):
        return None
    return None


def _pressure_io_snapshot(proc_root: Path | None = None) -> dict[str, Any] | None:
    root = proc_root or Path("/proc")
    path = root / "pressure" / "io"
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None
    result: dict[str, Any] = {}
    for line in content.strip().split("\n"):
        parts = line.split()
        if not parts:
            continue
        kind = parts[0]
        metrics: dict[str, float] = {}
        for part in parts[1:]:
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            if key in {"avg10", "avg60", "avg300"}:
                metrics[key] = float(value)
        if metrics:
            result[kind] = metrics
    return result or None


def _windows_host_probe_json() -> dict[str, Any] | None:
    """One PowerShell fork for live Windows CPU/RAM/vmmem (probe path only)."""
    from system_utils import _run_windows_powershell_json  # noqa: PLC0415

    if "microsoft" not in platform.uname().release.lower():
        return None

    command = r"""
$cpu = (Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'").PercentProcessorTime
$os = Get-CimInstance Win32_OperatingSystem
$total = [double]$os.TotalVisibleMemorySize * 1KB
$free = [double]$os.FreePhysicalMemory * 1KB
$used = $total - $free
$vmmem = Get-Process -Name 'vmmemWSL' -ErrorAction SilentlyContinue | Select-Object -First 1
$vmmem_gb = if ($vmmem) { [math]::Round($vmmem.WorkingSet64 / 1GB, 2) } else { $null }
[pscustomobject]@{
  cpu_percent = [math]::Round([double]$cpu, 1)
  memory_total_gb = [math]::Round($total / 1GB, 1)
  memory_used_gb = [math]::Round($used / 1GB, 1)
  memory_available_gb = [math]::Round($free / 1GB, 1)
  memory_percent = [math]::Round(($used / $total) * 100, 1)
  vmmemwsl_working_set_gb = $vmmem_gb
} | ConvertTo-Json -Compress
"""
    data = _run_windows_powershell_json(command, deadline=time.monotonic() + 8.0)
    if not isinstance(data, dict):
        return None
    try:
        return {
            "cpu_percent": float(data["cpu_percent"]),
            "memory_total_gb": float(data["memory_total_gb"]),
            "memory_used_gb": float(data["memory_used_gb"]),
            "memory_available_gb": float(data["memory_available_gb"]),
            "memory_percent": float(data["memory_percent"]),
            "vmmemwsl_working_set_gb": (
                float(data["vmmemwsl_working_set_gb"]) if data.get("vmmemwsl_working_set_gb") is not None else None
            ),
        }
    except (TypeError, ValueError, KeyError):
        return None


def _disk_at(path: str) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return {"path": path, "status": "unavailable"}
    total_gb = round(usage.total / 1e9, 1)
    used_gb = round(usage.used / 1e9, 1)
    free_gb = round(usage.free / 1e9, 1)
    percent = round(usage.used / usage.total * 100, 1) if usage.total else 0.0
    return {
        "path": path,
        "total_gb": total_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "percent": percent,
    }


def _disk_snapshots() -> dict[str, Any]:
    paths = []
    primary = str(RUNNER_BASE_DIR) if RUNNER_BASE_DIR.exists() else "/"
    paths.append(primary)
    if "microsoft" in platform.uname().release.lower():
        paths.append("/mnt/c")
    disks: dict[str, Any] = {}
    for path in dict.fromkeys(paths):
        disks[path] = _disk_at(path)
    return {"primary_path": primary, "by_path": disks}


def sample_host_metrics(*, proc_root: Path | None = None) -> dict[str, Any]:
    """Collect one probe sample (expensive path; background timer only)."""
    windows = _windows_host_probe_json()
    wsl_block: dict[str, Any] = {
        "mem_available_gb": _read_proc_mem_available_gb(proc_root),
        "memory_used_gb": _read_proc_wsl_memory_used_gb(proc_root),
        "psi": {
            "cpu_some_avg60": _pressure_some_avg60("cpu", proc_root),
            "memory_some_avg60": _pressure_some_avg60("memory", proc_root),
        },
        "io_pressure": _pressure_io_snapshot(proc_root),
    }
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "sampled_at": datetime.now(UTC).isoformat(),
        "hostname": HOSTNAME or platform.node(),
        "windows": windows,
        "wsl": wsl_block,
        "disk": _disk_snapshots(),
    }


def cache_meta_for_api(result: CacheReadResult) -> dict[str, Any]:
    """Telemetry block embedded in ``/api/system`` for operators and #4310."""
    base: dict[str, Any] = {
        "status": result.status if result.status != "missing" else "unknown",
        "path": str(result.path),
        "stale_after_s": DEFAULT_STALE_AFTER_S,
        "sample_interval_s": DEFAULT_SAMPLE_INTERVAL_S,
    }
    if result.age_seconds is not None:
        base["age_seconds"] = round(result.age_seconds, 1)
    if result.payload and result.status == "stale":
        base["reason"] = "cache older than stale threshold"
    if result.status == "missing":
        base["reason"] = "cache file missing or unreadable"
    return base


def _expects_windows_host_metrics() -> bool:
    return "microsoft" in platform.uname().release.lower()


def _windows_metrics_complete(block: object) -> bool:
    if not isinstance(block, dict):
        return False
    cpu = block.get("cpu_percent")
    mem = block.get("memory_percent")
    return isinstance(cpu, (int, float)) and isinstance(mem, (int, float))


def windows_host_from_cache() -> dict[str, float] | None:
    """Fresh Windows host CPU/RAM for legacy ``_windows_host_resource_snapshot`` shape."""
    result = read_cache()
    if not result.is_usable:
        return None
    payload = result.payload or {}
    windows = payload.get("windows")
    if not isinstance(windows, dict):
        return None
    try:
        return {
            "cpu_percent": float(windows["cpu_percent"]),
            "memory_total_gb": float(windows["memory_total_gb"]),
            "memory_used_gb": float(windows["memory_used_gb"]),
            "memory_available_gb": float(windows["memory_available_gb"]),
            "memory_percent": float(windows["memory_percent"]),
        }
    except (TypeError, ValueError, KeyError):
        return None


def metrics_view_for_gauge(result: CacheReadResult) -> dict[str, Any] | None:
    """Shape consumed by ``host_health.build_host_health_gauge`` / ``HostMetricsProvider``."""
    if not result.is_usable:
        return None
    payload = result.payload or {}
    raw_windows = payload.get("windows")
    windows: dict[str, Any] = raw_windows if isinstance(raw_windows, dict) else {}
    raw_wsl = payload.get("wsl")
    wsl: dict[str, Any] = raw_wsl if isinstance(raw_wsl, dict) else {}
    windows_ok = _windows_metrics_complete(windows)
    if _expects_windows_host_metrics() and not windows_ok:
        return {
            "status": "degraded",
            "source": "cache",
            "sampled_at": payload.get("sampled_at"),
            "reason": "windows host metrics missing from fresh cache (probe failed or incomplete)",
            "cpu": {"percent": None},
            "memory": {
                "percent": None,
                "windows_percent": None,
                "wsl_used_gb": wsl.get("memory_used_gb"),
                "vmmemwsl_working_set_gb": None,
            },
            "wsl": wsl,
            "disk": payload.get("disk"),
        }
    cpu = windows.get("cpu_percent")
    mem = windows.get("memory_percent")
    view: dict[str, Any] = {
        "status": "ok",
        "source": "cache",
        "sampled_at": payload.get("sampled_at"),
        "cpu": {"percent": float(cpu) if isinstance(cpu, (int, float)) else None},
        "memory": {
            "percent": float(mem) if isinstance(mem, (int, float)) else None,
            "windows_percent": float(mem) if isinstance(mem, (int, float)) else None,
            "wsl_used_gb": wsl.get("memory_used_gb"),
            "vmmemwsl_working_set_gb": windows.get("vmmemwsl_working_set_gb"),
        },
        "wsl": wsl,
        "disk": payload.get("disk"),
    }
    return view


class CachedHostMetricsProvider:
    """#4309 provider wired from ``host_metrics.get_host_metrics_provider``."""

    def read_cached(self, host: str) -> dict[str, Any] | None:
        local = normalize_host_key(HOSTNAME or platform.node())
        if normalize_host_key(host) != local:
            return None
        result = read_cache()
        if not result.is_usable:
            return {"status": "unknown", "source": "cache", "cache_status": result.status}
        return metrics_view_for_gauge(result)


def scheduler_cached_wsl_mem_available_gb() -> float | None:
    result = read_cache()
    if not result.is_usable:
        return None
    wsl = (result.payload or {}).get("wsl")
    if not isinstance(wsl, dict):
        return None
    value = wsl.get("mem_available_gb")
    return float(value) if isinstance(value, (int, float)) else None


def scheduler_cached_memory_psi_avg60() -> float | None:
    result = read_cache()
    if not result.is_usable:
        return None
    wsl = (result.payload or {}).get("wsl")
    if not isinstance(wsl, dict):
        return None
    psi = wsl.get("psi")
    if not isinstance(psi, dict):
        return None
    value = psi.get("memory_some_avg60")
    return float(value) if isinstance(value, (int, float)) else None


def scheduler_cached_disk_free_gb(disk_path: str) -> float | None:
    result = read_cache()
    if not result.is_usable:
        return None
    disk = (result.payload or {}).get("disk")
    if not isinstance(disk, dict):
        return None
    by_path = disk.get("by_path")
    if isinstance(by_path, dict) and disk_path in by_path:
        entry = by_path[disk_path]
        if isinstance(entry, dict):
            value = entry.get("free_gb")
            return float(value) if isinstance(value, (int, float)) else None
    return None
