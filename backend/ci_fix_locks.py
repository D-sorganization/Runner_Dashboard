"""Per-PR concurrency locks for RD-1 CI-fix sessions (#1846), with expiry (#1881)."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from time_utils import utc_now_iso

__all__ = ["DEFAULT_LOCK_TTL_SECONDS", "GLOBAL_CI_FIX_LOCK_MGR", "CIFixLockManager"]

# #1881: a lock outlives neither its staff run nor this TTL (default 2 h, the lease length).
DEFAULT_LOCK_TTL_SECONDS = float(os.environ.get("CI_FIX_LOCK_TTL_SECONDS", "7200"))


class CIFixLockManager:
    """Thread-safe per-PR concurrency lock for CI-fix sessions, with expiry (#1881).

    A lock is dropped when its TTL elapses or, once a staff run is attached, as soon as
    ``run_is_live(run_id)`` reports the run finished. A liveness probe that raises keeps
    the lock (the TTL still bounds it).
    """

    def __init__(
        self,
        ttl_seconds: float | None = None,
        clock: Callable[[], float] = time.time,
        run_is_live: Callable[[str], bool] | None = None,
    ) -> None:
        self._ttl = DEFAULT_LOCK_TTL_SECONDS if ttl_seconds is None else float(ttl_seconds)
        assert self._ttl > 0, "lock TTL must be positive"  # noqa: S101 - constructor contract
        self._clock = clock
        self._run_is_live = run_is_live
        self._lock = threading.Lock()
        self._active_locks: dict[tuple[str, int], dict[str, Any]] = {}
        self._deadlines: dict[tuple[str, int], float] = {}

    @staticmethod
    def _key(repo: str, pr_number: int) -> tuple[str, int]:
        return (repo.strip().lower(), pr_number)

    def _ended(self, key: tuple[str, int], now: float) -> bool:
        if now >= self._deadlines[key]:
            return True
        run_id = self._active_locks[key].get("run_id")
        if not run_id or self._run_is_live is None:
            return False
        try:
            return not self._run_is_live(run_id)
        except Exception:  # noqa: BLE001 - an unreadable run store keeps the lock until its TTL
            return False

    def _purge(self) -> None:
        now = self._clock()
        for key in [k for k in self._active_locks if self._ended(k, now)]:
            self._active_locks.pop(key, None)
            self._deadlines.pop(key, None)

    def acquire(self, repo: str, pr_number: int, session_id: str = "") -> bool:
        """Acquire single-session concurrency lock for a PR."""
        key = self._key(repo, pr_number)
        with self._lock:
            self._purge()
            if key in self._active_locks:
                return False
            deadline = self._clock() + self._ttl
            self._deadlines[key] = deadline
            self._active_locks[key] = {
                "repo": repo,
                "pr_number": pr_number,
                "session_id": session_id,
                "run_id": "",
                "acquired_at": utc_now_iso(),
                "expires_at": datetime.fromtimestamp(deadline, UTC).isoformat(),
            }
            return True

    def attach_run(self, repo: str, pr_number: int, run_id: str) -> bool:
        """Bind a held lock to the staff run it launched, so the lock ends with the run."""
        key = self._key(repo, pr_number)
        with self._lock:
            info = self._active_locks.get(key)
            if info is None:
                return False
            info["run_id"] = run_id
            return True

    def release(self, repo: str, pr_number: int) -> bool:
        """Release concurrency lock for a PR."""
        key = self._key(repo, pr_number)
        with self._lock:
            self._deadlines.pop(key, None)
            return self._active_locks.pop(key, None) is not None

    def is_locked(self, repo: str, pr_number: int) -> bool:
        """Check if PR currently has an active CI-fix session."""
        key = self._key(repo, pr_number)
        with self._lock:
            self._purge()
            return key in self._active_locks

    def get_lock_info(self, repo: str, pr_number: int) -> dict[str, Any] | None:
        """Return lock metadata if locked."""
        key = self._key(repo, pr_number)
        with self._lock:
            self._purge()
            info = self._active_locks.get(key)
            return dict(info) if info is not None else None

    def list_locks(self) -> list[dict[str, Any]]:
        """Return snapshot of all active locks."""
        with self._lock:
            self._purge()
            return [dict(info) for info in self._active_locks.values()]


def _staff_run_is_live(run_id: str) -> bool:
    """Whether a staff run is still queued or working (the default lock liveness probe)."""
    from staff.store import ACTIVE_STATUSES, get_store  # noqa: PLC0415

    rec = get_store().get_run(run_id)
    return rec is not None and rec.status in (*ACTIVE_STATUSES, "needs_input")


# Global singleton instance for application lifetime
GLOBAL_CI_FIX_LOCK_MGR = CIFixLockManager(run_is_live=_staff_run_is_live)
