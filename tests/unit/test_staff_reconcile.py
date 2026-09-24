"""Tests for staff run reconciliation on dashboard restart (issue #1293, SC-A4)."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fleet_events import EventStore
from staff import runner as runner_mod
from staff import store as store_mod
from staff.reconcile import reconcile_orphaned_runs
from staff.roles import RoleSpec
from staff.store import RunRecord, RunStore


@pytest.fixture
def temp_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RunStore:
    db_path = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_path))
    store_mod.reset_store()
    return store_mod.get_store()


@pytest.fixture
def staff_runner(temp_store: RunStore) -> runner_mod.StaffRunner:
    runner_mod.reset_runner()
    return runner_mod.StaffRunner(store=temp_store, machine="test-node")


def _make_run(
    store: RunStore,
    run_id: str,
    status: str,
    role: str = "worker",
    **kwargs: Any,
) -> RunRecord:
    kwargs.setdefault("repo", "Tools")
    kwargs.setdefault("machine", "test-node")
    kwargs.setdefault("target_kind", "issue")
    kwargs.setdefault("target_ref", "#100")
    kwargs.setdefault("prompt", "test prompt")
    rec = RunRecord(
        id=run_id,
        role=role,
        provider="fake",
        model=None,
        status=status,
        **kwargs,
    )
    store.create_run(rec)
    return rec


def test_active_runs_reconciled_to_failed_orphaned(temp_store: RunStore, staff_runner: runner_mod.StaffRunner) -> None:
    """All queued, preparing, and running runs are marked failed with failure_class=orphaned."""
    _make_run(temp_store, "run-q1", "queued")
    _make_run(temp_store, "run-p1", "preparing", started_at="2026-09-24T12:00:00Z")
    _make_run(temp_store, "run-r1", "running", started_at="2026-09-24T12:05:00Z")
    _make_run(temp_store, "run-d1", "succeeded", ended_at="2026-09-24T12:10:00Z")

    event_store = EventStore()
    reconciled = reconcile_orphaned_runs(staff_runner, event_store=event_store)

    assert set(reconciled) == {"run-q1", "run-p1", "run-r1"}
    assert temp_store.active_runs() == []

    # Check updated records
    q_after = temp_store.get_run("run-q1")
    assert q_after is not None
    assert q_after.status == "failed"
    assert q_after.failure_class == "orphaned"
    assert q_after.ended_at is not None
    assert "orphaned" in q_after.error

    r_after = temp_store.get_run("run-r1")
    assert r_after is not None
    assert r_after.status == "failed"
    assert r_after.failure_class == "orphaned"

    d_after = temp_store.get_run("run-d1")
    assert d_after is not None
    assert d_after.status == "succeeded"

    # Verify fleet events were emitted
    events = event_store.recent()
    orphaned_events = [e for e in events if e.kind == "staff_run_orphaned"]
    assert len(orphaned_events) == 3
    run_ids_in_events = {e.title.split()[2] for e in orphaned_events}
    assert run_ids_in_events == {"run-q1", "run-p1", "run-r1"}


def test_child_pid_terminated(temp_store: RunStore, staff_runner: runner_mod.StaffRunner) -> None:
    """If a child PID is recorded on an active run, it is terminated upon reconciliation."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert proc.poll() is None  # alive
        _make_run(temp_store, "run-proc", "running", pid=proc.pid)

        reconcile_orphaned_runs(staff_runner, event_store=EventStore())

        # Process should be terminated
        dead = False
        for _ in range(50):
            if proc.poll() is not None:
                dead = True
                break
            time.sleep(0.1)
        assert dead, f"Process {proc.pid} was not terminated"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_unpushed_worktree_preserved(
    temp_store: RunStore, staff_runner: runner_mod.StaffRunner, tmp_path: Path
) -> None:
    """A worktree with unpushed commits or dirty files is preserved and its path recorded on the run."""
    # Create a git repo
    repo_dir = tmp_path / "origin"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo_dir)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.email", "test@test.local"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "Test"], check=True)
    (repo_dir / "README.md").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-m", "init"], check=True)

    # Create worktree
    wt_dir = tmp_path / "worktree-dirty"
    subprocess.run(
        [
            "git",
            "-C",
            str(repo_dir),
            "worktree",
            "add",
            "-b",
            "staff-test",
            str(wt_dir),
        ],
        check=True,
    )

    # Make uncommitted dirty change in worktree
    (wt_dir / "uncommitted.txt").write_text("dirty content", encoding="utf-8")

    _make_run(temp_store, "run-dirty", "running", workdir=str(wt_dir), repo="Tools")

    reconcile_orphaned_runs(staff_runner, event_store=EventStore())

    # Worktree MUST still exist!
    assert wt_dir.exists()
    rec = temp_store.get_run("run-dirty")
    assert rec is not None
    assert rec.status == "failed"
    assert rec.failure_class == "orphaned"
    assert str(wt_dir) in rec.error


def test_clean_worktree_removed(temp_store: RunStore, staff_runner: runner_mod.StaffRunner, tmp_path: Path) -> None:
    """A worktree with no unpushed commits or dirty files is removed on reconciliation."""
    repo_dir = tmp_path / "origin"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo_dir)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.email", "test@test.local"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "Test"], check=True)
    (repo_dir / "README.md").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-m", "init"], check=True)

    wt_dir = tmp_path / "worktree-clean"
    subprocess.run(
        [
            "git",
            "-C",
            str(repo_dir),
            "worktree",
            "add",
            "-b",
            "staff-clean",
            str(wt_dir),
        ],
        check=True,
    )

    _make_run(temp_store, "run-clean", "running", workdir=str(wt_dir), repo="Tools")

    with patch("staff.workspace.find_repo_checkout", return_value=repo_dir):
        reconcile_orphaned_runs(staff_runner, event_store=EventStore())

    assert not wt_dir.exists()


def test_lease_release_failure_retried_in_background_never_blocks(
    temp_store: RunStore, staff_runner: runner_mod.StaffRunner
) -> None:
    """Lease release failure does not block reconciliation or raise an error."""
    _make_run(temp_store, "run-lease", "running", lease_id="staff-run-lease")

    with patch("staff.lease.release", side_effect=RuntimeError("RM network failure")):
        reconcile_orphaned_runs(staff_runner, event_store=EventStore())

    rec = temp_store.get_run("run-lease")
    assert rec is not None
    assert rec.status == "failed"
    assert rec.failure_class == "orphaned"


def test_start_scheduler_invokes_reconciliation(temp_store: RunStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling start_scheduler() triggers reconciliation of active runs."""
    from routers.staff_schedule import start_scheduler
    from staff.runner import get_runner

    runner = get_runner()
    _make_run(temp_store, "run-sched-boot", "running", machine=runner.machine)
    assert len(temp_store.active_runs()) == 1

    monkeypatch.setenv("STAFF_SCHEDULER_ENABLED", "0")  # disable scheduler ticker, but verify reconcile runs
    start_scheduler()

    assert temp_store.active_runs() == []
    rec = temp_store.get_run("run-sched-boot")
    assert rec is not None
    assert rec.status == "failed"
    assert rec.failure_class == "orphaned"


def test_role_schedulable_again_after_reconciliation(
    temp_store: RunStore, staff_runner: runner_mod.StaffRunner
) -> None:
    """Before reconciliation, an orphaned run blocks role scheduling. After reconciliation, the role gate passes."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from staff.budget import BudgetGuard
    from staff.holds import HoldsList
    from staff.scheduler import StaffScheduler

    role = RoleSpec(
        name="worker",
        title="Worker",
        instructions="Work",
        providers=("fake",),
        schedule="0 22 * * *",
        window={"start": "00:00", "end": "23:59"},
        repos=("Tools",),
        budget_usd_per_run=5.0,
        budget_usd_per_day=20.0,
    )

    scheduler = StaffScheduler(
        runner=staff_runner,
        holds=HoldsList(roles_loader=lambda: {"worker": role}),
        budget=BudgetGuard(temp_store),
    )

    now = datetime(2026, 9, 24, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles"))

    # When an active run exists for worker:
    _make_run(temp_store, "run-blocking", "running", role="worker")
    eval_before = scheduler.evaluate(role, now)
    assert eval_before["active_run"] == "run-blocking"
    assert "still active" in scheduler._blocker(eval_before)

    # Reconcile!
    reconcile_orphaned_runs(staff_runner, event_store=EventStore())

    # Now the role gate opens:
    eval_after = scheduler.evaluate(role, now)
    assert eval_after["active_run"] is None
    assert scheduler._blocker(eval_after) == ""


@pytest.mark.asyncio
async def test_summary_attention_includes_orphaned_runs(
    temp_store: RunStore,
    staff_runner: runner_mod.StaffRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Orphaned runs surface in summary.attention with failure_class=orphaned."""
    from routers.staff import summary

    monkeypatch.setattr("routers.staff.get_runner", lambda: staff_runner)
    monkeypatch.setattr("staff.fleet.peer_nodes", lambda: {})

    _make_run(temp_store, "run-broken", "running")
    reconcile_orphaned_runs(staff_runner, event_store=EventStore())

    result = await summary()
    attention_ids = {a["id"] for a in result["attention"]}
    assert "run-broken" in attention_ids
    broken_item = next(a for a in result["attention"] if a["id"] == "run-broken")
    assert broken_item["status"] == "failed"
    assert broken_item["failure_class"] == "orphaned"
