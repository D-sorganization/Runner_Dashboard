"""Claude CLI project folder management and cleanup for staff turns (#1683, #1688).

Claude CLI creates persistent project folders under ``<CLAUDE_CONFIG_DIR>/projects/<encoded cwd>``
where every character outside ``[A-Za-z0-9]`` is replaced by ``-``. These project directories leak
unless explicitly cleaned up after the turn and periodically swept.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path

log = logging.getLogger("dashboard.staff.cli_projects")

__all__ = [
    "CHAT_SCRATCH_PREFIX",
    "DEFAULT_CHAT_IDLE_SECONDS",
    "DEFAULT_PANEL_PROJECT_MAX_AGE_SECONDS",
    "PANEL_SCRATCH_PREFIX",
    "chat_project_prefix",
    "encode_project_name",
    "panel_project_prefix",
    "projects_root",
    "remove_turn_project",
    "remove_worktree_project",
    "run_project_prefix",
    "sweep_idle_chat_scratch",
    "sweep_orphan_run_projects",
    "start_panel_sweep",
    "sweep_stale_panel_projects",
]

CHAT_SCRATCH_PREFIX = "staff_chat_"
DEFAULT_CHAT_IDLE_SECONDS = 14 * 24 * 3600
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


def _scratch_project_prefix(scratch_prefix: str, tmpdir: str | None = None) -> str:
    """Return the encoded project prefix for temporary directories starting with scratch_prefix."""
    base_dir = tmpdir or tempfile.gettempdir()
    return encode_project_name(os.path.join(base_dir, scratch_prefix))


def panel_project_prefix(tmpdir: str | None = None) -> str:
    """Return the encoded project prefix for temporary panel scratch directories."""
    return _scratch_project_prefix(PANEL_SCRATCH_PREFIX, tmpdir)


def chat_project_prefix(tmpdir: str | None = None) -> str:
    """Return the encoded project prefix for temporary chat scratch directories."""
    return _scratch_project_prefix(CHAT_SCRATCH_PREFIX, tmpdir)


def _is_prefixed_project(path: Path, root: Path, prefix: str) -> bool:
    """Return True if path is a genuine direct project directory under root matching prefix."""
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


def _is_panel_project(path: Path, root: Path, prefix: str) -> bool:
    """Return True if path is a genuine direct panel project directory under root."""
    return _is_prefixed_project(path, root, prefix)


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


def start_panel_sweep(env: Mapping[str, str]) -> threading.Thread:
    """Run ``sweep_stale_panel_projects`` on a daemon thread and return at once.

    Callers must never wait for the sweep: awaiting it from a panel handed control back to
    the event loop, and a short-lived loop then abandoned the panel (the Windows hang
    found after #1685). A failure inside the sweep is logged and never propagates.
    """
    snapshot = dict(env)

    def _sweep() -> None:
        try:
            sweep_stale_panel_projects(snapshot)
        except Exception as exc:  # noqa: BLE001 — a sweep problem must never surface
            log.warning("background panel project sweep failed: %s", exc)

    worker = threading.Thread(target=_sweep, name="panel-project-sweep", daemon=True)
    worker.start()
    return worker


def run_project_prefix(worktrees_root: Path) -> str:
    """Return the encoded project prefix for run worktrees directly under worktrees_root."""
    return encode_project_name(str(worktrees_root)) + "-"


def remove_worktree_project(worktree: Path, worktrees_root: Path, env: Mapping[str, str]) -> bool:
    """Remove the Claude CLI project folder corresponding to worktree if it matches the run prefix.

    Pre: worktree is a direct child of worktrees_root (compared as normpath strings).
    Never raises: catches OSError, logs a warning, and returns False.
    """
    assert os.path.normpath(str(worktree.parent)) == os.path.normpath(str(worktrees_root)), (  # noqa: S101
        f"worktree must be a direct child of worktrees_root: {worktree!r} vs {worktrees_root!r}"
    )
    try:
        root = projects_root(env)
        target = root / encode_project_name(str(worktree))
        prefix = run_project_prefix(worktrees_root)
        if _is_prefixed_project(target, root, prefix):
            shutil.rmtree(target)
            return True
        return False
    except OSError as exc:
        log.warning("failed to remove worktree project for %s: %s", worktree, exc)
        return False


def sweep_orphan_run_projects(
    env: Mapping[str, str],
    worktrees_root: Path,
    *,
    min_age_seconds: float = DEFAULT_PANEL_PROJECT_MAX_AGE_SECONDS,
    now: float | None = None,
) -> list[str]:
    """Sweep and remove orphaned run Claude CLI project directories older than min_age_seconds.

    Pre: min_age_seconds > 0.
    Never raises: catches OSError per entry, logs a warning, and returns sorted removed names.
    """
    assert min_age_seconds > 0, f"min_age_seconds must be positive: {min_age_seconds}"  # noqa: S101
    current_time = time.time() if now is None else now

    live: set[str] = set()
    try:
        if worktrees_root.is_dir():
            for child in worktrees_root.iterdir():
                try:
                    if child.exists():
                        live.add(encode_project_name(str(worktrees_root / child.name)))
                except OSError:
                    continue
    except OSError as exc:
        log.warning("failed to iterate worktrees root %s: %s", worktrees_root, exc)

    try:
        root = projects_root(env)
        if not root.is_dir():
            return []
        children = list(root.iterdir())
    except OSError as exc:
        log.warning("failed to iterate projects root: %s", exc)
        return []

    prefix = run_project_prefix(worktrees_root)
    removed: list[str] = []
    for child in children:
        try:
            if not _is_prefixed_project(child, root, prefix):
                continue
            if child.name in live:
                continue
            if current_time - child.stat().st_mtime > min_age_seconds:
                shutil.rmtree(child)
                removed.append(child.name)
        except OSError as exc:
            log.warning("failed to sweep run project %s: %s", child, exc)
            continue
    return sorted(removed)


def _last_activity(path: Path) -> float:
    """Return the newest st_mtime among path itself and its direct children.

    Uses lstat, ignores children that vanish or fail lstat, and never follows symlinks.
    """
    try:
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            return 0.0
        newest = st.st_mtime
    except OSError:
        return 0.0

    try:
        if stat.S_ISDIR(st.st_mode):
            for child in path.iterdir():
                try:
                    child_st = child.lstat()
                    if not stat.S_ISLNK(child_st.st_mode):
                        newest = max(newest, child_st.st_mtime)
                except OSError:
                    continue
    except OSError:
        pass
    return newest


def _surviving_chat_scratch_encodings(temp_base: Path) -> set[str]:
    """Return encoded project names for genuine surviving chat scratch directories."""
    surviving: set[str] = set()
    try:
        if temp_base.is_dir():
            for child in temp_base.iterdir():
                try:
                    if (
                        child.name.startswith(CHAT_SCRATCH_PREFIX)
                        and len(child.name) > len(CHAT_SCRATCH_PREFIX)
                        and not child.is_symlink()
                        and child.is_dir()
                    ):
                        surviving.add(encode_project_name(str(child)))
                except OSError:
                    continue
    except OSError as exc:
        log.warning("failed to iterate temp directory %s: %s", temp_base, exc)
    return surviving


def sweep_idle_chat_scratch(
    env: Mapping[str, str],
    *,
    idle_seconds: float = DEFAULT_CHAT_IDLE_SECONDS,
    now: float | None = None,
    tmpdir: str | None = None,
) -> list[str]:
    """Sweep and remove idle chat scratch directories and orphaned project directories (#1688).

    Pre: idle_seconds > 0.
    Never raises: catches OSError per entry, logs a warning, and returns sorted removed names.
    """
    assert idle_seconds > 0, f"idle_seconds must be positive: {idle_seconds}"  # noqa: S101
    current_time = time.time() if now is None else now
    temp_base = Path(tmpdir or tempfile.gettempdir())
    root = projects_root(env)
    prefix = chat_project_prefix(tmpdir)
    removed: list[str] = []

    # a) Sweep idle chat scratch directories in temp directory
    try:
        temp_children = list(temp_base.iterdir()) if temp_base.is_dir() else []
    except OSError as exc:
        log.warning("failed to iterate temp directory %s: %s", temp_base, exc)
        temp_children = []

    for d in temp_children:
        if not (d.name.startswith(CHAT_SCRATCH_PREFIX) and len(d.name) > len(CHAT_SCRATCH_PREFIX)):
            continue
        try:
            st = d.lstat()
        except OSError as exc:
            log.warning("failed to lstat scratch directory %s: %s", d, exc)
            continue
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            continue
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            continue

        p = root / encode_project_name(str(d))
        try:
            p_exists = p.exists()
        except OSError:
            p_exists = False

        # The scratch dir's own mtime counts too: thread_scratch_dir touches it when a turn
        # starts, so a resumed idle thread is never swept before its CLI writes a session.
        activity = max(st.st_mtime, _last_activity(p) if p_exists else _last_activity(d))
        if current_time - activity > idle_seconds:
            try:
                shutil.rmtree(d)
                removed.append(d.name)
            except OSError as exc:
                log.warning("failed to remove chat scratch dir %s: %s", d, exc)
                continue

            if _is_prefixed_project(p, root, prefix):
                try:
                    shutil.rmtree(p)
                except OSError as exc:
                    log.warning("failed to remove chat project dir %s: %s", p, exc)

    # b) Sweep orphaned chat project directories in projects root
    surviving = _surviving_chat_scratch_encodings(temp_base)

    try:
        project_children = list(root.iterdir()) if root.is_dir() else []
    except OSError as exc:
        log.warning("failed to iterate projects root %s: %s", root, exc)
        project_children = []

    for p in project_children:
        try:
            if not _is_prefixed_project(p, root, prefix):
                continue
            if p.name in surviving:
                continue
            if current_time - _last_activity(p) > idle_seconds:
                shutil.rmtree(p)
                removed.append(p.name)
        except OSError as exc:
            log.warning("failed to sweep orphan chat project %s: %s", p, exc)
            continue

    return sorted(removed)
