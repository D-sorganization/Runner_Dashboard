"""Host memory health: commit %, vmmemWSL vs cap, cache-trim last run, kernel pool.

Fleet memory policy, Repository_Management issue 2071. Every source degrades to
``{"status": "unavailable", "reason": ...}``; nothing is ever faked as zero.
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UNAVAILABLE = "unavailable"
# Alert when the Windows nonpaged pool exceeds this many GB (fleet policy: 3 GB).
NONPAGED_POOL_ALERT_GB = float(os.environ.get("DASHBOARD_NONPAGED_POOL_ALERT_GB", "3.0"))
KERNEL_POOL_TAIL_ROWS = 6
_CSV_RELATIVE = "ProgramData/fleet-metrics/kernel-pool.csv"
_CSV_FIELDS = ("nonpaged_gb", "paged_gb", "system_handles", "free_gb", "commit_gb", "vmmemwsl_gb")
_JOURNAL_TIMEOUT_S = 3.0
_MEM_RE = re.compile(r"^\s*memory\s*=\s*([0-9.]+)\s*([A-Za-z]*)\s*$", re.IGNORECASE | re.MULTILINE)
_UNIT_GB = {
    "": 1 / 1024**3,
    "b": 1 / 1024**3,
    "kb": 1 / 1024**2,
    "mb": 1 / 1024,
    "m": 1 / 1024,
    "gb": 1.0,
    "g": 1.0,
    "tb": 1024.0,
}
_GIB = 1024**3


def unavailable(reason: str) -> dict[str, Any]:
    """Return the canonical degraded section."""
    return {"status": UNAVAILABLE, "reason": reason}


def _num(raw: str | None) -> float | None:
    try:
        value = float(raw) if raw not in (None, "") else None  # type: ignore[arg-type]
    except ValueError:
        return None
    return None if value is None or value != value else value  # reject NaN


def kernel_pool_csv_candidates() -> list[Path]:
    """Candidate CSV paths: env override, native Windows, then WSL mount."""
    override = os.environ.get("FLEET_KERNEL_POOL_CSV")
    paths = [Path(override)] if override else []
    return [*paths, Path("C:/") / _CSV_RELATIVE, Path("/mnt/c") / _CSV_RELATIVE]


def read_kernel_pool_tail(paths: list[Path] | None = None, rows: int = KERNEL_POOL_TAIL_ROWS) -> dict[str, Any]:
    """Return the last ``rows`` kernel-pool samples (oldest first) plus ``latest``."""
    assert rows > 0, "rows must be positive"
    for path in paths if paths is not None else kernel_pool_csv_candidates():
        try:
            if not path.is_file():
                continue
            with path.open(newline="", encoding="utf-8") as handle:
                records = list(csv.DictReader(handle))
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            return unavailable(f"cannot read {path.name}: {exc}")
        samples = [{"time": rec.get("time"), **{k: _num(rec.get(k)) for k in _CSV_FIELDS}} for rec in records[-rows:]]
        if not samples:
            return unavailable(f"{path.name} has no samples")
        return {"status": "ok", "path": str(path), "samples": samples, "latest": samples[-1]}
    return unavailable("kernel-pool.csv not found")


def parse_memory_cap_gb(text: str) -> float | None:
    """Parse the ``memory=`` value of .wslconfig text into GB."""
    match = _MEM_RE.search(text)
    if not match:
        return None
    factor = _UNIT_GB.get(match.group(2).lower())
    value = _num(match.group(1))
    return None if factor is None or value is None else round(value * factor, 2)


def read_wsl_memory_cap_gb() -> float | None:
    """Return the ``memory=`` cap (GB) from .wslconfig, or None when absent."""
    from platform_utils.wsl_paths import _candidate_wslconfig_paths

    for path in _candidate_wslconfig_paths():
        try:
            if path.is_file():
                return parse_memory_cap_gb(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return None


def _commit_limit_free_gb() -> tuple[float, float] | None:
    """(limit_gb, free_gb) of the Windows commit charge, or None off Windows."""
    if os.name == "nt":
        import ctypes

        class _MemStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (n, ctypes.c_ulonglong) for n in ("tp", "ap", "tpf", "apf", "tv", "av", "ae")
            ]

        mem = _MemStatus()
        mem.length = ctypes.sizeof(_MemStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):  # type: ignore[attr-defined]
            return None
        return mem.tpf / _GIB, mem.apf / _GIB
    from system_utils import _run_windows_powershell_json

    data = _run_windows_powershell_json(
        "$o = Get-CimInstance Win32_OperatingSystem; [pscustomobject]@{ limit = $o.TotalVirtualMemorySize; "
        "free = $o.FreeVirtualMemory } | ConvertTo-Json -Compress"
    )
    if not isinstance(data, dict):
        return None
    return float(data["limit"]) / 1024**2, float(data["free"]) / 1024**2


def read_commit(probe: Callable[[], tuple[float, float] | None] = _commit_limit_free_gb) -> dict[str, Any]:
    """Commit charge vs limit from the Windows host (native or via WSL interop)."""
    try:
        result = probe()
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return unavailable(f"commit probe failed: {exc}")
    if result is None:
        return unavailable("Windows commit data not available on this host")
    limit, free = result
    if limit <= 0:
        return unavailable("commit limit unavailable")
    used = limit - free
    return {
        "status": "ok",
        "commit_gb": round(used, 1),
        "limit_gb": round(limit, 1),
        "percent": round(used / limit * 100, 1),
    }


def read_vmmem(kernel_pool: dict[str, Any], cap_gb: float | None) -> dict[str, Any]:
    """vmmemWSL (latest sampled value) against the .wslconfig ``memory=`` cap."""
    latest = kernel_pool.get("latest") if kernel_pool.get("status") == "ok" else None
    used = latest.get("vmmemwsl_gb") if latest else None
    if used is None:
        return unavailable("vmmemWSL not sampled")
    percent = round(used / cap_gb * 100, 1) if cap_gb else None
    return {"status": "ok", "vmmemwsl_gb": used, "cap_gb": cap_gb, "percent_of_cap": percent}


def read_cache_trim_last_run(run: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Most recent ``wsl-cache-trim`` journal entry (it logs only when it trims)."""
    cmd = ["journalctl", "-t", "wsl-cache-trim", "-n", "1", "--no-pager", "-o", "json"]
    try:
        proc = run(cmd, capture_output=True, text=True, timeout=_JOURNAL_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return unavailable(f"journalctl unavailable: {exc}")
    lines = (proc.stdout or "").strip().splitlines()
    if proc.returncode != 0 or not lines:
        return unavailable("no wsl-cache-trim journal entries")
    try:
        entry = json.loads(lines[-1])
        micros = int(entry["__REALTIME_TIMESTAMP"])
    except (ValueError, KeyError, TypeError):
        return unavailable("unparseable journal entry")
    when = datetime.fromtimestamp(micros / 1e6, tz=timezone.utc)  # noqa: UP017
    return {"status": "ok", "last_run": when.isoformat(), "message": str(entry.get("MESSAGE", ""))}


def evaluate_alerts(kernel_pool: dict[str, Any], threshold_gb: float = NONPAGED_POOL_ALERT_GB) -> list[dict[str, Any]]:
    """Alerts for measured values only; unavailable data never alerts."""
    latest = kernel_pool.get("latest") if kernel_pool.get("status") == "ok" else None
    nonpaged = latest.get("nonpaged_gb") if latest else None
    if nonpaged is not None and nonpaged > threshold_gb:
        return [
            {"code": "nonpaged_pool_high", "severity": "warning", "value_gb": nonpaged, "threshold_gb": threshold_gb}
        ]
    return []


def get_host_memory_health() -> dict[str, Any]:
    """Aggregate every source; each degrades independently."""
    kernel_pool = read_kernel_pool_tail()
    sections = {
        "commit": read_commit(),
        "vmmem": read_vmmem(kernel_pool, read_wsl_memory_cap_gb()),
        "cache_trim": read_cache_trim_last_run(),
        "kernel_pool": kernel_pool,
    }
    alerts = evaluate_alerts(kernel_pool)
    if alerts:
        status = "alert"
    elif all(s["status"] == UNAVAILABLE for s in sections.values()):
        status = UNAVAILABLE
    else:
        status = "ok"
    return {"status": status, "nonpaged_pool_alert_gb": NONPAGED_POOL_ALERT_GB, "alerts": alerts, **sections}
