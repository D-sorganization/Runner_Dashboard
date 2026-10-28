#!/usr/bin/env python3
"""Apply a time-of-day GitHub Actions runner capacity schedule."""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time as time_module
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

RUNNER_ROOT = Path(os.environ.get("RUNNER_ROOT", str(Path.home() / "actions-runners")))
CONFIG_PATH = Path(
    os.environ.get(
        "RUNNER_SCHEDULE_CONFIG",
        str(Path.home() / ".config" / "runner-dashboard" / "runner-schedule.json"),
    )
)
STATE_PATH = Path(os.environ.get("RUNNER_SCHEDULER_STATE", "/var/lib/runner-scheduler/state.json"))
RUNNER_BUSY_LOCK_DIR = Path(os.environ.get("RUNNER_BUSY_LOCK_DIR", "/var/run/runner-busy"))
RUNNER_PICKUP_MAX_AGE_SECONDS = int(os.environ.get("RUNNER_PICKUP_MAX_AGE_SECONDS", "30"))
RUNNER_BUSY_LOCK_MAX_AGE_SECONDS = int(os.environ.get("RUNNER_BUSY_LOCK_MAX_AGE_SECONDS", "86400"))
# A Worker log written this recently means a job is (or was just) executing.
RUNNER_DIAG_MAX_AGE_SECONDS = int(os.environ.get("RUNNER_DIAG_MAX_AGE_SECONDS", "120"))
# Pause between the two idle confirmations made immediately before a stop.
STOP_RECHECK_DELAY_SECONDS = float(os.environ.get("RUNNER_STOP_RECHECK_DELAY_SECONDS", "3"))
# Drain watcher (#1929): under sustained load every runner is "busy" at each
# 5-minute tick, so surplus runners are polled at short intervals for the gap
# between two jobs. Budget stays below the tick; 0 disables the watcher.
DRAIN_MAX_SECONDS = float(os.environ.get("RUNNER_DRAIN_MAX_SECONDS", "240"))
DRAIN_POLL_SECONDS = float(os.environ.get("RUNNER_DRAIN_POLL_SECONDS", "2"))
DRAIN_CONFIRM_SECONDS = float(os.environ.get("RUNNER_DRAIN_CONFIRM_SECONDS", "1"))
# A drain stop needs the runner to have stayed between jobs this long (#1973).
# Under a deep queue GitHub hands a just-freed runner its next job within
# seconds, server-side, to the listener's open session; the SIGSTOP freeze
# cannot stop that, so a stop in the gap orphans the job (zero steps, failed
# after 10 min). A runner left idle this long has nothing queued for it.
DRAIN_SETTLE_SECONDS = float(os.environ.get("RUNNER_DRAIN_SETTLE_SECONDS", "30"))
# `systemctl stop` waits for the listener to shut down, which can exceed the
# 30 s run_cmd default; a timeout must not crash the whole run (#1973).
STOP_TIMEOUT_SECONDS = float(os.environ.get("RUNNER_STOP_TIMEOUT_SECONDS", "120"))
# Maintenance hold (#1929 item 3): while this flag exists every tick targets 0
# runners, so a drain is not undone by the next scheduled start.
MAINTENANCE_PATH = Path(os.environ.get("RUNNER_MAINTENANCE_FLAG", "/var/lib/runner-scheduler/maintenance.json"))
MAINTENANCE_DRAIN_TIMEOUT_SECONDS = float(os.environ.get("RUNNER_MAINTENANCE_DRAIN_TIMEOUT_SECONDS", "3600"))
PROC_ROOT = Path("/proc")
CGROUP_ROOT = Path(os.environ.get("RUNNER_CGROUP_ROOT", "/sys/fs/cgroup"))
# After a stop, survivors of KillMode=process get SIGTERM this long before SIGKILL (#2776).
LEFTOVER_TERM_WAIT_SECONDS = float(os.environ.get("RUNNER_LEFTOVER_TERM_WAIT_SECONDS", "10"))
log = logging.getLogger("runner-scheduler")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _resolve_tz(tz_name: str) -> tzinfo:
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError, Exception):
        return UTC


@dataclass
class RunnerUnit:
    num: int
    unit: str
    workdir: Path
    active: bool
    busy: bool


def run_cmd(cmd: list[str], check: bool = False, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=timeout)


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def validate_config(config: dict) -> None:
    if not isinstance(config, dict):
        raise ValueError("schedule config must be an object")
    if not isinstance(config.get("schedules"), list):
        raise ValueError("schedule config must include a schedules list")
    default_count = int(config.get("default_count", 1))
    max_count = int(config.get("max_count", default_count))
    if default_count < 0 or max_count < 0:
        raise ValueError("runner counts must be nonnegative")
    ZoneInfo(config.get("timezone", "America/Los_Angeles"))
    for entry in config["schedules"]:
        if not isinstance(entry, dict):
            raise ValueError("each schedule entry must be an object")
        if int(entry["runners"]) < 0:
            raise ValueError("scheduled runner counts must be nonnegative")
        parse_hhmm(entry["start"])
        parse_hhmm(entry["end"])
        days = entry.get("days", [])
        if not days or any(day not in DAYS for day in days):
            raise ValueError(f"invalid schedule days for {entry.get('name', '<unnamed>')}")
    validate_resource_floor(config.get("resource_floor"))


RESOURCE_FLOOR_NUMERIC_KEYS = (
    "min_disk_free_gb",
    "min_mem_available_gb",
    "max_memory_pressure_avg60",
    "runners",
)


def validate_resource_floor(floor: object) -> None:
    """Optional host-resource floor (#2000); every numeric threshold must be >= 0."""
    if floor is None:
        return
    if not isinstance(floor, dict):
        raise ValueError("resource_floor must be an object")
    for key in RESOURCE_FLOOR_NUMERIC_KEYS:
        if key not in floor:
            continue
        value = floor[key]
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            raise ValueError(f"resource_floor.{key} must be a nonnegative number")
    if "disk_path" in floor and not isinstance(floor["disk_path"], str):
        raise ValueError("resource_floor.disk_path must be a string")


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = json.load(f)
    validate_config(config)
    return config


def schedule_matches(entry: dict, now: datetime) -> bool:
    start = parse_hhmm(entry["start"])
    end = parse_hhmm(entry["end"])
    days = set(entry["days"])
    today = DAYS[now.weekday()]
    yesterday = DAYS[(now.weekday() - 1) % 7]
    current = now.time()
    if start <= end:
        return today in days and start <= current < end
    return (today in days and current >= start) or (yesterday in days and current < end)


def desired_capacity(
    config: dict,
    target_override: int | None = None,
    *,
    target_source: str | None = None,
) -> tuple[int, str]:
    def capped(count: int, reason: str) -> tuple[int, str]:
        maximum = int(config.get("max_count", count))
        if count > maximum:
            return maximum, f"{reason} (capped at max_count={maximum})"
        return count, reason

    if target_override is not None:
        source_desc = f" ({target_source})" if target_source else ""
        return capped(target_override, f"manual-target{source_desc}")
    if not config.get("enabled", True):
        return capped(int(config.get("default_count", 1)), "schedule-disabled")
    tz = _resolve_tz(config.get("timezone", "America/Los_Angeles"))
    now = datetime.now(tz)
    for entry in config["schedules"]:
        if schedule_matches(entry, now):
            return capped(int(entry["runners"]), str(entry.get("name", "scheduled")))
    return capped(int(config.get("default_count", 1)), "default")


def unit_workdir(unit: str) -> Path | None:
    result = run_cmd(["systemctl", "show", unit, "--property=WorkingDirectory", "--value"])
    value = result.stdout.strip()
    return Path(value) if value else None


def unit_active(unit: str) -> bool:
    return run_cmd(["systemctl", "is-active", "--quiet", unit]).returncode == 0


def _recent_path_is_busy(path: Path, max_age_seconds: int) -> bool:
    """Return whether a job marker is fresh, failing closed on unreadable metadata."""
    try:
        modified = path.stat().st_mtime
    except FileNotFoundError:
        return False
    except OSError:
        return True
    age = datetime.now(UTC).timestamp() - modified
    return age <= max_age_seconds


def runner_busy_via_pickup(workdir: Path) -> bool:
    """Detect the listener-to-worker pickup race from the command directory."""
    pickup = workdir / "_work" / "_temp" / "_runner_file_commands"
    return _recent_path_is_busy(pickup, RUNNER_PICKUP_MAX_AGE_SECONDS)


def runner_names(workdir: Path, unit: str | None = None) -> list[str]:
    """Names a job-start hook may key its lockfile on.

    ``job-started.sh`` writes ``${RUNNER_NAME}.lock`` where RUNNER_NAME is the
    *registered* runner name (``.runner`` agentName, also the unit's last dotted
    segment), not the workdir basename. Accept every spelling (#1921).
    """
    names = [workdir.name]
    try:
        # The runner writes .runner with a UTF-8 BOM.
        data = json.loads((workdir / ".runner").read_text(encoding="utf-8-sig"))
        agent = data.get("agentName") if isinstance(data, dict) else None
        if isinstance(agent, str) and agent:
            names.append(agent)
    except (OSError, ValueError):
        pass
    if unit:
        suffix = unit.removesuffix(".service").rsplit(".", 1)[-1]
        if suffix:
            names.append(suffix)
    return list(dict.fromkeys(names))


def _lock_worker_pid_stale(text: str) -> bool:
    """Issue #1987: True only when the lock's worker_pid= is dead or reused.

    Same rule as backend/runner_busy_lock.py::lock_is_stale_by_worker_pid and
    deploy/runner-cleanup.sh::lock_worker_pid_stale (this script is deployed
    standalone, so it cannot import the backend). The legacy pid= is the
    short-lived hook shell and is never consulted; a lock without worker_pid
    is never stale-by-pid and falls back to the age rule.
    """
    value = ""
    for line in text.splitlines():
        key, sep, val = line.strip().partition("=")
        if sep and key == "worker_pid":
            value = val.strip()
            break
    if not value.isdigit() or int(value) <= 0:
        return False
    if not PROC_ROOT.is_dir():
        return False
    pid_dir = PROC_ROOT / value
    if not pid_dir.is_dir():
        return True
    try:
        cmdline = (pid_dir / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return "Runner.Worker" not in cmdline


def runner_busy_via_lock(workdir: Path, unit: str | None = None, *, cleanup_stale: bool = False) -> bool:
    """Detect a job-start hook marker for the runner.

    A fresh lock counts as busy unless its recorded Runner.Worker pid is dead
    or reused (#1987). Stale locks are only unlinked when explicitly requested
    by an applied scheduler operation; previews keep inspection read-only.
    """
    for name in runner_names(workdir, unit):
        lock = RUNNER_BUSY_LOCK_DIR / f"{name}.lock"
        if not _recent_path_is_busy(lock, RUNNER_BUSY_LOCK_MAX_AGE_SECONDS):
            continue
        try:
            content = lock.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return True
        if not _lock_worker_pid_stale(content):
            return True
        if not cleanup_stale:
            continue
        log.info("removing stale runner-busy lock %s (worker_pid dead or not a Runner.Worker)", lock)
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            return True
    return False


def runner_busy_via_diag(workdir: Path) -> bool:
    """Detect a recently written Worker log (job executing, or finished seconds ago)."""
    diag = workdir / "_diag"
    try:
        logs = list(diag.glob("Worker_*.log"))
    except OSError:
        return True
    return any(_recent_path_is_busy(log, RUNNER_DIAG_MAX_AGE_SECONDS) for log in logs)


def _proc_cmdlines() -> list[str]:
    """Command lines of all processes, read from /proc (independent of ps/tty width)."""
    lines: list[str] = []
    try:
        entries = list(PROC_ROOT.iterdir())
    except OSError:
        return lines
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue  # process exited mid-scan
        if raw:
            lines.append(raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip())
    return lines


def runner_busy_via_process(workdir: Path) -> bool:
    """Detect legacy, self-updated, or reparented workers from the global table."""
    try:
        result = run_cmd(["ps", "-eo", "args=", "-ww"])
    except (OSError, subprocess.SubprocessError):
        return True
    if result.returncode != 0:
        return True
    normalized_root = str(workdir).replace("\\", "/")
    pattern = re.compile(rf"{re.escape(normalized_root)}/bin(?:\.[^/\s]+)?/Runner\.Worker(?:\s|$)")
    lines = result.stdout.splitlines() + _proc_cmdlines()
    return any(pattern.search(line.replace("\\", "/")) is not None for line in lines)


def runner_busy(workdir: Path, unit: str | None = None, *, cleanup_stale: bool = False) -> bool:
    """Return busy when any independent job signal is positive or unreadable."""
    return (
        runner_busy_via_pickup(workdir)
        or runner_busy_via_lock(workdir, unit, cleanup_stale=cleanup_stale)
        or runner_busy_via_diag(workdir)
        or runner_busy_via_process(workdir)
    )


def runner_num(unit: str, workdir: Path | None) -> int | None:
    if workdir is not None:
        match = re.search(r"runner-(\d+)$", str(workdir))
        if match:
            return int(match.group(1))
    match = re.search(r"-(\d+)\.service$", unit)
    if match:
        return int(match.group(1))
    return None


def list_units(*, cleanup_stale: bool = False) -> list[RunnerUnit]:
    result = run_cmd(["systemctl", "list-unit-files", "--type=service", "--no-legend"])
    units: list[RunnerUnit] = []
    for line in result.stdout.splitlines():
        name = line.split()[0] if line.strip() else ""
        if not name.startswith("actions.runner.") or not name.endswith(".service"):
            continue
        workdir = unit_workdir(name)
        num = runner_num(name, workdir)
        if num is None or workdir is None:
            continue
        if not str(workdir).startswith(str(RUNNER_ROOT / "runner-")):
            continue
        active = unit_active(name)
        units.append(
            RunnerUnit(
                num=num,
                unit=name,
                workdir=workdir,
                active=active,
                busy=runner_busy(workdir, unit=name, cleanup_stale=cleanup_stale),
            )
        )
    return sorted(units, key=lambda item: item.num)


def _confirmed_idle(unit: RunnerUnit, settle: bool, *, cleanup_stale: bool = False) -> bool:
    if runner_busy(unit.workdir, unit=unit.unit, cleanup_stale=cleanup_stale):
        return False
    if settle and STOP_RECHECK_DELAY_SECONDS > 0:
        time_module.sleep(STOP_RECHECK_DELAY_SECONDS)
        return not runner_busy(unit.workdir, unit=unit.unit, cleanup_stale=cleanup_stale)
    return True


def _worker_absent(unit: RunnerUnit, *, cleanup_stale: bool = False) -> bool:
    """True when neither a Runner.Worker process nor a job lock exists for the runner."""
    return not (
        runner_busy_via_process(unit.workdir)
        or runner_busy_via_lock(unit.workdir, unit.unit, cleanup_stale=cleanup_stale)
    )


def _signal_pid(pid: int, sig: str) -> None:
    try:
        os.kill(pid, getattr(signal, sig))
    except OSError:
        pass  # already gone


def _unit_cgroup_pids(unit: str) -> list[int]:
    """PIDs still in the unit's cgroup (KillMode=process leaves children behind)."""
    group = run_cmd(["systemctl", "show", unit, "-p", "ControlGroup", "--value"]).stdout.strip()
    if not group.startswith("/"):
        return []
    try:
        text = (CGROUP_ROOT / group.lstrip("/") / "cgroup.procs").read_text(encoding="utf-8")
    except OSError:
        return []
    return [int(item) for item in text.split() if item.isdigit()]


def _reap_leftover_processes(unit: str) -> None:
    """Thaw and terminate children a stopped unit left frozen in its cgroup (#2776)."""
    pids = _unit_cgroup_pids(unit)
    if not pids:
        return
    log.warning("%s stopped but %d process(es) remain in its cgroup; terminating", unit, len(pids))
    for sig in ("SIGCONT", "SIGTERM"):
        for pid in pids:
            _signal_pid(pid, sig)
    deadline = time_module.monotonic() + LEFTOVER_TERM_WAIT_SECONDS
    while time_module.monotonic() < deadline:
        pids = _unit_cgroup_pids(unit)
        if not pids:
            return
        time_module.sleep(min(0.5, LEFTOVER_TERM_WAIT_SECONDS))
    pids = _unit_cgroup_pids(unit)
    for pid in pids:
        _signal_pid(pid, "SIGKILL")
    if pids:
        log.warning("%s: SIGKILLed %d process(es) that ignored SIGTERM", unit, len(pids))


def stop_runner_safely(unit: RunnerUnit) -> bool:
    """Stop an idle runner without a window in which a job can be assigned (#1929).

    Checking idle and then stopping leaves milliseconds in which the listener can
    accept a job. Freeze the whole unit first (a stopped listener cannot pick up or
    fork a job), re-verify no Worker exists, and only then submit the stop.
    KillMode=mixed resumes only the main process; explicitly queue SIGTERM for
    every frozen child before SIGCONT so none can accept work when resumed.
    A job seen after the freeze vetoes the stop and thaws the unit. Errors also
    thaw it. Fails closed if the freeze fails.
    """
    if run_cmd(["systemctl", "kill", "--kill-who=all", "--signal=SIGSTOP", unit.unit]).returncode != 0:
        log.warning("could not freeze %s; not stopping it", unit.unit)
        return False
    stopped = False
    resumed = False
    try:
        if not _worker_absent(unit, cleanup_stale=True):
            return False
        if run_cmd(["systemctl", "kill", "--kill-who=all", "--signal=SIGTERM", unit.unit]).returncode != 0:
            # An external stop may have completed while we checked the unit.
            stopped = (
                run_cmd(["systemctl", "show", unit.unit, "-p", "ActiveState", "--value"]).stdout.strip() == "inactive"
            )
            if stopped:
                return True
            log.warning("could not signal stopping children of %s", unit.unit)
            return False
        # All processes are still frozen: SIGTERM remains pending until the
        # stop job is queued. systemd may then resume only the main process;
        # resume its children too, with termination already pending.
        if run_cmd(["systemctl", "--no-block", "stop", unit.unit]).returncode != 0:
            return False
        resumed = run_cmd(["systemctl", "kill", "--kill-who=all", "--signal=SIGCONT", unit.unit]).returncode == 0
        try:
            stopped = run_cmd(["systemctl", "stop", unit.unit], timeout=STOP_TIMEOUT_SECONDS).returncode == 0
        except subprocess.TimeoutExpired:
            # The stop was issued; it counts once the unit is no longer active.
            stopped = not unit_active(unit.unit)
            log.warning("systemctl stop %s timed out; unit %s", unit.unit, "going down" if stopped else "still active")
    finally:
        if not stopped and not resumed:
            # Every path except a successful stop must leave the unit running.
            run_cmd(["systemctl", "kill", "--kill-who=all", "--signal=SIGCONT", unit.unit])
    if stopped:
        _reap_leftover_processes(unit.unit)
        run_cmd(["systemctl", "reset-failed", unit.unit])
    return stopped


def apply_capacity(units: list[RunnerUnit], desired: int, dry_run: bool) -> list[dict]:
    if [u.num for u in units] != sorted(u.num for u in units):
        raise ValueError("runner units must be sorted by slot number")
    actions: list[dict] = []
    desired = max(0, min(desired, len(units)))
    active = [unit for unit in units if unit.active]
    if len(active) < desired:
        for unit in [unit for unit in units if not unit.active and not unit.busy][: desired - len(active)]:
            actions.append({"runner": unit.num, "unit": unit.unit, "action": "start"})
            if not dry_run:
                run_cmd(["systemctl", "start", unit.unit])
    if (needed := len(active) - desired) > 0:
        stopped = 0
        for unit in sorted((u for u in active if not u.busy), key=lambda item: item.num, reverse=True):
            if stopped >= needed:
                break
            # The snapshot is seconds old (list_units issues several systemctl
            # calls per unit); a job may have been assigned since. Confirm idle
            # twice, spaced out, immediately before killing the listener (#1921).
            if not _confirmed_idle(unit, settle=not dry_run, cleanup_stale=not dry_run):
                continue
            if not dry_run and not stop_runner_safely(unit):
                continue
            actions.append({"runner": unit.num, "unit": unit.unit, "action": "stop"})
            stopped += 1
    if len(active) == desired:
        missing = next((u for u in units[:desired] if not u.active and not u.busy), None)
        surplus = next((u for u in reversed(active) if u not in units[:desired] and not u.busy), None)
        if missing and surplus and _confirmed_idle(surplus, not dry_run, cleanup_stale=not dry_run):
            if dry_run or run_cmd(["systemctl", "start", missing.unit]).returncode == 0:
                actions.append({"runner": missing.num, "unit": missing.unit, "action": "start"})
                if dry_run or stop_runner_safely(surplus):
                    actions.append({"runner": surplus.num, "unit": surplus.unit, "action": "stop"})
    return actions


def _between_jobs(unit: RunnerUnit) -> bool:
    """True when the runner has no Worker process and no job lock, on two looks.

    The tick-time ``runner_busy`` also counts the pickup and Worker-log signals,
    which stay positive for 30-120 s after a job ends. Under a deep queue the
    next job starts well inside that window, so they would veto every drain.
    The Worker process is the authoritative "job is executing" signal; the
    second look, spaced ``DRAIN_CONFIRM_SECONDS`` later, covers a job assigned
    between the first look and the stop.
    """

    def idle() -> bool:
        return not (
            runner_busy_via_process(unit.workdir) or runner_busy_via_lock(unit.workdir, unit.unit, cleanup_stale=True)
        )

    if not idle():
        return False
    time_module.sleep(DRAIN_CONFIRM_SECONDS)
    return idle()


def drain_surplus(units: list[RunnerUnit], desired: int, dry_run: bool, max_seconds: float | None = None) -> list[dict]:
    """Stop surplus runners once they settle between jobs (#1929, #1973).

    Contract: never stops a runner with a live Worker/lock, and never leaves
    fewer than ``desired`` active runners. Returns the stop actions taken.

    Only a runner that has stayed between jobs for ``DRAIN_SETTLE_SECONDS`` is
    stopped (#1973): a shorter gap is the moment GitHub assigns it the next job.
    """
    budget = DRAIN_MAX_SECONDS if max_seconds is None else max_seconds
    if dry_run or budget <= 0:
        return []
    desired = max(0, desired)
    remaining = sorted((u for u in units if u.active), key=lambda item: item.num, reverse=True)
    actions: list[dict] = []
    idle_since: dict[str, float] = {}
    deadline = time_module.monotonic() + budget
    while len(remaining) > desired and time_module.monotonic() < deadline:
        for unit in list(remaining):
            if len(remaining) <= desired:
                break
            if not _between_jobs(unit):
                idle_since.pop(unit.unit, None)
                continue
            now = time_module.monotonic()
            if now - idle_since.setdefault(unit.unit, now) < DRAIN_SETTLE_SECONDS:
                continue
            assert len(remaining) > desired, "drain must not go below the desired runner count"  # noqa: S101
            if not stop_runner_safely(unit):
                continue
            remaining.remove(unit)
            actions.append({"runner": unit.num, "unit": unit.unit, "action": "stop", "mode": "drain"})
        if len(remaining) > desired:
            time_module.sleep(DRAIN_POLL_SECONDS)
    return actions


def maintenance_active() -> bool:
    return MAINTENANCE_PATH.exists()


def set_maintenance(active: bool) -> None:
    if active:
        MAINTENANCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        MAINTENANCE_PATH.write_text(
            json.dumps({"since": datetime.now(UTC).isoformat(), "host": platform.node()}) + "\n", encoding="utf-8"
        )
    else:
        MAINTENANCE_PATH.unlink(missing_ok=True)


def disk_free_gb(path: str) -> float | None:
    """Free space at ``path`` in GB, or None when it cannot be read."""
    try:
        return shutil.disk_usage(path).free / 1e9
    except OSError:
        return None


def mem_available_gb() -> float | None:
    """``MemAvailable`` from /proc/meminfo in GiB, or None when unreadable."""
    try:
        for line in (PROC_ROOT / "meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        return None
    return None


def pressure_avg60(resource: str) -> float | None:
    """PSI ``some avg60`` for ``resource`` (cpu/memory/io), or None when unreadable."""
    try:
        for line in (PROC_ROOT / "pressure" / resource).read_text(encoding="utf-8").splitlines():
            if line.startswith("some "):
                fields = dict(part.split("=", 1) for part in line.split()[1:])
                return float(fields["avg60"])
    except (OSError, ValueError, KeyError):
        return None
    return None


def resource_floor_breaches(floor: dict) -> list[str]:
    """Breached host-resource floors; an unreadable probe never counts as a breach."""
    breaches: list[str] = []
    if "min_disk_free_gb" in floor:
        free = disk_free_gb(str(floor.get("disk_path", "/")))
        if free is not None and free < floor["min_disk_free_gb"]:
            breaches.append(f"disk_free={free:.1f}GB<{floor['min_disk_free_gb']}")
    if "min_mem_available_gb" in floor:
        available = mem_available_gb()
        if available is not None and available < floor["min_mem_available_gb"]:
            breaches.append(f"mem_available={available:.1f}GB<{floor['min_mem_available_gb']}")
    if "max_memory_pressure_avg60" in floor:
        psi = pressure_avg60("memory")
        if psi is not None and psi > floor["max_memory_pressure_avg60"]:
            breaches.append(f"memory_psi_avg60={psi:.1f}>{floor['max_memory_pressure_avg60']}")
    return breaches


def apply_resource_floor(config: dict, desired: int, reason: str) -> tuple[int, str]:
    """Lower (never raise) capacity while the host is short on disk or memory (#2000).

    Surplus runners still leave through the busy-safe drain, so a breach never
    kills a running job; capacity returns on the first tick after recovery.
    """
    floor = config.get("resource_floor")
    if not floor:
        return desired, reason
    breaches = resource_floor_breaches(floor)
    if not breaches:
        return desired, reason
    floored = min(desired, int(floor.get("runners", 1)))
    return floored, f"{reason} + resource-floor ({', '.join(breaches)})"


def effective_capacity(config: dict, target: int | None) -> tuple[int, str]:
    """Desired runner count; an active maintenance hold overrides everything with 0."""
    if maintenance_active():
        return 0, "maintenance-drain"
    desired, reason = desired_capacity(
        config,
        target,
        target_source="cli: --target" if target is not None else None,
    )
    return apply_resource_floor(config, desired, reason)


def run_maintenance_drain(units: list[RunnerUnit], timeout: float) -> dict:
    """Hold at 0 runners and drain fast; report whether the host is fully idle.

    The hold stays in place until ``--resume`` (or a drained reboot), so the next
    tick does not restart what was just stopped.
    """
    set_maintenance(True)
    active = [unit for unit in units if unit.active]
    actions = drain_surplus(active, 0, dry_run=False, max_seconds=timeout)
    stopped = {action["unit"] for action in actions}
    remaining = [unit for unit in active if unit.unit not in stopped]
    return {"drained": not remaining, "remaining": len(remaining), "actions": actions}


def reboot_if_drained(report: dict) -> bool:
    """Reboot the host only when the drain finished with no runner left; never otherwise."""
    if not report.get("drained") or report.get("remaining", 1) != 0:
        log.warning("reboot refused: %s runner(s) still active", report.get("remaining"))
        return False
    set_maintenance(False)  # runners must come back after the boot
    run_cmd(["systemctl", "reboot"])
    return True


def build_state(config: dict, desired: int, reason: str, actions: list[dict]) -> dict:
    units = list_units()
    active = [unit for unit in units if unit.active]
    busy = [unit for unit in units if unit.busy]
    busy_active = [unit for unit in active if unit.busy]
    busy_without_listener = [unit for unit in busy if not unit.active]
    tz = _resolve_tz(config.get("timezone", "America/Los_Angeles"))
    return {
        "hostname": platform.node(),
        "timestamp": datetime.now(tz).isoformat(),
        "config_path": str(CONFIG_PATH),
        "enabled": bool(config.get("enabled", True)),
        "timezone": config.get("timezone", "America/Los_Angeles"),
        "desired": desired,
        "reason": reason,
        "installed": len(units),
        "online": len(active) + len(busy_without_listener),
        "busy": len(busy),
        "busy_without_listener": len(busy_without_listener),
        "idle": len(active) - len(busy_active),
        "offline": len(units) - len(active) - len(busy_without_listener),
        "actions": actions,
        "runners": [
            {
                "num": unit.num,
                "unit": unit.unit,
                "workdir": str(unit.workdir),
                "active": unit.active,
                "busy": unit.busy,
            }
            for unit in units
        ],
        "schedule": config,
    }


def write_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="start/stop runners")
    parser.add_argument("--dry-run", action="store_true", help="show planned actions")
    parser.add_argument("--json", action="store_true", help="print state as JSON")
    parser.add_argument("--target", type=int, help="override scheduled desired count")
    parser.add_argument("--drain", action="store_true", help="maintenance: hold at 0 runners, drain, report")
    parser.add_argument("--reboot", action="store_true", help="with --drain: reboot once fully drained")
    parser.add_argument("--resume", action="store_true", help="clear the maintenance hold")
    parser.add_argument("--drain-timeout", type=float, default=MAINTENANCE_DRAIN_TIMEOUT_SECONDS)
    args = parser.parse_args()
    if args.reboot and not args.drain:
        parser.error("--reboot requires --drain")
    if args.drain and args.resume:
        parser.error("--drain and --resume are mutually exclusive")

    config = load_config()
    dry_run = args.dry_run or not args.apply
    if args.resume and not dry_run:
        set_maintenance(False)
    units = list_units(cleanup_stale=not dry_run)
    maintenance_report: dict | None = None
    if args.drain and not dry_run:
        maintenance_report = run_maintenance_drain(units, timeout=args.drain_timeout)
        actions = maintenance_report["actions"]
        desired, reason = 0, "maintenance-drain"
        if args.reboot:
            maintenance_report["reboot"] = reboot_if_drained(maintenance_report)
    else:
        desired, reason = effective_capacity(config, args.target)
        actions = apply_capacity(units, desired, dry_run=dry_run)
        stopped = {action["unit"] for action in actions if action["action"] == "stop"}
        remaining = [replace(unit, active=False) if unit.unit in stopped else unit for unit in units]
        actions += drain_surplus(remaining, desired, dry_run=dry_run)
    state = build_state(config, desired, reason, actions)
    rebooting = bool(maintenance_report and maintenance_report.get("reboot"))
    if maintenance_report is not None or maintenance_active():
        state["maintenance"] = {
            "active": maintenance_active() or rebooting,
            "drained": state["online"] == 0,
            "rebooting": rebooting,
        }
    if args.apply and not args.dry_run:
        write_state(state)
    if args.json:
        sys.stdout.write(json.dumps(state, indent=2) + "\n")
    else:
        sys.stdout.write(
            f"desired={state['desired']} reason={state['reason']} "
            f"installed={state['installed']} online={state['online']} "
            f"busy={state['busy']} idle={state['idle']} offline={state['offline']} "
            f"actions={len(actions)}\n"
        )
        for action in actions:
            sys.stdout.write(f"{action['action']} runner-{action['runner']} {action['unit']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
