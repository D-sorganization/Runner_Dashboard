"""Per-PR CI-fix locks: TTL expiry and run-liveness release (#1881)."""

from __future__ import annotations

from ci_fix_locks import CIFixLockManager


def test_lock_expires_after_ttl() -> None:
    now = [1000.0]
    mgr = CIFixLockManager(ttl_seconds=60, clock=lambda: now[0])
    assert mgr.acquire("Runner_Dashboard", 42) is True
    assert mgr.acquire("Runner_Dashboard", 42) is False
    assert mgr.get_lock_info("Runner_Dashboard", 42)["expires_at"]
    now[0] += 61
    assert mgr.is_locked("Runner_Dashboard", 42) is False
    assert mgr.acquire("Runner_Dashboard", 42) is True


def test_lock_released_when_its_run_ends() -> None:
    live = {"run-1": True}
    mgr = CIFixLockManager(ttl_seconds=3600, run_is_live=lambda run_id: live.get(run_id, False))
    assert mgr.acquire("Runner_Dashboard", 42) is True
    assert mgr.attach_run("Runner_Dashboard", 42, "run-1") is True
    assert mgr.is_locked("Runner_Dashboard", 42) is True
    live["run-1"] = False
    assert mgr.is_locked("Runner_Dashboard", 42) is False
    assert mgr.list_locks() == []


def test_lock_liveness_errors_keep_the_lock_until_ttl() -> None:
    def boom(run_id: str) -> bool:
        raise RuntimeError("store unavailable")

    mgr = CIFixLockManager(ttl_seconds=3600, run_is_live=boom)
    mgr.acquire("Runner_Dashboard", 42)
    mgr.attach_run("Runner_Dashboard", 42, "run-1")
    assert mgr.is_locked("Runner_Dashboard", 42) is True
