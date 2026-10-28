"""Locate the Runner Dashboard checkout or deploy tree (#2982)."""

from __future__ import annotations

import os
from pathlib import Path


def _looks_like_dashboard(root: Path) -> bool:
    return (root / "backend" / "agent_bus").is_dir() and (root / "clients" / "fleet").is_dir()


def resolve_dashboard_root(*, start: Path | None = None) -> Path | None:
    """Return the dashboard root for hook bridges running outside the RD git checkout."""
    for key in ("RUNNER_DASHBOARD_REPO_ROOT", "RUNNER_DASHBOARD_DEPLOY_DIR"):
        raw = os.environ.get(key, "").strip()
        if raw:
            candidate = Path(raw).expanduser()
            if _looks_like_dashboard(candidate):
                return candidate
    deploy = Path.home() / "actions-runners" / "dashboard"
    for name in ("current", "live"):
        candidate = deploy / name
        if _looks_like_dashboard(candidate):
            return candidate
    if _looks_like_dashboard(deploy):
        return deploy
    anchor = start or Path.cwd()
    for root in (anchor, *anchor.parents):
        if _looks_like_dashboard(root):
            return root
    script = Path(__file__).resolve()
    for root in script.parents:
        if _looks_like_dashboard(root):
            return root
    return None
