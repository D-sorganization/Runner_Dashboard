#!/usr/bin/env python3
"""Trusted Claude Stop hook: fleet agent-check only inside a real git worktree (#2899).

Panel seats run in ``/tmp/staff_panel_*`` scratch directories created by the staff panel
runner. Those sessions must skip stop-hook checks without treating arbitrary ``/tmp`` paths
as trusted.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared_scripts.panel_turn_scratch import discover_fleet_repo_root, is_verified_panel_scratch  # noqa: E402


def _is_git_worktree(cwd: Path) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0 and proc.stdout.strip().lower() == "true"


def _should_skip_agent_check(cwd: Path) -> bool:
    if os.environ.get("STAFF_PANEL_TURN") == "1" and is_verified_panel_scratch(cwd):
        return True
    if not _is_git_worktree(cwd):
        return True
    return discover_fleet_repo_root(cwd) is None


def main() -> int:
    payload = sys.stdin.read()
    cwd = Path.cwd()
    if _should_skip_agent_check(cwd):
        return 0
    root = discover_fleet_repo_root(cwd)
    assert root is not None  # guarded by _should_skip_agent_check  # noqa: S101
    bridge = Path(__file__).resolve().parent / "agent-check-hook-bridge.py"
    proc = subprocess.run(
        [sys.executable, str(bridge), "claude-stop"],
        cwd=root,
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
