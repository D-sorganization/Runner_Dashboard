#!/usr/bin/env python3
"""Trusted Claude Stop hook: fleet agent-check only inside a real git worktree (#2899).

Panel seats run in ``/tmp/staff_panel_*`` scratch directories. Those sessions must not
run stop-hook checks or surface hook failures as the model's answer.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _is_git_worktree(cwd: Path) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0 and proc.stdout.strip().lower() == "true"


def discover_fleet_repo_root(start: Path | None = None) -> Path | None:
    """Return the nearest ancestor of ``start`` that contains ``fleet-hooks/manifest.yml``."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "fleet-hooks" / "manifest.yml").is_file():
            return candidate
    return None


def main() -> int:
    payload = sys.stdin.read()
    if os.environ.get("STAFF_PANEL_TURN") == "1":
        return 0
    if not _is_git_worktree(Path.cwd()):
        return 0
    root = discover_fleet_repo_root()
    if root is None:
        return 0
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
