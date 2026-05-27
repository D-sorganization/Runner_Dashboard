"""Stale git-state cleanup for runner homes after autoscaler stops.

When the autoscaler sends ``systemctl stop`` to a busy runner — or when the
runner is killed for any reason while ``git config --global`` is mid-write —
git leaves behind ``$HOME/.gitconfig.lock``. Every subsequent ``actions/checkout``
on that machine fails at the ``git config --global core.longpaths true`` step
with ``error: could not lock config file ...: File exists`` and the job dies
with exit code 255 *before* it can produce any diagnostic.

All runner processes on a single physical machine share the same ``$HOME``
(seen in the wild: ``/home/dieterolson/`` shared across ``runner-1`` through
``runner-8`` on the ControlTower Oglaptop), so a single orphaned lock file
silently breaks the whole fleet on that host.

This module is the recovery half of the contract: after every autoscaler stop
(and defensively on every autoscaler start), call :func:`cleanup_runner_state`
to nuke the known set of stale lock files. The function is intentionally
idempotent and best-effort — if cleanup itself fails, we log a warning and
move on rather than fail the autoscaler loop.

Tracking: Runner_Dashboard#640 (autoscaler kills busy runners),
Runner_Dashboard#664 (lock-file fix for non-root deploys).
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterable
from pathlib import Path

log = logging.getLogger("runner-autoscaler")

# Lock files git leaves behind when ``git config --global`` (or a worktree
# ``git config``) is killed mid-write. These are atomic-rename markers, not
# user data — safe to remove unconditionally once the writer is gone.
_KNOWN_LOCK_BASENAMES: tuple[str, ...] = (
    ".gitconfig.lock",
    ".gitconfig.lock.0",  # rare; git uses .0/.1 suffixes when retrying.
)

# Per-worktree git lock files. Only removed if older than _STALE_LOCK_AGE_S
# so we don't yank a lock out from under an actively-running git command.
_WORKTREE_LOCK_BASENAMES: tuple[str, ...] = (
    "config.lock",
    "index.lock",
    "HEAD.lock",
    "packed-refs.lock",
)

# How old (seconds) a per-worktree lock must be before we treat it as stale.
# 60s is long enough for any sane git operation to finish and short enough
# that orphaned locks don't outlive a single autoscaler tick.
_STALE_LOCK_AGE_S: float = 60.0


def _candidate_runner_homes(unit: str) -> list[Path]:
    """Return the most likely home directory(ies) for *unit*'s runner.

    The autoscaler doesn't know the runner's $HOME directly; instead we
    enumerate the small set of plausible locations:

      1. The current process's $HOME (the dashboard runs as the same user
         that owns the runner on this host — verified by the fleet topology
         described in CLAUDE.md).
      2. ``/home/<user>`` for each user whose name appears in the unit name
         (defensive — the unit name convention is ``actions.runner.<org>.<host>-<N>.service``
         and doesn't include a user, but we look anyway).

    Returns an ordered, de-duplicated list.
    """
    homes: list[Path] = []
    seen: set[str] = set()

    process_home = os.environ.get("HOME")
    if process_home:
        p = Path(process_home).resolve()
        homes.append(p)
        seen.add(str(p))

    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        p = Path(f"/home/{sudo_user}").resolve()
        key = str(p)
        if key not in seen and p.exists():
            homes.append(p)
            seen.add(key)

    # Last resort: enumerate /home/* — bounded and cheap.
    home_root = Path("/home")
    if home_root.is_dir():
        for child in home_root.iterdir():
            if not child.is_dir():
                continue
            key = str(child.resolve())
            if key not in seen:
                homes.append(child)
                seen.add(key)

    _ = unit  # reserved for future per-unit overrides.
    return homes


def _iter_runner_worktree_locks(home: Path) -> Iterable[Path]:
    """Yield per-worktree git lock files inside the runner's _work tree.

    The runner agent extracts each job under ``$HOME/actions-runners/runner-N/_work/...``.
    git lock files (``index.lock``, ``HEAD.lock``, etc.) inside any of those
    worktrees are candidates for cleanup if old enough.
    """
    runners_root = home / "actions-runners"
    if not runners_root.is_dir():
        return
    for runner_dir in runners_root.iterdir():
        if not runner_dir.is_dir():
            continue
        work = runner_dir / "_work"
        if not work.is_dir():
            continue
        # The runner can nest checkouts arbitrarily deep but the .git dir is
        # consistently one level inside the workspace. Use rglob with a small
        # cap to avoid pathological recursion on a corrupted runner.
        # Bound: at most 2000 .git/*.lock files per home dir per pass.
        count = 0
        for lock_name in _WORKTREE_LOCK_BASENAMES:
            for path in work.rglob(f".git/{lock_name}"):
                yield path
                count += 1
                if count >= 2000:
                    log.warning(
                        "worktree-lock scan hit 2000-file cap under %s; stopping early to avoid runaway recursion",
                        work,
                    )
                    return


def cleanup_runner_state(unit: str) -> dict[str, int]:
    """Remove stale git lock files left by an abruptly-stopped runner.

    Returns a small dict summarising what was removed (used by tests + logs).
    Never raises — best-effort. Locks younger than :data:`_STALE_LOCK_AGE_S`
    are left in place to avoid racing an actively-running git command.

    Args:
        unit: systemd unit name (e.g. ``actions.runner.org.host-3.service``).
              Currently used only for log context; reserved for future
              per-unit cleanup of the runner's own ``$RUNNER_TEMP``.
    """
    removed_home_locks = 0
    removed_worktree_locks = 0
    skipped_fresh = 0
    errors = 0

    now = time.time()

    for home in _candidate_runner_homes(unit):
        # Home-level locks (~/.gitconfig.lock) — remove unconditionally.
        # If a live process were still writing, the rename has either already
        # happened (lock is stale) or will overwrite our absence harmlessly.
        for basename in _KNOWN_LOCK_BASENAMES:
            lock = home / basename
            try:
                if lock.is_file():
                    lock.unlink()
                    removed_home_locks += 1
                    log.warning(
                        "cleanup_runner_state: removed stale %s for unit=%s",
                        lock,
                        unit,
                    )
            except OSError as exc:
                errors += 1
                log.warning(
                    "cleanup_runner_state: failed to remove %s: %s",
                    lock,
                    exc,
                )

        # Per-worktree locks — only if older than _STALE_LOCK_AGE_S to avoid
        # racing a live `git checkout` from a freshly-started job.
        for lock in _iter_runner_worktree_locks(home):
            try:
                st = lock.stat()
            except OSError as exc:
                errors += 1
                log.warning(
                    "cleanup_runner_state: failed to stat %s: %s",
                    lock,
                    exc,
                )
                continue
            age = now - st.st_mtime
            if age < _STALE_LOCK_AGE_S:
                skipped_fresh += 1
                continue
            try:
                lock.unlink()
                removed_worktree_locks += 1
                log.warning(
                    "cleanup_runner_state: removed stale %s (age=%.0fs) for unit=%s",
                    lock,
                    age,
                    unit,
                )
            except OSError as exc:
                errors += 1
                log.warning(
                    "cleanup_runner_state: failed to remove %s: %s",
                    lock,
                    exc,
                )

    summary = {
        "home_locks_removed": removed_home_locks,
        "worktree_locks_removed": removed_worktree_locks,
        "fresh_locks_skipped": skipped_fresh,
        "errors": errors,
    }
    if removed_home_locks or removed_worktree_locks:
        log.info("cleanup_runner_state(unit=%s) summary=%s", unit, summary)
    return summary
