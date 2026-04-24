#!/usr/bin/env python3
"""Apply a time-of-day GitHub Actions runner capacity schedule."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

RUNNER_ROOT = Path(os.environ.get("RUNNER_ROOT", str(Path.home() / "actions-runners")))
CONFIG_PATH = Path(
    os.environ.get(
        "RUNNER_SCHEDULE_CONFIG",
        str(Path.home() / ".config" / "runner-dashboard" / "runner-schedule.json"),
    )
)
STATE_PATH = Path(
    os.environ.get("RUNNER_SCHEDULER_STATE", "/var/lib/runner-scheduler/state.json")
)
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


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
    int(config.get("default_count", 1))
    ZoneInfo(config.get("timezone", "America/Los_Angeles"))
    for entry in config["schedules"]:
        if not isinstance(entry, dict):
            raise ValueError("each schedule entry must be an object")
        int(entry["runners"])
        parse_hhmm(entry["start"])
        parse_hhmm(entry["end"])
        days = entry.get("days", [])
        if not days or any(day not in DAYS for day in days):
            raise ValueError(
                f"invalid schedule days for {entry.get('name', '<unnamed>')}"
            )


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
    config: dict, target_override: int | None = None
) -> tuple[int, str]:
    if target_override is not None:
        return target_override, "manual-target"
    if not config.get("enabled", True):
        return int(config.get("default_count", 1)), "schedule-disabled"
    tz = ZoneInfo(config.get("timezone", "America/Los_Angeles"))
    now = datetime.now(tz)
    for entry in config["schedules"]:
        if schedule_matches(entry, now):
            return int(entry["runners"]), str(entry.get("name", "scheduled"))
    return int(config.get("default_count", 1)), "default"


def unit_workdir(unit: str) -> Path | None:
    result = run_cmd(
        ["systemctl", "show", unit, "--property=WorkingDirectory", "--value"]
    )
    value = result.stdout.strip()
    return Path(value) if value else None


def unit_active(unit: str) -> bool:
    return run_cmd(["systemctl", "is-active", "--quiet", unit]).returncode == 0


def runner_busy(workdir: Path) -> bool:
    pattern = str(workdir / "bin" / "Runner.Worker")
    result = run_cmd(["ps", "-eo", "args="])
    return any(pattern in line for line in result.stdout.splitlines())


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
                busy=active and runner_busy(workdir),
            )
        )
    return sorted(units, key=lambda item: item.num)


def apply_capacity(units: list[RunnerUnit], desired: int, dry_run: bool) -> list[dict]:
    actions: list[dict] = []
    desired = max(0, min(desired, len(units)))
    active = [unit for unit in units if unit.active]
    if len(active) < desired:
        for unit in [unit for unit in units if not unit.active][
            : desired - len(active)
        ]:
            actions.append({"runner": unit.num, "unit": unit.unit, "action": "start"})
            if not dry_run:
                run_cmd(["systemctl", "start", unit.unit])
    if len(active) > desired:
        idle_active = [unit for unit in active if not unit.busy]
        for unit in sorted(idle_active, key=lambda item: item.num, reverse=True)[
            : len(active) - desired
        ]:
            actions.append({"runner": unit.num, "unit": unit.unit, "action": "stop"})
            if not dry_run:
                run_cmd(["systemctl", "stop", unit.unit])
                run_cmd(["systemctl", "reset-failed", unit.unit])
    return actions


def build_state(config: dict, desired: int, reason: str, actions: list[dict]) -> dict:
    units = list_units()
    active = [unit for unit in units if unit.active]
    busy = [unit for unit in active if unit.busy]
    tz = ZoneInfo(config.get("timezone", "America/Los_Angeles"))
    return {
        "hostname": platform.node(),
        "timestamp": datetime.now(tz).isoformat(),
        "config_path": str(CONFIG_PATH),
        "enabled": bool(config.get("enabled", True)),
        "timezone": config.get("timezone", "America/Los_Angeles"),
        "desired": desired,
        "reason": reason,
        "installed": len(units),
        "online": len(active),
        "busy": len(busy),
        "idle": len(active) - len(busy),
        "offline": len(units) - len(active),
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
    desired, reason = desired_capacity(config, args.target)
    units = list_units()
    actions = apply_capacity(units, desired, dry_run=(args.dry_run or not args.apply))
    state = build_state(config, desired, reason, actions)
    if args.apply and not args.dry_run:
        write_state(state)
    if args.json:
        print(json.dumps(state, indent=2))
    else:
        print(
            f"desired={state['desired']} reason={state['reason']} "
            f"installed={state['installed']} online={state['online']} "
            f"busy={state['busy']} idle={state['idle']} offline={state['offline']} "
            f"actions={len(actions)}"
        )
        for action in actions:
            print(f"{action['action']} runner-{action['runner']} {action['unit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
