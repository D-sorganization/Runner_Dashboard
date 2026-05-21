"""Runner schedule validation and persistence helpers.

Extracted from server.py (issue #2942).

Responsibilities
----------------
- _validate_hhmm()           — validate HH:MM time strings
- _validate_runner_schedule() — validate a full schedule config dict
- load_runner_schedule_config()  — load + validate from disk
- write_runner_schedule_config() — validate + atomically write to disk
- runner_scheduler_apply_command() — build the systemctl apply command
- get_runner_capacity_snapshot()  — full capacity snapshot dict
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import config_schema
from security import safe_subprocess_env

log = logging.getLogger("dashboard")


# ---------------------------------------------------------------------------
# Module-level defaults (overridden by server.py after import)
# ---------------------------------------------------------------------------

_NUM_RUNNERS: int = 12
_RUNNER_BASE_DIR: Path = Path.home() / "actions-runners"
_RUNNER_SCHEDULE_CONFIG: Path = (
    Path.home() / ".config" / "runner-dashboard" / "runner-schedule.json"
)
_RUNNER_SCHEDULER_BIN: str = os.environ.get(
    "RUNNER_SCHEDULER_BIN", "/usr/local/bin/runner-scheduler"
)
_RUNNER_SCHEDULER_STATE: Path = Path("/var/lib/runner-scheduler/state.json")
_RUNNER_SCHEDULER_SERVICE: str = os.environ.get(
    "RUNNER_SCHEDULER_SERVICE", "runner-scheduler.service"
)
_RUNNER_SCHEDULER_APPLY_CMD: str = os.environ.get("RUNNER_SCHEDULER_APPLY_CMD", "")
_SYSTEMCTL_BIN: str = os.environ.get("SYSTEMCTL_BIN", "/usr/bin/systemctl")
_RUNNER_ALIASES: list[str] = []
_HOSTNAME: str = os.environ.get("DISPLAY_NAME", "")
_MAX_RUNNERS: int = 12


def configure(
    *,
    num_runners: int,
    max_runners: int,
    runner_base_dir: Path,
    runner_schedule_config: Path,
    runner_scheduler_bin: str,
    runner_scheduler_state: Path,
    runner_scheduler_service: str,
    runner_scheduler_apply_cmd: str,
    systemctl_bin: str,
    runner_aliases: list[str],
    hostname: str,
) -> None:
    """Inject runtime configuration from server.py (called once at startup)."""
    global _NUM_RUNNERS, _MAX_RUNNERS, _RUNNER_BASE_DIR, _RUNNER_SCHEDULE_CONFIG
    global _RUNNER_SCHEDULER_BIN, _RUNNER_SCHEDULER_STATE, _RUNNER_SCHEDULER_SERVICE
    global _RUNNER_SCHEDULER_APPLY_CMD, _SYSTEMCTL_BIN, _RUNNER_ALIASES, _HOSTNAME
    _NUM_RUNNERS = num_runners
    _MAX_RUNNERS = max_runners
    _RUNNER_BASE_DIR = runner_base_dir
    _RUNNER_SCHEDULE_CONFIG = runner_schedule_config
    _RUNNER_SCHEDULER_BIN = runner_scheduler_bin
    _RUNNER_SCHEDULER_STATE = runner_scheduler_state
    _RUNNER_SCHEDULER_SERVICE = runner_scheduler_service
    _RUNNER_SCHEDULER_APPLY_CMD = runner_scheduler_apply_cmd
    _SYSTEMCTL_BIN = systemctl_bin
    _RUNNER_ALIASES = runner_aliases
    _HOSTNAME = hostname


# ---------------------------------------------------------------------------
# Default schedule constant
# ---------------------------------------------------------------------------


def _default_runner_schedule() -> dict:
    """Return the default runner schedule config using current module settings."""
    limit = max(_NUM_RUNNERS, _MAX_RUNNERS)
    return {
        "enabled": True,
        "timezone": os.environ.get("RUNNER_SCHEDULE_TIMEZONE", "America/Los_Angeles"),
        "default_count": min(_NUM_RUNNERS, int(os.environ.get("RUNNER_SCHEDULE_DEFAULT", "4"))),
        "schedules": [
            {
                "name": "day",
                "days": ["mon", "tue", "wed", "thu", "fri"],
                "start": "07:00",
                "end": "22:00",
                "runners": min(_NUM_RUNNERS, 4),
            },
            {
                "name": "overnight",
                "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                "start": "22:00",
                "end": "07:00",
                "runners": min(limit, _NUM_RUNNERS),
            },
            {
                "name": "weekend",
                "days": ["sat", "sun"],
                "start": "07:00",
                "end": "22:00",
                "runners": min(_NUM_RUNNERS, 6),
            },
        ],
    }


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _runner_limit() -> int:
    """Return the hard runner capacity this dashboard is allowed to manage."""
    return max(_NUM_RUNNERS, _MAX_RUNNERS)


def _validate_hhmm(value: object) -> str:
    """Validate and return a HH:MM time string.

    Precondition: value should be a string in HH:MM format.
    Raises ValueError on invalid input.
    """
    if not isinstance(value, str) or not re.match(r"^\d{2}:\d{2}$", value):
        raise ValueError("time values must use HH:MM format")
    hour, minute = (int(part) for part in value.split(":", 1))
    if hour > 23 or minute > 59:
        raise ValueError("time values must be valid HH:MM clock times")
    return value


def _validate_runner_schedule(config: object) -> dict:
    """Validate a runner schedule config dict.

    Precondition: config should be a dict.
    Postcondition: returns a sanitised dict with 'enabled', 'timezone',
        'default_count', and 'schedules' keys.
    Raises ValueError on invalid input.
    """
    if not isinstance(config, dict):
        raise ValueError("schedule config must be an object")
    days_allowed = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    sanitized: dict[str, Any] = {
        "enabled": bool(config.get("enabled", True)),
        "timezone": str(config.get("timezone") or "America/Los_Angeles"),
        "default_count": max(0, min(_runner_limit(), int(config.get("default_count", 1)))),
        "schedules": [],
    }
    schedules = config.get("schedules", [])
    if not isinstance(schedules, list):
        raise ValueError("schedules must be a list")
    for entry in schedules:
        if not isinstance(entry, dict):
            raise ValueError("each schedule entry must be an object")
        days = entry.get("days", [])
        if not isinstance(days, list) or not days:
            raise ValueError("each schedule entry needs at least one day")
        normalized_days = [str(day).lower() for day in days]
        if any(day not in days_allowed for day in normalized_days):
            raise ValueError("schedule days must be mon/tue/wed/thu/fri/sat/sun")
        runners = max(0, min(_runner_limit(), int(entry.get("runners", 0))))
        sanitized["schedules"].append(
            {
                "name": str(entry.get("name") or "scheduled"),
                "days": normalized_days,
                "start": _validate_hhmm(entry.get("start")),
                "end": _validate_hhmm(entry.get("end")),
                "runners": runners,
            }
        )
    assert "enabled" in sanitized and "schedules" in sanitized
    return sanitized


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------


def load_runner_schedule_config() -> dict:
    """Load runner schedule config from disk and validate it."""
    raw = config_schema.safe_read_json(_RUNNER_SCHEDULE_CONFIG, _default_runner_schedule())
    return _validate_runner_schedule(raw)


def write_runner_schedule_config(config: dict) -> None:
    """Validate and atomically write a runner schedule config to disk.

    Precondition: config is a dict.
    """
    assert isinstance(config, dict), "config must be a dict"
    try:
        config_schema.validate_runner_schedule_config(config)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    config_schema.atomic_write_json(_RUNNER_SCHEDULE_CONFIG, config)


# ---------------------------------------------------------------------------
# Scheduler helpers
# ---------------------------------------------------------------------------


def runner_scheduler_apply_command() -> list[str]:
    """Return the command used to apply the runner schedule."""
    if _RUNNER_SCHEDULER_APPLY_CMD.strip():
        import shlex  # noqa: PLC0415

        return shlex.split(_RUNNER_SCHEDULER_APPLY_CMD)
    return ["sudo", "-n", _SYSTEMCTL_BIN, "start", _RUNNER_SCHEDULER_SERVICE]


def _sync_runner_scheduler_state(config: dict) -> dict:
    """Query the runner scheduler for its current dry-run state."""
    if not Path(_RUNNER_SCHEDULER_BIN).exists():
        return {
            "available": False,
            "error": f"{_RUNNER_SCHEDULER_BIN} is not installed",
            "config": config,
        }
    env = safe_subprocess_env()
    env["RUNNER_ROOT"] = str(_RUNNER_BASE_DIR)
    env["RUNNER_SCHEDULE_CONFIG"] = str(_RUNNER_SCHEDULE_CONFIG)
    env["RUNNER_SCHEDULER_STATE"] = str(_RUNNER_SCHEDULER_STATE)
    try:
        result = subprocess.run(
            [_RUNNER_SCHEDULER_BIN, "--dry-run", "--json"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "error": str(exc), "config": config}
    if result.returncode != 0:
        return {
            "available": True,
            "error": (result.stderr or result.stdout).strip()[:500],
            "config": config,
        }
    try:
        state = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "available": True,
            "error": "scheduler returned invalid JSON",
            "config": config,
        }
    state["available"] = True
    return state


def _unit_active_sync(unit: str) -> bool:
    """Return True when a systemd unit is active (synchronous)."""
    if os.name == "nt":
        return False
    try:
        result = subprocess.run(
            [_SYSTEMCTL_BIN, "is-active", "--quiet", unit],
            timeout=5,
            check=False,
            env=safe_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def get_runner_capacity_snapshot() -> dict:
    """Build and return the full runner capacity snapshot dict.

    Postcondition: result is a dict with keys: machine, configured_runners,
        installed_runners, max_runners, schedule, state, timers.
    """
    config_error = None
    try:
        config = load_runner_schedule_config()
        state = _sync_runner_scheduler_state(config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        config = _validate_runner_schedule(_default_runner_schedule())
        config_error = str(exc)
        state = {
            "available": Path(_RUNNER_SCHEDULER_BIN).exists(),
            "error": f"schedule config invalid: {config_error}",
            "config": config,
        }
    timer_states: dict[str, str] = {}
    for unit in ("runner-scheduler.timer", "runner-cleanup.timer"):
        timer_states[unit] = "active" if _unit_active_sync(unit) else "inactive"
    result = {
        "machine": _HOSTNAME,
        "aliases": _RUNNER_ALIASES,
        "configured_runners": _NUM_RUNNERS,
        "default_runners": _NUM_RUNNERS,
        "installed_runners": sum(1 for p in _RUNNER_BASE_DIR.glob("runner-*") if p.is_dir()),
        "max_runners": _runner_limit(),
        "config_path": str(_RUNNER_SCHEDULE_CONFIG),
        "state_path": str(_RUNNER_SCHEDULER_STATE),
        "timers": timer_states,
        "schedule": config,
        "state": state,
    }
    assert "machine" in result and "schedule" in result
    return result
