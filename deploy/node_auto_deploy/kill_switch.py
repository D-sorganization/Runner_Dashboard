"""Per-node kill switch for auto-deploy."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

_FALSEY = frozenset({"0", "false", "no", "off", ""})


def auto_deploy_disabled(
    *,
    env: Mapping[str, str] | None = None,
    flag_file: Path | None = None,
) -> bool:
    """Return True when auto-deploy must not run on this node."""
    return kill_switch_reason(env=env, flag_file=flag_file) is not None


def kill_switch_reason(
    *,
    env: Mapping[str, str] | None = None,
    flag_file: Path | None = None,
) -> str | None:
    """Human-readable reason auto-deploy is disabled, or None when enabled."""
    environ = env if env is not None else os.environ
    raw = environ.get("RUNNER_DASHBOARD_AUTO_DEPLOY", "1").strip().lower()
    if raw in _FALSEY:
        return "RUNNER_DASHBOARD_AUTO_DEPLOY is disabled"
    path = flag_file if flag_file is not None else default_flag_path(environ)
    if path.is_file():
        return f"kill-switch flag file present: {path}"
    return None


def default_flag_path(environ: Mapping[str, str] | None = None) -> Path:
    env = environ if environ is not None else os.environ
    override = env.get("RUNNER_DASHBOARD_AUTO_DEPLOY_FLAG", "").strip()
    if override:
        return Path(override).expanduser()
    state_dir = env.get("RUNNER_DASHBOARD_STATE_DIR", "").strip()
    if state_dir:
        return Path(state_dir).expanduser() / "auto-deploy.disabled"
    return Path.home() / ".config" / "runner-dashboard" / "auto-deploy.disabled"
