"""Remove stale git lock files under a staff worktree before submodule bootstrap (#3935).

Only deletes ``*.lock`` files under the worktree's git directory (including
``.git/modules/**``) when no live ``git`` process is operating on that lock's
repository (matched by resolved ``--git-common-dir``). Live locks are left in
place and logged.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

log = logging.getLogger("dashboard.staff.worktree_git_lock_cleanup")

_Run = Callable[..., subprocess.CompletedProcess[str]]
_MAX_LOCK_FILES = 500


@dataclass(frozen=True)
class GitLockCleanupResult:
    removed: tuple[str, ...] = ()
    kept_live: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


def resolve_git_common_dir(worktree: Path, run: _Run) -> Path | None:
    """Return the resolved ``--git-common-dir`` for *worktree*, or ``None``."""
    try:
        proc = run(
            ["git", "-C", str(worktree), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = (worktree / path).resolve()
    else:
        path = path.resolve()
    return path


def iter_git_lock_files(git_common_dir: Path) -> Iterable[Path]:
    """Yield ``*.lock`` files under *git_common_dir*, excluding ``objects/``."""
    if not git_common_dir.is_dir():
        return
    count = 0
    for path in git_common_dir.rglob("*.lock"):
        posix = path.as_posix()
        if "/objects/" in posix:
            continue
        yield path
        count += 1
        if count >= _MAX_LOCK_FILES:
            log.warning(
                "git lock scan hit %d-file cap under %s; stopping early",
                _MAX_LOCK_FILES,
                git_common_dir,
            )
            return


def _is_git_process(proc: psutil.Process) -> bool:
    try:
        name = (proc.name() or "").lower()
        cmdline = proc.cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    if name == "git" or name.endswith("/git"):
        return True
    if cmdline:
        base = Path(cmdline[0]).name.lower()
        if base in ("git", "git.exe"):
            return True
    return False


def _git_workdirs_from_process(proc: psutil.Process) -> list[Path]:
    paths: list[Path] = []
    try:
        cmdline = proc.cmdline()
        cwd = proc.cwd()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return paths
    i = 0
    while i < len(cmdline):
        arg = cmdline[i]
        if arg in ("-C", "--git-dir", "--work-tree") and i + 1 < len(cmdline):
            paths.append(Path(cmdline[i + 1]))
            i += 2
            continue
        if arg.startswith("-C") and len(arg) > 2:
            paths.append(Path(arg[2:]))
            i += 1
            continue
        i += 1
    if cwd:
        paths.append(Path(cwd))
    return paths


def _process_may_touch_worktree(proc: psutil.Process, worktree: Path) -> bool:
    wt = worktree.resolve()
    prefix = str(wt) + os.sep
    for candidate in _git_workdirs_from_process(proc):
        try:
            path = candidate.resolve()
        except OSError:
            continue
        if path == wt or wt in path.parents or path in wt.parents:
            return True
        if str(path).startswith(prefix):
            return True
    return False


def live_git_common_dirs(worktree: Path, run: _Run) -> set[Path]:
    """Collect git-common-dir paths for live ``git`` processes touching *worktree*."""
    if psutil is None:
        return set()
    live: set[Path] = set()
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.pid == os.getpid():
                continue
            if not _is_git_process(proc):
                continue
            if not _process_may_touch_worktree(proc, worktree):
                continue
            for workdir in _git_workdirs_from_process(proc):
                common = resolve_git_common_dir(workdir, run)
                if common is not None:
                    live.add(common)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return live


def clear_stale_git_locks(
    worktree: Path,
    *,
    run: _Run = subprocess.run,
    live_common_dirs: set[Path] | None = None,
) -> GitLockCleanupResult:
    """Delete stale ``*.lock`` files under *worktree*'s git dir when safe (#3935)."""
    if not (worktree / ".git").exists():
        return GitLockCleanupResult()

    git_dir = resolve_git_common_dir(worktree, run)
    if git_dir is None:
        return GitLockCleanupResult()

    if live_common_dirs is None:
        live_common_dirs = live_git_common_dirs(worktree, run)

    removed: list[str] = []
    kept: list[str] = []
    errors: list[str] = []

    for lock in iter_git_lock_files(git_dir):
        repo_git_dir = lock.parent.resolve()
        if repo_git_dir in live_common_dirs:
            kept.append(str(lock))
            log.info(
                "worktree preflight: keeping %s (live git process uses %s)",
                lock,
                repo_git_dir,
            )
            continue
        try:
            lock.unlink()
            removed.append(str(lock))
            log.warning("worktree preflight: removed stale git lock %s", lock)
        except OSError as exc:
            msg = f"{lock}: {exc}"
            errors.append(msg)
            log.warning("worktree preflight: failed to remove stale git lock %s: %s", lock, exc)

    return GitLockCleanupResult(
        removed=tuple(removed),
        kept_live=tuple(kept),
        errors=tuple(errors),
    )
