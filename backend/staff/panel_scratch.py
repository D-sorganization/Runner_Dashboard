"""Ephemeral working directories for expert-panel seat turns (#2899).

MIRRORED CONTRACT. ``discover_fleet_repo_root``/``is_fleet_runner_dashboard_root``/
``write_panel_scratch_marker`` are a deliberate copy of Repository_Management
``shared_scripts/panel_turn_scratch.py``: the deploy artifact and ``python
backend/server.py`` do not put the repo root (which holds ``shared_scripts``) on
``sys.path``, so importing it at panel-turn time crashed every seat with
``No module named 'shared_scripts'`` (#3477). Keep these in sync by hand; there is
no automated drift check since the two copies serve different processes
(``scripts/trusted_agent_hook.py`` still imports the ``shared_scripts`` original).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from staff.cli_projects import PANEL_SCRATCH_PREFIX

log = logging.getLogger("dashboard.staff.panel_scratch")

__all__ = ["prepare_panel_turn_scratch", "remove_panel_turn_scratch"]

PANEL_SCRATCH_MARKER = ".staff-panel-fleet-root"


def discover_fleet_repo_root(start: Path | None = None) -> Path | None:
    """Return the nearest ancestor of ``start`` that contains ``fleet-hooks/manifest.yml``."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "fleet-hooks" / "manifest.yml").is_file():
            return candidate
    return None


def is_fleet_runner_dashboard_root(root: Path) -> bool:
    """True when ``root`` looks like a Runner_Dashboard checkout (not an arbitrary repo)."""
    resolved = root.resolve()
    return (
        (resolved / "fleet-hooks" / "manifest.yml").is_file()
        and (resolved / "backend" / "staff" / "panel.py").is_file()
        and (resolved / "scripts" / "agent-check-hook-bridge.py").is_file()
    )


def write_panel_scratch_marker(scratch_dir: Path, fleet_root: Path) -> None:
    """Record which fleet checkout created this panel scratch directory."""
    assert scratch_dir.is_dir(), f"scratch must exist: {scratch_dir}"  # noqa: S101
    assert is_fleet_runner_dashboard_root(fleet_root), f"invalid fleet root: {fleet_root}"  # noqa: S101
    (scratch_dir / PANEL_SCRATCH_MARKER).write_text(f"{fleet_root.resolve()}\n", encoding="utf-8")


def _write_fleet_root_marker(scratch: Path) -> None:
    """Best-effort fleet-root marker: a seat turn must run even when it can't be written (#3477)."""
    root = discover_fleet_repo_root(Path(__file__).resolve())
    if root is None:
        raise RuntimeError("Runner_Dashboard fleet root not found for panel scratch marker")
    write_panel_scratch_marker(scratch, root)


def prepare_panel_turn_scratch() -> str:
    """Create a git-initialised ``staff_panel_*`` scratch dir with a best-effort fleet-root marker (#2899)."""
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
        try:
            _write_fleet_root_marker(scratch)
        except Exception as exc:  # noqa: BLE001 — orthogonality: a missing marker must not break the seat turn (#3477)
            log.warning("panel scratch fleet-root marker not written for %s: %s", path, exc)
        return path
    except Exception:
        shutil.rmtree(path, ignore_errors=True)
        raise


def remove_panel_turn_scratch(path: str) -> None:
    """Remove a panel turn scratch directory when the turn finishes."""
    if path:
        shutil.rmtree(path, ignore_errors=True)
