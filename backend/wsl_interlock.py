"""Active-job interlock and host teardown safeguards (Issue #1067).

Guards against destructive host/WSL recovery operations while CI jobs are active:
1. Detects live Runner.Worker processes across the host and runner workdirs.
2. Evaluates teardown safety with support for explicit emergency overrides.
3. Records structured audit logs before any planned or attempted host reset.
4. Surfaces boot times, recovery reasons, and interrupted runner telemetry.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore

log = logging.getLogger("dashboard.wsl_interlock")

# Python 3.11+ UTC
UTC = getattr(datetime, "UTC", timezone.utc)  # noqa: UP017


def count_active_runner_workers() -> int:
    """Return the total number of running Runner.Worker processes on this machine."""
    if psutil is None:
        return 0
    count = 0
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            name = proc.info.get("name") or ""
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if "Runner.Worker" in name or "Runner.Worker" in cmdline:
                count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return count


def check_wsl_teardown_safety(
    emergency_override: bool = False,
    reason: str = "unresponsive_distro",
    initiator: str = "wsl-keepalive-watchdog",
    active_worker_count: int | None = None,
) -> dict[str, Any]:
    """Interlock: determine whether a destructive WSL shutdown/reset is safe."""
    active_count = count_active_runner_workers() if active_worker_count is None else active_worker_count
    allowed = (active_count == 0) or emergency_override

    if active_count > 0 and not emergency_override:
        decision_reason = f"active_runner_workers_running ({active_count} active)"
    elif active_count > 0 and emergency_override:
        decision_reason = f"emergency_override_used ({active_count} active workers bypassed)"
    else:
        decision_reason = "no_active_workers"

    decision = {
        "allowed": allowed,
        "active_workers": active_count,
        "emergency_override": emergency_override,
        "reason": reason,
        "decision_reason": decision_reason,
        "initiator": initiator,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    return decision


def record_wsl_teardown_audit(
    initiator: str,
    reason: str,
    active_workers: int,
    allowed: bool,
    override_used: bool = False,
    log_dir: str | Path | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a structured JSONL audit entry for a host reset / teardown decision."""
    now_iso = datetime.now(UTC).isoformat()
    entry: dict[str, Any] = {
        "timestamp": now_iso,
        "initiator": initiator,
        "reason": reason,
        "active_workers": active_workers,
        "allowed": allowed,
        "override_used": override_used,
    }
    if extra:
        entry.update(extra)

    if log_dir is None:
        appdata = os.environ.get("LOCALAPPDATA")
        home = os.environ.get("HOME")
        if appdata:
            log_dir = Path(appdata) / "runner-dashboard"
        elif home:
            log_dir = Path(home) / ".runner-dashboard"
        else:
            log_dir = Path("/tmp/runner-dashboard")

    log_path = Path(log_dir) / "wsl-teardown-audit.jsonl"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as exc:
        log.warning("Failed to write WSL teardown audit log to %s: %s", log_path, exc)

    return entry


def get_last_wsl_boot_time() -> str | None:
    """Return the ISO timestamp of the last WSL / host boot time."""
    if psutil is not None:
        try:
            boot_ts = psutil.boot_time()
            if boot_ts > 0:
                return datetime.fromtimestamp(boot_ts, UTC).isoformat()
        except Exception:
            pass
    return None


def get_wsl_lifecycle_diagnostics(state_file: str | Path | None = None) -> dict[str, Any]:
    """Return telemetry for host recovery and WSL lifecycle."""
    boot_time = get_last_wsl_boot_time()
    last_reason: str | None = None
    interrupted_count: int = 0

    if state_file is None:
        appdata = os.environ.get("LOCALAPPDATA")
        home = os.environ.get("HOME")
        if appdata:
            state_file = Path(appdata) / "runner-dashboard" / "wsl-keepalive-state.json"
        elif home:
            state_file = Path(home) / ".runner-dashboard" / "wsl-keepalive-state.json"
        else:
            state_file = Path("/tmp/runner-dashboard/wsl-keepalive-state.json")

    p = Path(state_file)
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                last_reason = data.get("last_recovery_reason")
                interrupted_count = int(data.get("interrupted_runner_count", 0))
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "last_wsl_boot_time": boot_time,
        "last_recovery_reason": last_reason,
        "interrupted_runner_count": interrupted_count,
        "active_worker_count": count_active_runner_workers(),
    }
