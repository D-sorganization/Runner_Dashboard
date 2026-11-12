from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

from node_auto_deploy.deploy_lock import (  # noqa: E402
    format_lock_body,
    lock_fields,
    lock_is_stale,
    try_reclaim_stale_lock,
    write_lock_metadata,
)
from node_auto_deploy.transaction import deployment_lock  # noqa: E402


def test_lock_body_records_pid_started_at_and_host(tmp_path: Path) -> None:
    lock = tmp_path / ".node-auto-deploy.lock"
    lock.touch()
    body = write_lock_metadata(lock, host="test-host", clock=lambda: 1_700_000_000.5)
    assert body == format_lock_body(pid=os.getpid(), started_at=1_700_000_000.5, host="test-host")
    assert lock_fields(lock.read_text(encoding="ascii")) == {
        "pid": str(os.getpid()),
        "started_at": "1700000000.500000",
        "host": "test-host",
    }


def test_deployment_lock_writes_metadata(tmp_path: Path) -> None:
    live = tmp_path / "dashboard"
    live.mkdir()
    with deployment_lock(live):
        lock = tmp_path / "dashboard-releases" / ".node-auto-deploy.lock"
        fields = lock_fields(lock.read_text(encoding="ascii"))
        assert fields["pid"] == str(os.getpid())
        assert "started_at" in fields
        assert fields["host"]


def test_stale_lock_is_reclaimed_when_owner_pid_is_dead(tmp_path: Path, monkeypatch) -> None:
    live = tmp_path / "dashboard"
    live.mkdir()
    releases = tmp_path / "dashboard-releases"
    releases.mkdir(parents=True)
    lock = releases / ".node-auto-deploy.lock"
    proc = tmp_path / "proc"
    proc.mkdir()
    monkeypatch.setenv("PROC_ROOT", str(proc))
    lock.write_text("pid=424242\nstarted_at=1.0\nhost=node-a\n", encoding="ascii")

    with deployment_lock(live):
        fields = lock_fields(lock.read_text(encoding="ascii"))
        assert fields["pid"] == str(os.getpid())
        assert fields["pid"] != "424242"


def test_live_lock_is_not_reclaimed(tmp_path: Path, monkeypatch) -> None:
    live = tmp_path / "dashboard"
    live.mkdir()
    releases = tmp_path / "dashboard-releases"
    releases.mkdir(parents=True)
    lock = releases / ".node-auto-deploy.lock"
    proc = tmp_path / "proc" / str(os.getpid())
    proc.mkdir(parents=True)
    monkeypatch.setenv("PROC_ROOT", str(tmp_path / "proc"))
    lock.write_text(
        format_lock_body(pid=os.getpid(), started_at=time.time(), host="node-a"),
        encoding="ascii",
    )

    try:
        with deployment_lock(live):
            pass
        raised = False
    except RuntimeError as exc:
        raised = True
        assert "another deployment owns" in str(exc)
    assert raised


def test_lock_is_stale_when_ttl_elapsed() -> None:
    clock = lambda: 100.0  # noqa: E731
    body = format_lock_body(pid=1, started_at=10.0, host="node-a")
    assert lock_is_stale(body, host="node-a", clock=clock, ttl_seconds=60)
    body = format_lock_body(pid=1, started_at=50.0, host="node-a")
    assert not lock_is_stale(body, host="node-a", clock=clock, ttl_seconds=60)


def test_try_reclaim_stale_lock_removes_dead_owner_file(tmp_path: Path) -> None:
    lock = tmp_path / ".node-auto-deploy.lock"
    lock.write_text("pid=999001\n", encoding="ascii")
    assert try_reclaim_stale_lock(lock, proc_root=tmp_path / "proc")
    assert not lock.exists()


def test_concurrent_stale_reclaim_only_one_succeeds(tmp_path: Path) -> None:
    lock = tmp_path / ".node-auto-deploy.lock"
    lock.write_text("pid=999001\nstarted_at=1.0\nhost=node-a\n", encoding="ascii")
    proc = tmp_path / "proc"
    proc.mkdir()
    barrier = threading.Barrier(2)
    results: list[bool] = []

    def reclaim() -> None:
        barrier.wait()
        results.append(
            try_reclaim_stale_lock(
                lock,
                host="node-a",
                proc_root=proc,
                clock=lambda: 100.0,
                ttl_seconds=60,
            )
        )

    threads = [threading.Thread(target=reclaim), threading.Thread(target=reclaim)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(True) == 1
    assert results.count(False) == 1
    assert not lock.exists()


def test_try_reclaim_aborts_when_lock_path_replaced_before_unlink(tmp_path: Path, monkeypatch) -> None:
    lock = tmp_path / ".node-auto-deploy.lock"
    lock.write_text("pid=999001\nstarted_at=1.0\nhost=node-a\n", encoding="ascii")
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / str(os.getpid())).mkdir(parents=True)
    pause = threading.Event()
    replaced = threading.Event()
    original_stat = os.stat

    def stat_with_pause(path: str | os.PathLike[str], *args: object, **kwargs: object) -> os.stat_result:
        if Path(path) == lock:
            pause.set()
            replaced.wait(timeout=2.0)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat_with_pause)

    def replacer() -> None:
        assert pause.wait(timeout=2.0)
        lock.unlink(missing_ok=True)
        write_lock_metadata(lock, host="node-a", clock=lambda: 50.0)
        replaced.set()

    outcome: dict[str, bool] = {}

    def reclaim() -> None:
        outcome["reclaimed"] = try_reclaim_stale_lock(
            lock,
            host="node-a",
            proc_root=proc,
            clock=lambda: 100.0,
            ttl_seconds=60,
        )

    reclaim_thread = threading.Thread(target=reclaim)
    replace_thread = threading.Thread(target=replacer)
    reclaim_thread.start()
    replace_thread.start()
    reclaim_thread.join(timeout=5.0)
    replace_thread.join(timeout=5.0)
    assert not reclaim_thread.is_alive()
    assert not replace_thread.is_alive()
    assert outcome.get("reclaimed") is False
    assert lock.exists()
    assert not lock_is_stale(
        lock.read_text(encoding="ascii"),
        lock_path=lock,
        host="node-a",
        proc_root=proc,
        clock=lambda: 50.0,
        ttl_seconds=60,
    )
