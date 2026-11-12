"""Shared status for the latest runner-control action on this machine."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock

_lock = Lock()
_status: dict[str, str] = {"status": "healthy"}


def _status_path() -> Path:
    config_dir = os.environ.get("RUNNER_DASHBOARD_CONFIG_DIR")
    return Path(config_dir or (Path.home() / ".config" / "runner-dashboard")) / "runner-control-health.json"


def record_runner_control_result(action: str, *, succeeded: bool) -> None:
    """Record the bounded outcome of a validated runner-control action."""
    assert action in {"start", "stop", "restart"}, "unsupported runner control action"
    with _lock:
        _status.clear()
        _status.update(status="healthy" if succeeded else "degraded")
        if not succeeded:
            _status["last_action"] = action
        _write_status()


def get_runner_control_health() -> dict[str, str]:
    """Return a defensive snapshot of the latest runner-control outcome."""
    with _lock:
        try:
            value = json.loads(_status_path().read_text(encoding="utf-8"))
            if (
                isinstance(value, dict)
                and value.get("status") in {"healthy", "degraded"}
                and ("last_action" not in value or value["last_action"] in {"start", "stop", "restart"})
            ):
                _status.clear()
                _status.update(value)
        except (OSError, json.JSONDecodeError):
            pass
        return dict(_status)


def reset_runner_control_health() -> None:
    """Reset process-local state for isolated tests."""
    with _lock:
        _status.clear()
        _status["status"] = "healthy"
        try:
            _status_path().unlink(missing_ok=True)
        except OSError:
            pass


def _write_status() -> None:
    """Persist the shared status for the dashboard and autoscaler processes."""
    path = _status_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(_status), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass
