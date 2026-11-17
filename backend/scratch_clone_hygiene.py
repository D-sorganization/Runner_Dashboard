"""Account for git clones and aged task folders in a scratch root such as ``/tmp`` (#4440).

Agents leave review clones and task output in ``/tmp``; no timer looks at them, and on
2026-11-14 they helped push a node under its disk floor. This module answers "what is there,
how big is it, and what holds no work?". **It only reports. Nothing here deletes.**

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
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from agent_worktree_hygiene import (
    DEFAULT_PROTECTED_REPOS,
    _count_by_reason,
    _git,
    _origin_url,
    _submodule_problem,
    is_protected_repo,
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
    clone: Path, age_hours: float | None, min_age_hours: float, protected_repos: tuple[str, ...], busy_dirs: BusyDirs
) -> str:
    if is_protected_repo(clone, _origin_url(clone), protected_repos):
        return "private_repo"
    if age_hours is None:
        return "unreadable"
    if age_hours < min_age_hours:
        return "too_young"
    busy = busy_dirs()
    if busy is None:
        return "in_use_unknown"
    if _in_use(clone, busy):
        return "in_use"
    return _holds_work(clone)


def plan_scratch_clones(
    roots: Iterable[Path],
    *,
    min_age_hours: float = DEFAULT_SCRATCH_MIN_AGE_HOURS,
    protected_repos: tuple[str, ...] = DEFAULT_PROTECTED_REPOS,
    now: float | None = None,
    busy_dirs: BusyDirs = process_working_directories,
) -> dict[str, Any]:
    """Report the git clones directly under each root. Post: no file is changed."""
    ts = now if now is not None else time.time()
    reclaimable: list[dict[str, Any]] = []
    kept: list[dict[str, str]] = []
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
            age = _age_hours(child, ts)
            reason = _keep_reason(child, age, min_age_hours, protected_repos, busy_dirs)
            if reason:
                kept.append({"path": str(child), "reason": reason})
                continue
            row = {"path": str(child), "size_bytes": _size_bytes(child), "age_hours": round(age or 0.0, 1)}
            reclaimable.append({**row, "origin": _origin_url(child)})
    return {
        "mode": "report-only",
        "min_age_hours": min_age_hours,
        "reclaimable": reclaimable,
        "reclaimable_bytes": sum(row["size_bytes"] for row in reclaimable),
        "kept": kept,
        "kept_by_reason": _count_by_reason(kept),
    }


def plan_aged_task_folders(
    roots: Iterable[Path],
    *,
    patterns: tuple[str, ...] = DEFAULT_TASK_FOLDER_PATTERNS,
    min_age_hours: float = DEFAULT_TASK_FOLDER_MIN_AGE_HOURS,
    now: float | None = None,
) -> dict[str, Any]:
    """Report task-output folders whose newest file is older than ``min_age_hours``. Post: no file is changed."""
    ts = now if now is not None else time.time()
    aged: list[dict[str, Any]] = []
    for root in roots:
        for child in _children(root):
            if child.is_symlink() or not child.is_dir():
                continue
            if not any(fnmatch.fnmatchcase(child.name, pattern) for pattern in patterns):
                continue
            infos = list(_walk_files(child))
            try:
                newest = max([info.st_mtime for info in infos] + [child.stat().st_mtime])
            except OSError:
                continue
            age = (ts - newest) / 3600.0
            if age >= min_age_hours:
                size = sum(info.st_size for info in infos)
                aged.append({"path": str(child), "size_bytes": size, "age_hours": round(age, 1)})
    return {
        "mode": "report-only",
        "patterns": list(patterns),
        "min_age_hours": min_age_hours,
        "aged": aged,
        "aged_bytes": sum(row["size_bytes"] for row in aged),
    }
