"""Account for git clones and aged task folders in a scratch root such as ``/tmp`` (#4440).

Agents leave review clones and task output in ``/tmp``; no timer looks at them, and on
2026-11-14 they helped push a node under its disk floor. This module answers what is reclaimable
under the same fail-closed rules as worktree hygiene. With ``apply=False`` (default) nothing is
deleted; with ``apply=True`` eligible paths are removed after a second check.

A clone is reported as reclaimable only when every check passes; anything that cannot be
read counts as a reason to keep it:

- it is a clone of its own (a linked worktree belongs to ``agent_worktree_hygiene``),
- its repo is not private (``DEFAULT_PROTECTED_REPOS``, by folder or origin name),
- nothing in it was touched for ``min_age_hours``,
- no process has its working directory in it,
- it has no linked worktrees, no local changes, no stash and no commit missing from its
  remotes, and the same holds for every submodule.

Stdlib only and free of 3.11+ constructs: ``deploy/prune_agent_worktrees.py`` imports this
under the hosts' system python.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, cast

from agent_active_workdirs import active_staff_workdirs
from agent_worktree_hygiene import (
    DEFAULT_PROTECTED_REPOS,
    _active_session_reason,
    _count_by_reason,
    _git,
    _origin_url,
    _submodule_problem,
    is_protected_repo,
    isolated_git_config,
)

DEFAULT_SCRATCH_MIN_AGE_HOURS = 24.0
DEFAULT_TASK_FOLDER_MIN_AGE_HOURS = 48.0
DEFAULT_TASK_FOLDER_PATTERNS: tuple[str, ...] = ("claude-*",)
_ACTIVITY_MARKERS = ("", ".git", ".git/HEAD", ".git/index", ".git/logs/HEAD")

BusyDirs = Callable[[], Iterable[Path] | None]


def process_working_directories() -> set[Path] | None:
    """Working directories of this user's processes, or ``None`` when they cannot all be read.

    Reads ``/proc``. A process of another user is skipped: its directory is not readable
    without root, and a report must not need root.
    """
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    mine = os.getuid() if hasattr(os, "getuid") else None
    found: set[Path] = set()
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != mine:
                continue
            found.add(Path(os.readlink(entry / "cwd")))
        except FileNotFoundError:
            continue  # the process ended while we looked
        except OSError:
            return None
    return found


def _children(root: Path) -> list[Path]:
    try:
        return sorted(root.iterdir())
    except OSError:
        return []


def _walk_files(folder: Path) -> Iterator[os.stat_result]:
    """``lstat`` of every entry below ``folder``; symlinks are counted, never followed."""
    stack = [folder]
    while stack:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            yield info
            if entry.is_dir(follow_symlinks=False):
                stack.append(Path(entry.path))


def _size_bytes(folder: Path) -> int:
    return sum(info.st_size for info in _walk_files(folder))


def _age_hours(clone: Path, now: float) -> float | None:
    """Hours since git or the user last touched ``clone``; ``None`` when it cannot be read."""
    stamps: list[float] = []
    for marker in _ACTIVITY_MARKERS:
        try:
            stamps.append((clone / marker).stat().st_mtime)
        except FileNotFoundError:
            continue
        except OSError:
            return None
    return (now - max(stamps)) / 3600.0 if stamps else None


def _in_use(clone: Path, busy: Iterable[Path]) -> bool:
    resolved = clone.resolve()
    return any(resolved == cwd.resolve() or resolved in cwd.resolve().parents for cwd in busy)


def _foreign_owned(child: Path) -> bool:
    """True when ``child`` or its ``.git`` is owned by another user (it could plant repo-local config)."""
    if not hasattr(os, "getuid"):
        return False
    me = os.getuid()
    for target in (child, child / ".git"):
        try:
            if target.lstat().st_uid != me:
                return True
        except FileNotFoundError:
            continue
        except OSError:
            return True
    return False


def _holds_work(clone: Path) -> str:
    """Why ``clone`` may hold work that exists nowhere else, or ``""`` (fail-closed)."""
    worktrees = _git(clone, "worktree", "list", "--porcelain")
    if worktrees.returncode != 0:
        return "unreadable"
    if sum(line.startswith("worktree ") for line in worktrees.stdout.splitlines()) > 1:
        return "has_worktrees"
    status = _git(clone, "status", "--porcelain")
    if status.returncode != 0:
        return "unreadable"
    if status.stdout.strip():
        return "dirty"
    stash = _git(clone, "stash", "list")
    if stash.returncode != 0:
        return "unreadable"
    if stash.stdout.strip():
        return "stash"
    unpushed = _git(clone, "rev-list", "--count", "HEAD", "--all", "--not", "--remotes")
    if unpushed.returncode != 0:
        return "unreadable"
    if unpushed.stdout.strip() != "0":
        return "unpushed"
    return _submodule_problem(clone)


def _keep_reason(
    clone: Path,
    age_hours: float | None,
    min_age_hours: float,
    protected_repos: tuple[str, ...],
    busy_dirs: BusyDirs,
    *,
    active_workdirs: set[Path] | None,
) -> str:
    if is_protected_repo(clone, _origin_url(clone), protected_repos):
        return "private_repo"
    if age_hours is None:
        return "unreadable"
    if age_hours < min_age_hours:
        return "too_young"
    session = _active_session_reason(clone, active_workdirs)
    if session:
        return session
    busy = busy_dirs()
    if busy is None:
        return "in_use_unknown"
    if _in_use(clone, busy):
        return "in_use"
    return _holds_work(clone)


def _remove_tree(path: Path) -> bool:
    try:
        shutil.rmtree(path)
    except OSError:
        return False
    return not path.exists()


def plan_scratch_clones(
    roots: Iterable[Path],
    *,
    apply: bool = False,
    min_age_hours: float = DEFAULT_SCRATCH_MIN_AGE_HOURS,
    protected_repos: tuple[str, ...] = DEFAULT_PROTECTED_REPOS,
    now: float | None = None,
    busy_dirs: BusyDirs | None = None,
    active_workdirs: set[Path] | None | object = ...,
) -> dict[str, Any]:
    """List git clones directly under each root; optionally remove reclaimable ones."""
    with isolated_git_config():
        return _plan_scratch_clones(
            roots,
            apply=apply,
            min_age_hours=min_age_hours,
            protected_repos=protected_repos,
            now=now,
            busy_dirs=busy_dirs,
            active_workdirs=active_workdirs,
        )


def _plan_scratch_clones(
    roots: Iterable[Path],
    *,
    apply: bool,
    min_age_hours: float,
    protected_repos: tuple[str, ...],
    now: float | None,
    busy_dirs: BusyDirs | None,
    active_workdirs: set[Path] | None | object,
) -> dict[str, Any]:
    ts = now if now is not None else time.time()
    workdirs = active_staff_workdirs() if active_workdirs is ... else cast("set[Path] | None", active_workdirs)
    busy_probe = busy_dirs or process_working_directories
    reclaimable: list[dict[str, Any]] = []
    kept: list[dict[str, str]] = []
    removed = 0
    for root in roots:
        for child in _children(root):
            git_entry = child / ".git"
            if child.is_symlink():
                if git_entry.exists():
                    kept.append({"path": str(child), "reason": "symlink"})
                continue
            if git_entry.is_file():
                kept.append({"path": str(child), "reason": "linked_worktree"})
                continue
            if not git_entry.is_dir():
                continue
            if _foreign_owned(child):
                kept.append({"path": str(child), "reason": "foreign_owned"})
                continue
            age = _age_hours(child, ts)
            reason = _keep_reason(child, age, min_age_hours, protected_repos, busy_probe, active_workdirs=workdirs)
            if reason:
                kept.append({"path": str(child), "reason": reason})
                continue
            row = {"path": str(child), "size_bytes": _size_bytes(child), "age_hours": round(age or 0.0, 1)}
            reclaimable.append({**row, "origin": _origin_url(child)})
            if not apply:
                continue
            if _keep_reason(child, age, min_age_hours, protected_repos, busy_probe, active_workdirs=workdirs):
                kept.append({"path": str(child), "reason": "re_check_failed"})
                reclaimable.pop()
                continue
            if _remove_tree(child):
                removed += 1
    return {
        "dry_run": not apply,
        "min_age_hours": min_age_hours,
        "reclaimable": reclaimable,
        "reclaimable_bytes": sum(row["size_bytes"] for row in reclaimable),
        "kept": kept,
        "kept_by_reason": _count_by_reason(kept),
        "removed": removed,
    }


def _in_use_reason(folder: Path, active_workdirs: set[Path] | None, busy_probe: BusyDirs) -> str:
    """Why ``folder`` must not be removed right now, or ``""``; an unreadable busy probe counts as in use."""
    session = _active_session_reason(folder, active_workdirs)
    if session:
        return session
    busy = busy_probe()
    if busy is None:
        return "in_use_unknown"
    return "in_use" if _in_use(folder, busy) else ""


def plan_aged_task_folders(
    roots: Iterable[Path],
    *,
    apply: bool = False,
    patterns: tuple[str, ...] = DEFAULT_TASK_FOLDER_PATTERNS,
    min_age_hours: float = DEFAULT_TASK_FOLDER_MIN_AGE_HOURS,
    now: float | None = None,
    busy_dirs: BusyDirs | None = None,
    active_workdirs: set[Path] | None | object = ...,
) -> dict[str, Any]:
    """List (and optionally remove) task-output folders past ``min_age_hours`` with no active session."""
    ts = now if now is not None else time.time()
    workdirs = active_staff_workdirs() if active_workdirs is ... else cast("set[Path] | None", active_workdirs)
    busy_probe = busy_dirs or process_working_directories
    aged: list[dict[str, Any]] = []
    kept: list[dict[str, str]] = []
    removed = 0
    for root in roots:
        for child in _children(root):
            if child.is_symlink() or not child.is_dir():
                continue
            if not any(fnmatch.fnmatchcase(child.name, pattern) for pattern in patterns):
                continue
            if _foreign_owned(child):
                kept.append({"path": str(child), "reason": "foreign_owned"})
                continue
            in_use = _in_use_reason(child, workdirs, busy_probe)
            if in_use:
                kept.append({"path": str(child), "reason": in_use})
                continue
            infos = list(_walk_files(child))
            try:
                newest = max([info.st_mtime for info in infos] + [child.stat().st_mtime])
            except OSError:
                continue
            age = (ts - newest) / 3600.0
            if age < min_age_hours:
                continue
            size = sum(info.st_size for info in infos)
            aged.append({"path": str(child), "size_bytes": size, "age_hours": round(age, 1)})
            if not apply:
                continue
            in_use = _in_use_reason(child, workdirs, busy_probe)
            if in_use:
                kept.append({"path": str(child), "reason": "re_check_failed"})
                aged.pop()
                continue
            if _remove_tree(child):
                removed += 1
    return {
        "dry_run": not apply,
        "kept": kept,
        "patterns": list(patterns),
        "min_age_hours": min_age_hours,
        "aged": aged,
        "aged_bytes": sum(row["size_bytes"] for row in aged),
        "removed": removed,
    }
