"""Expose node-local auto-deploy state on fleet telemetry (issue auto-deploy)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _state_path() -> Path:
    override = os.environ.get("RUNNER_DASHBOARD_AUTO_DEPLOY_STATE", "").strip()
    if override:
        return Path(override).expanduser()
    state_dir = os.environ.get("RUNNER_DASHBOARD_STATE_DIR", "").strip()
    if state_dir:
        return Path(state_dir).expanduser() / "auto_deploy_state.json"
    return Path.home() / ".config" / "runner-dashboard" / "auto_deploy_state.json"


def read_auto_deploy_fleet_status() -> dict[str, Any]:
    """Best-effort read of ``auto_deploy_state.json`` for ``/api/fleet/status``."""
    path = _state_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"enabled": True, "attention": False, "last_result": None}
    if not isinstance(payload, dict):
        return {"enabled": True, "attention": False, "last_result": None}
    return {
        "enabled": True,
        "attention": bool(payload.get("attention")),
        "last_result": payload.get("last_result"),
        "last_attempt_at": payload.get("last_attempt_at"),
        "target_commit": payload.get("target_commit"),
        "previous_commit": payload.get("previous_commit"),
        "message": payload.get("message"),
        "recent_events": (payload.get("events") or [])[:5],
    }
