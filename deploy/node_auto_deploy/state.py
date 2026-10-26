"""Persist auto-deploy events and status for /api/fleet/status."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from node_auto_deploy.contracts import AutoDeployEvent, AutoDeployState, DeployResult

MAX_EVENTS = 20
_ATTENTION_RESULTS = frozenset({"failed", "rollback", "rollback_failed"})


def default_state_path(environ: dict[str, str] | None = None) -> Path:
    import os

    env = environ if environ is not None else dict(os.environ)
    override = env.get("RUNNER_DASHBOARD_AUTO_DEPLOY_STATE", "").strip()
    if override:
        return Path(override).expanduser()
    state_dir = env.get("RUNNER_DASHBOARD_STATE_DIR", "").strip()
    if state_dir:
        return Path(state_dir).expanduser() / "auto_deploy_state.json"
    return Path.home() / ".config" / "runner-dashboard" / "auto_deploy_state.json"


def load_state(path: Path) -> AutoDeployState:
    if not path.is_file():
        return AutoDeployState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AutoDeployState()
    if not isinstance(payload, dict):
        return AutoDeployState()
    return AutoDeployState.from_dict(payload)


def save_state(path: Path, state: AutoDeployState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record_event(
    state: AutoDeployState,
    *,
    commit: str,
    result: DeployResult,
    message: str,
    rolled_back_from: str | None = None,
    now: datetime | None = None,
    attention: bool = False,
) -> AutoDeployEvent:
    when = now or datetime.now(timezone.utc)  # noqa: UP017 - dashboard hub supports Python 3.10
    event = AutoDeployEvent(
        at=when.isoformat(),
        commit=commit,
        result=result,
        message=message,
        rolled_back_from=rolled_back_from,
    )
    state.last_attempt_at = event.at
    state.last_result = result
    state.target_commit = commit
    state.message = message
    state.attention = attention or result in _ATTENTION_RESULTS
    state.events = [event.to_dict(), *state.events][:MAX_EVENTS]
    return event


def fleet_status_payload(state: AutoDeployState) -> dict[str, Any]:
    """Shape embedded under local /api/fleet/status metrics."""
    return {
        "last_attempt_at": state.last_attempt_at,
        "last_result": state.last_result,
        "target_commit": state.target_commit,
        "previous_commit": state.previous_commit,
        "message": state.message,
        "attention": state.attention,
        "recent_events": state.events[:5],
    }
