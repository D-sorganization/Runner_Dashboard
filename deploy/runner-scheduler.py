#!/usr/bin/env python3
"""Apply a time-of-day GitHub Actions runner capacity schedule."""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import re
import subprocess
import sys
import time as time_module
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time
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
log = logging.getLogger("runner-scheduler")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _resolve_tz(tz_name: str):
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


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32" and pid == 1:
        return True
    try:
        import psutil  # type: ignore

        return bool(psutil.pid_exists(pid))
    except ImportError:
        pass
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def runner_busy_via_lock(workdir: Path, unit: str | None = None) -> bool:
    """Detect a job-start hook marker for the runner."""
    for name in runner_names(workdir, unit):
        lock = RUNNER_BUSY_LOCK_DIR / f"{name}.lock"
        if not _recent_path_is_busy(lock, RUNNER_BUSY_LOCK_MAX_AGE_SECONDS):
            continue
        # Issue #1987: Treat lock as stale immediately if recorded pid= is dead
        # and no Runner.Worker process is running for that runner.
        try:
            content = lock.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("pid="):
                    val = line.split("=", 1)[1].strip()
                    if val.isdigit():
                        lock_pid = int(val)
                        if not _pid_is_alive(lock_pid) and not runner_busy_via_process(workdir):
                            try:
                                lock.unlink(missing_ok=True)
                            except OSError:
                                pass
                            break
                    break
            else:
                return True
            if lock.exists():
                return True
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


def runner_busy(workdir: Path, unit: str | None = None) -> bool:
    """Return busy when any independent job signal is positive or unreadable."""
    return (
        runner_busy_via_pickup(workdir)
        or runner_busy_via_lock(workdir, unit)
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


def list_units() -> list[RunnerUnit]:
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
                busy=runner_busy(workdir, unit=name),
            )
        )
    return sorted(units, key=lambda item: item.num)


def _confirmed_idle(unit: RunnerUnit, settle: bool) -> bool:
    if runner_busy(unit.workdir, unit=unit.unit):
        return False
    if settle and STOP_RECHECK_DELAY_SECONDS > 0:
        time_module.sleep(STOP_RECHECK_DELAY_SECONDS)
        return not runner_busy(unit.workdir, unit=unit.unit)
    return True


def _worker_absent(unit: RunnerUnit) -> bool:
    """True when neither a Runner.Worker process nor a job lock exists for the runner."""
    return not (runner_busy_via_process(unit.workdir) or runner_busy_via_lock(unit.workdir, unit.unit))


def stop_runner_safely(unit: RunnerUnit) -> bool:
    """Stop an idle runner without a window in which a job can be assigned (#1929).

    Checking idle and then stopping leaves milliseconds in which the listener can
    accept a job. Freeze the whole unit first (a stopped listener cannot pick up or
    fork a job), re-verify no Worker exists, and only then stop; ``systemctl stop``
    follows SIGTERM with SIGCONT. A job seen after the freeze thaws the unit and
    vetoes the stop; the unit is thawed on every path except a successful
    stop (including errors). Fails closed if the freeze fails. Returns True only when the
    stop was issued.
    """
    if run_cmd(["systemctl", "kill", "--kill-who=all", "--signal=SIGSTOP", unit.unit]).returncode != 0:
        log.warning("could not freeze %s; not stopping it", unit.unit)
        return False
    stopped = False
    try:
        if not _worker_absent(unit):
            return False
        try:
            stopped = run_cmd(["systemctl", "stop", unit.unit], timeout=STOP_TIMEOUT_SECONDS).returncode == 0
        except subprocess.TimeoutExpired:
            # The stop was issued; it counts once the unit is no longer active.
            stopped = not unit_active(unit.unit)
            log.warning("systemctl stop %s timed out; unit %s", unit.unit, "going down" if stopped else "still active")
    finally:
        if not stopped:
            # Every path except a successful stop must leave the unit running.
            run_cmd(["systemctl", "kill", "--kill-who=all", "--signal=SIGCONT", unit.unit])
    if stopped:
        run_cmd(["systemctl", "reset-failed", unit.unit])
    return stopped


def apply_capacity(units: list[RunnerUnit], desired: int, dry_run: bool) -> list[dict]:
    actions: list[dict] = []
    desired = max(0, min(desired, len(units)))
    active = [unit for unit in units if unit.active]
    if len(active) < desired:
        for unit in [unit for unit in units if not unit.active and not unit.busy][: desired - len(active)]:
            actions.append({"runner": unit.num, "unit": unit.unit, "action": "start"})
            if not dry_run:
                run_cmd(["systemctl", "start", unit.unit])
    if len(active) > desired:
        needed = len(active) - desired
        stopped = 0
        for unit in sorted((u for u in active if not u.busy), key=lambda item: item.num, reverse=True):
            if stopped >= needed:
                break
            # The snapshot is seconds old (list_units issues several systemctl
            # calls per unit); a job may have been assigned since. Confirm idle
            # twice, spaced out, immediately before killing the listener (#1921).
            if not _confirmed_idle(unit, settle=not dry_run):
                continue
            if not dry_run and not stop_runner_safely(unit):
                continue
            actions.append({"runner": unit.num, "unit": unit.unit, "action": "stop"})
            stopped += 1
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
        return not (runner_busy_via_process(unit.workdir) or runner_busy_via_lock(unit.workdir, unit.unit))

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


def effective_capacity(config: dict, target: int | None) -> tuple[int, str]:
    """Desired runner count; an active maintenance hold overrides everything with 0."""
    if maintenance_active():
        return 0, "maintenance-drain"
    return desired_capacity(
        config,
        target,
        target_source="cli: --target" if target is not None else None,
    )


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
    units = list_units()
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
