from __future__ import annotations

import os
import sys
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
