"""Ephemeral working directories for expert-panel seat turns (#2899)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile

from staff.cli_projects import PANEL_SCRATCH_PREFIX

log = logging.getLogger("dashboard.staff.panel_scratch")

__all__ = ["prepare_panel_turn_scratch", "remove_panel_turn_scratch"]


def prepare_panel_turn_scratch() -> str:
    """Create an isolated scratch directory for one panel seat turn.

    Pre: none.
    Post: returned path exists, is not a symlink, contains a ``.git`` directory from
    ``git init`` (so provider stop hooks can resolve a worktree), and its name uses
    :data:`~staff.cli_projects.PANEL_SCRATCH_PREFIX`.
    """
    path = tempfile.mkdtemp(prefix=PANEL_SCRATCH_PREFIX)
    try:
        proc = subprocess.run(
            [
                "git",
                "-c",
                "user.email=panel@localhost",
                "-c",
                "user.name=panel",
                "init",
            ],
            cwd=path,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"panel scratch git init failed: {proc.stderr.strip() or proc.stdout.strip()}")
        git_dir = os.path.join(path, ".git")
        if not os.path.isdir(git_dir):
            raise RuntimeError(f"panel scratch missing .git after init: {path}")
        return path
    except Exception:
        shutil.rmtree(path, ignore_errors=True)
        raise


def remove_panel_turn_scratch(path: str) -> None:
    """Remove a panel turn scratch directory when the turn finishes."""
    if path:
        shutil.rmtree(path, ignore_errors=True)
