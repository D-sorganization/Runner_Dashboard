"""Staleness rule for runner-busy lockfiles (issue #1987).

The JOB_STARTED hook (``deploy/runner-hooks/job-started.sh``) writes
``<RUNNER_BUSY_LOCK_DIR>/<runner>.lock`` with two pid fields:

* ``pid=`` -- the hook shell itself. It exits as soon as the lock is written,
  so it is dead for the entire job and says nothing about liveness. Kept for
  compatibility only.
* ``worker_pid=`` -- the hook's ``Runner.Worker`` ancestor, when one was found.

Rule (identical to ``lock_worker_pid_stale`` in ``deploy/runner-cleanup.sh``
and ``_lock_worker_pid_stale`` in ``deploy/runner-scheduler.py``): a lock is
stale-by-pid ONLY when it carries ``worker_pid=`` AND that pid is dead or its
cmdline no longer contains ``Runner.Worker`` (pid reuse). A lock without
``worker_pid`` (older hook versions), with a malformed one, or on a host with
no proc filesystem is never stale-by-pid; callers fall back to their age rule.
"""

from __future__ import annotations

import os
from pathlib import Path

WORKER_MARKER = "Runner.Worker"


def lock_fields(text: str) -> dict[str, str]:
    """Parse ``key=value`` lines; the first occurrence of a key wins."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key not in fields:
            fields[key] = value.strip()
    return fields


def worker_pid(text: str) -> int | None:
    """Return the lock's ``worker_pid`` or None when absent/malformed."""
    value = lock_fields(text).get("worker_pid", "")
    return int(value) if value.isdigit() and int(value) > 0 else None


def _proc_root(proc_root: Path | None) -> Path:
    return proc_root if proc_root is not None else Path(os.environ.get("PROC_ROOT", "/proc"))


def lock_is_stale_by_worker_pid(text: str, proc_root: Path | None = None) -> bool:
    """True only when ``worker_pid`` is recorded and is dead or not a Worker.

    *proc_root* defaults to ``$PROC_ROOT`` or ``/proc`` (injectable for tests).
    """
    pid = worker_pid(text)
    if pid is None:
        return False
    root = _proc_root(proc_root)
    if not root.is_dir():
        return False
    pid_dir = root / str(pid)
    if not pid_dir.is_dir():
        return True
    try:
        cmdline = (pid_dir / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except FileNotFoundError:
        return True  # exited between the two checks
    except OSError:
        return False  # alive but unreadable: not provably stale
    return WORKER_MARKER not in cmdline
