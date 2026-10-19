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

import asyncio
import os
import time
from pathlib import Path
from typing import Literal

ProbeStatus = Literal["ok", "degraded", "down"]

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


class RunnerBusyLockProbe:
    """Check for stale runner-busy lockfiles (issue #1987).

    A lockfile in ``RUNNER_BUSY_LOCK_DIR`` is stale when its recorded
    ``worker_pid`` is dead or no longer a ``Runner.Worker`` (see
    ``runner_busy_lock``), or -- for legacy locks without ``worker_pid`` --
    when it is older than the threshold (default 10 minutes).
    """

    name = "runner_busy_locks"

    def __init__(
        self,
        lock_dir: str | Path | None = None,
        stale_threshold_seconds: float = 600.0,
        cache_ttl_seconds: float = 10.0,
    ) -> None:
        assert cache_ttl_seconds >= 0.0, "cache_ttl_seconds must be >= 0"
        self._lock_dir = (
            Path(lock_dir)
            if lock_dir is not None
            else Path(os.environ.get("RUNNER_BUSY_LOCK_DIR", "/var/run/runner-busy"))
        )
        self._stale_threshold = stale_threshold_seconds
        self._cache_ttl = cache_ttl_seconds
        self._cached: tuple[float, ProbeStatus, str | None] | None = None

    async def check(self) -> tuple[ProbeStatus, str | None]:
        if self._cache_ttl > 0 and self._cached is not None:
            ts, status, detail = self._cached
            if (time.monotonic() - ts) < self._cache_ttl:
                return status, detail

        status, detail = await asyncio.to_thread(self._check_busy_locks)
        self._cached = (time.monotonic(), status, detail)
        return status, detail

    def _check_busy_locks(self) -> tuple[ProbeStatus, str | None]:
        if not self._lock_dir.is_dir():
            return "ok", None

        now = time.time()
        stale_locks: list[str] = []

        try:
            lock_files = list(self._lock_dir.glob("*.lock"))
        except OSError as exc:
            return "degraded", f"unable to list locks in {self._lock_dir}: {exc}"

        if not lock_files:
            return "ok", None

        for lock_file in lock_files:
            try:
                age = now - lock_file.stat().st_mtime
                text = lock_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # Issue #1987: shared rule (runner_busy_lock.py). A recorded
            # worker_pid decides on its own; legacy locks fall back to age.
            if lock_is_stale_by_worker_pid(text):
                stale_locks.append(f"{lock_file.name} (worker_pid dead or reused, age={int(age)}s)")
            elif worker_pid(text) is None and age > self._stale_threshold:
                stale_locks.append(f"{lock_file.name} (age={int(age)}s)")

        if stale_locks:
            return (
                "degraded",
                f"stale runner-busy lock(s) detected: {', '.join(stale_locks)}",
            )

        return "ok", None
