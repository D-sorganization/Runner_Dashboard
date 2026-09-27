"""Claude CLI project folder management and cleanup for expert-panel turns (#1683).

Claude CLI creates persistent project folders under ``<CLAUDE_CONFIG_DIR>/projects/<encoded cwd>``
where every character outside ``[A-Za-z0-9]`` is replaced by ``-``. Because each expert-panel
turn runs in a fresh temporary directory, these project directories leak unless explicitly
cleaned up after the turn and periodically swept.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path

log = logging.getLogger("dashboard.staff.cli_projects")

__all__ = [
    "DEFAULT_PANEL_PROJECT_MAX_AGE_SECONDS",
    "PANEL_SCRATCH_PREFIX",
    "encode_project_name",
    "panel_project_prefix",
    "projects_root",
    "remove_turn_project",
    "sweep_stale_panel_projects",
]

PANEL_SCRATCH_PREFIX = "staff_panel_"
DEFAULT_PANEL_PROJECT_MAX_AGE_SECONDS = 6 * 3600


def encode_project_name(cwd: str) -> str:
    """Encode a directory path into Claude CLI's project folder naming scheme.

    Pre: cwd is absolute (POSIX, as the CLI runs under WSL, or native).
    """
    assert cwd.startswith("/") or os.path.isabs(cwd), f"cwd must be absolute: {cwd!r}"  # noqa: S101
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def projects_root(env: Mapping[str, str]) -> Path:
    """Return the Claude CLI projects directory from env or default location."""
    cfg_dir = env.get("CLAUDE_CONFIG_DIR")
    if cfg_dir:
        return Path(cfg_dir).expanduser() / "projects"
    home_dir = env.get("HOME") or Path.home()
    return Path(home_dir).expanduser() / ".claude" / "projects"


def panel_project_prefix(tmpdir: str | None = None) -> str:
    """Return the encoded project prefix for temporary panel scratch directories."""
    base_dir = tmpdir or tempfile.gettempdir()
    return encode_project_name(os.path.join(base_dir, PANEL_SCRATCH_PREFIX))


def _is_panel_project(path: Path, root: Path, prefix: str) -> bool:
    """Return True if path is a genuine direct panel project directory under root."""
    try:
        return (
            path.parent.resolve() == root.resolve()
            and path.name.startswith(prefix)
            and len(path.name) > len(prefix)
            and not path.is_symlink()
            and path.is_dir()
        )
    except OSError:
        return False


def remove_turn_project(cwd: str, env: Mapping[str, str]) -> bool:
    """Remove the Claude CLI project folder corresponding to cwd if it is a panel project.

    Never raises: catches OSError, logs a warning, and returns False.
    """
    try:
        root = projects_root(env)
        target = root / encode_project_name(cwd)
        if _is_panel_project(target, root, panel_project_prefix()):
            shutil.rmtree(target)
            return True
        return False
    except OSError as exc:
        log.warning("failed to remove turn project for %s: %s", cwd, exc)
        return False


def sweep_stale_panel_projects(
    env: Mapping[str, str],
    *,
    max_age_seconds: float = DEFAULT_PANEL_PROJECT_MAX_AGE_SECONDS,
    now: float | None = None,
) -> list[str]:
    """Sweep and remove stale expert-panel Claude CLI project directories.

    Pre: max_age_seconds > 0.
    Never raises: catches OSError on iterdir and per entry, logs a warning, and returns removed names.
    """
    assert max_age_seconds > 0, f"max_age_seconds must be positive: {max_age_seconds}"  # noqa: S101
    current_time = time.time() if now is None else now
    try:
        root = projects_root(env)
        if not root.is_dir():
            return []
        children = list(root.iterdir())
    except OSError as exc:
        log.warning("failed to iterate projects root: %s", exc)
        return []

    prefix = panel_project_prefix()
    removed: list[str] = []
    for child in children:
        try:
            if not _is_panel_project(child, root, prefix):
                continue
            if current_time - child.stat().st_mtime > max_age_seconds:
                shutil.rmtree(child)
                removed.append(child.name)
        except OSError as exc:
            log.warning("failed to sweep project %s: %s", child, exc)
            continue
    return sorted(removed)
