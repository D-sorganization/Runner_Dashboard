"""Ephemeral working directories for expert-panel seat turns (#2899)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from staff.cli_projects import PANEL_SCRATCH_PREFIX

from shared_scripts.panel_turn_scratch import (
    discover_fleet_repo_root,
    write_panel_scratch_marker,
)

log = logging.getLogger("dashboard.staff.panel_scratch")

__all__ = ["prepare_panel_turn_scratch", "remove_panel_turn_scratch"]


def _panel_fleet_root() -> Path:
    root = discover_fleet_repo_root(Path(__file__).resolve())
    if root is None:
        raise RuntimeError("Runner_Dashboard fleet root not found for panel scratch marker")
    return root


def prepare_panel_turn_scratch() -> str:
    """Create a git-initialised ``staff_panel_*`` scratch dir with a fleet-root marker (#2899)."""
    path = tempfile.mkdtemp(prefix=PANEL_SCRATCH_PREFIX)
    scratch = Path(path)
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
        write_panel_scratch_marker(scratch, _panel_fleet_root())
        return path
    except Exception:
        shutil.rmtree(path, ignore_errors=True)
        raise


def remove_panel_turn_scratch(path: str) -> None:
    """Remove a panel turn scratch directory when the turn finishes."""
    if path:
        shutil.rmtree(path, ignore_errors=True)
