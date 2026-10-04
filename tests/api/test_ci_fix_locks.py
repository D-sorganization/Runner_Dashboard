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


def test_lock_is_shared_across_managers_on_one_store(tmp_path) -> None:
    """Two dashboard workers (WORKERS>1) share one sqlite store: only one can hold a PR."""
    db = tmp_path / "locks.db"
    worker_a = CIFixLockManager(path=db)
    worker_b = CIFixLockManager(path=db)
    assert worker_a.acquire("Runner_Dashboard", 42, session_id="a") is True
    assert worker_b.acquire("Runner_Dashboard", 42, session_id="b") is False
    assert worker_b.is_locked("runner_dashboard", 42) is True
    assert worker_a.attach_run("Runner_Dashboard", 42, "run-1") is True
    assert worker_b.get_lock_info("Runner_Dashboard", 42)["run_id"] == "run-1"
    assert worker_b.release("Runner_Dashboard", 42) is True
    assert worker_a.acquire("Runner_Dashboard", 42) is True


def test_shared_lock_expiry_is_seen_by_every_worker(tmp_path) -> None:
    now = [1000.0]
    db = tmp_path / "locks.db"
    worker_a = CIFixLockManager(path=db, ttl_seconds=60, clock=lambda: now[0])
    worker_b = CIFixLockManager(path=db, ttl_seconds=60, clock=lambda: now[0])
    assert worker_a.acquire("Runner_Dashboard", 7) is True
    now[0] += 61
    assert worker_b.acquire("Runner_Dashboard", 7) is True
    assert [lock["session_id"] for lock in worker_a.list_locks()] == [""]


def _race_acquire(db: str, results: object) -> None:
    results.put(CIFixLockManager(path=__import__("pathlib").Path(db)).acquire("Runner_Dashboard", 99))  # type: ignore[attr-defined]


def test_exactly_one_process_wins_a_concurrent_acquire(tmp_path) -> None:
    """Separate OS processes racing for one PR: exactly one acquires (WORKERS>1, #1887 review)."""
    import multiprocessing

    ctx = multiprocessing.get_context("fork")
    results = ctx.Queue()
    procs = [ctx.Process(target=_race_acquire, args=(str(tmp_path / "locks.db"), results)) for _ in range(6)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=30)
    outcomes = [results.get(timeout=5) for _ in procs]
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 5
