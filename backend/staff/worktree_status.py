"""Read-only Git status probe for staff worktrees.

Moved from ``staff.workspace`` to give it headroom under the 500-line cap (#2806);
``staff.workspace`` re-exports ``worktree_has_unpushed_commits``, so callers and
``monkeypatch.setattr(workspace, ...)`` targets are unchanged.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def worktree_has_unpushed_commits(worktree: Path, base_ref: str = "origin/main") -> bool:
    """Return True if worktree has commits ahead of base_ref or uncommitted changes."""
    if not worktree.exists() or not worktree.is_dir():
        return False
    try:
        status_res = subprocess.run(  # noqa: S603
            ["git", "status", "--porcelain"],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if status_res.returncode == 0:
            dirty = [
                ln
                for ln in status_res.stdout.splitlines()
                if ln.strip() and not ln.strip().startswith(("?? .staff/", "?? .gemini/"))
            ]
            if dirty:
                return True
        else:
            return True
        # Try checking against upstream tracking branch first, then origin/<branch>, then base_ref
        log_res = subprocess.run(  # noqa: S603
            ["git", "rev-list", "@{u}..HEAD", "--count"],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if log_res.returncode == 0:
            return log_res.stdout.strip() != "0"
        head_res = subprocess.run(  # noqa: S603
            ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            timeout=15,
        )
        cur_branch = head_res.stdout.strip() if head_res.returncode == 0 else ""
        if cur_branch:
            remote_res = subprocess.run(  # noqa: S603
                ["git", "rev-list", f"origin/{cur_branch}..HEAD", "--count"],
                cwd=str(worktree),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if remote_res.returncode == 0:
                return remote_res.stdout.strip() != "0"

        fallback_res = subprocess.run(  # noqa: S603
            ["git", "rev-list", f"{base_ref}..HEAD", "--count"],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if fallback_res.returncode == 0:
            return fallback_res.stdout.strip() != "0"
    except Exception:  # noqa: BLE001
        return True
    return True
