#!/usr/bin/env python3
"""Apply a time-of-day GitHub Actions runner capacity schedule."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
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


def run_cmd(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=30)


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


def runner_busy_via_lock(workdir: Path) -> bool:
    """Detect a job-start hook marker for the runner."""
    lock = RUNNER_BUSY_LOCK_DIR / f"{workdir.name}.lock"
    return _recent_path_is_busy(lock, RUNNER_BUSY_LOCK_MAX_AGE_SECONDS)


def runner_busy_via_process(workdir: Path) -> bool:
    """Detect legacy, self-updated, or reparented workers from the global table."""
    try:
        result = run_cmd(["ps", "-eo", "args="])
    except (OSError, subprocess.SubprocessError):
        return True
    if result.returncode != 0:
        return True
    normalized_root = str(workdir).replace("\\", "/")
    pattern = re.compile(rf"{re.escape(normalized_root)}/bin(?:\.[^/\s]+)?/Runner\.Worker(?:\s|$)")
    return any(pattern.search(line.replace("\\", "/")) is not None for line in result.stdout.splitlines())


def runner_busy(workdir: Path) -> bool:
    """Return busy when any independent job signal is positive or unreadable."""
    return runner_busy_via_pickup(workdir) or runner_busy_via_lock(workdir) or runner_busy_via_process(workdir)


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
                busy=runner_busy(workdir),
            )
        )
    return sorted(units, key=lambda item: item.num)


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
        idle_active = [unit for unit in active if not unit.busy]
        for unit in sorted(idle_active, key=lambda item: item.num, reverse=True)[: len(active) - desired]:
            actions.append({"runner": unit.num, "unit": unit.unit, "action": "stop"})
            if not dry_run:
                run_cmd(["systemctl", "stop", unit.unit])
                run_cmd(["systemctl", "reset-failed", unit.unit])
    return actions


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
    args = parser.parse_args()

    config = load_config()
    desired, reason = desired_capacity(
        config,
        args.target,
        target_source="cli: --target" if args.target is not None else None,
    )
    units = list_units()
    actions = apply_capacity(units, desired, dry_run=(args.dry_run or not args.apply))
    state = build_state(config, desired, reason, actions)
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
