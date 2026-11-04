"""OGLaptop fleet backup pilot telemetry for fleet status (#4015)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from dashboard_config import HOSTNAME

MARKER_INLINE_MAX_BYTES = 4096
_PILOT_HOST = re.compile(r"oglaptop", re.IGNORECASE)


def _pilot_enabled() -> bool:
    flag = os.environ.get("FLEET_BACKUP_PILOT", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    if flag in {"0", "false", "no", "off"}:
        return False
    return bool(_PILOT_HOST.search(HOSTNAME or ""))


def _state_path() -> Path:
    override = os.environ.get("RUNNER_DASHBOARD_FLEET_BACKUP_PILOT_STATE", "").strip()
    if override:
        return Path(override).expanduser()
    wsl_path = Path("/mnt/c/ProgramData/runner-dashboard/fleet_backup_pilot.json")
    if wsl_path.exists() or Path("/mnt/c/ProgramData/runner-dashboard").is_dir():
        return wsl_path
    program_data = os.environ.get("PROGRAMDATA", "").strip()
    if program_data:
        return Path(program_data) / "runner-dashboard" / "fleet_backup_pilot.json"
    state_dir = os.environ.get("RUNNER_DASHBOARD_STATE_DIR", "").strip()
    if state_dir:
        return Path(state_dir).expanduser() / "fleet_backup_pilot.json"
    return Path.home() / ".config" / "runner-dashboard" / "fleet_backup_pilot.json"


def read_fleet_backup_pilot_status() -> dict[str, Any]:
    """Best-effort read of the Windows scheduled backup last-success marker."""
    if not _pilot_enabled():
        return {"enabled": False, "pilot": "oglaptop"}
    path = _state_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {
            "enabled": True,
            "pilot": "oglaptop",
            "attention": False,
            "last_success_at": None,
            "last_result": None,
            "bandwidth_mbps": None,
        }
    except OSError:
        return {
            "enabled": True,
            "pilot": "oglaptop",
            "attention": True,
            "last_result": "error",
        }
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {
            "enabled": True,
            "pilot": "oglaptop",
            "attention": True,
            "last_result": "error",
        }
    if not isinstance(payload, dict):
        return {
            "enabled": True,
            "pilot": "oglaptop",
            "attention": True,
            "last_result": "error",
        }
    result: dict[str, Any] = {
        "enabled": True,
        "pilot": "oglaptop",
        "attention": bool(payload.get("attention")),
        "last_success_at": payload.get("last_success_at"),
        "last_attempt_at": payload.get("last_attempt_at"),
        "last_result": payload.get("last_result"),
        "bandwidth_mbps": payload.get("bandwidth_mbps"),
        "bandwidth_probe_completed_at": payload.get("bandwidth_probe_completed_at"),
        "message": payload.get("message"),
    }
    if len(raw) <= MARKER_INLINE_MAX_BYTES:
        result["marker"] = payload
    else:
        result["marker_truncated"] = True
    return result
