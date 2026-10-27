"""Lease releases report actual outcomes rather than RM's success default (#3035)."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from staff import lease, reconcile, runner
from staff.plan import RunPlan
from staff.store import RUN_STATUSES, RunRecord, RunStore


def record(**fields):
    return RunRecord(
        id="reason-run",
        role="worker",
        provider="fake",
        model=None,
        machine="test",
        repo="Tools",
        target_kind="issue",
        target_ref="#1",
        prompt="test",
        **fields,
    )


def test_release_passes_reason_flag(tmp_path, monkeypatch):
    store = RunStore(tmp_path / "runs.db")
    rec = record(lease_id="staff-reason-run")
    store.create_run(rec)
    invoke = Mock()
    monkeypatch.setattr(lease, "rm_root", lambda: tmp_path)
    monkeypatch.setattr(lease, "run_module", invoke)
    lease.release(store, rec.id, repo=rec.repo, issue="1", agent="codex", reason="failed: crash")
    calls = [c for c in invoke.call_args_list if c.args[0] == "release_agent_lease"]
    assert len(calls) == 1
    args = calls[0].args
    assert args[args.index("--reason") + 1] == "failed: crash"


@pytest.mark.parametrize(
    "fields,expected",
    [
        ({"status": "succeeded", "pr_number": 42}, "completed: PR #42 opened"),
        ({"status": "succeeded", "pr_number": 42, "outcome": "reviewed PR #42"}, "completed: reviewed PR #42"),
        ({"status": "succeeded"}, "no-op: run finished without a PR"),
        (
            {
                "status": "failed",
                "failure_class": "timeout",
                "started_at": "2026-10-27T10:00:00Z",
                "ended_at": "2026-10-27T10:05:00Z",
            },
            "timeout: 5 min",
        ),
        ({"status": "failed", "failure_class": "timeout"}, "timeout: unknown min"),
        (
            {"status": "failed", "failure_class": "timeout", "started_at": "bad", "ended_at": "bad"},
            "timeout: unknown min",
        ),
        ({"status": "failed", "failure_class": "config"}, "failed: config"),
        ({"status": "failed"}, "failed: error"),
        ({"status": "cancelled"}, "cancelled"),
        ({"status": "needs_input"}, "skipped: needs input"),
        ({"status": "blocked"}, "released: status blocked"),
    ],
)
def test_release_reason_maps_terminal_status(fields, expected):
    assert lease.release_reason(record(**fields)) == expected


def test_release_reason_never_work_completed():
    for status in RUN_STATUSES:
        reason = lease.release_reason(record(status=status))
        assert reason and reason != "work completed"
        assert "\r" not in reason and "\n" not in reason
    assert lease.release_reason(None) == "released: status unknown"
    assert lease.release_reason(record(), crashed=True) == "failed: crash"
    assert (
        lease.release_reason(record(status="succeeded", pr_number=42, outcome="done\r\nnext")) == "completed: done next"
    )


@pytest.mark.parametrize("reason", ["", " ", "bad\nreason", "bad\rreason", None])
def test_release_rejects_invalid_reason_before_subprocess(reason, monkeypatch):
    invoke = Mock()
    monkeypatch.setattr(lease, "run_module", invoke)
    with pytest.raises(ValueError):
        lease.release(Mock(), "run", repo="Tools", issue="1", agent="codex", reason=reason)
    invoke.assert_not_called()


def test_runner_crash_releases_with_failed_reason(tmp_path, monkeypatch):
    store = RunStore(tmp_path / "runs.db")
    rec = record()
    store.create_run(rec)
    worker = runner.StaffRunner(store=store, adapters={"fake": SimpleNamespace(lease_agent="codex")})
    plan = RunPlan(
        role="worker",
        provider="fake",
        model=None,
        repo="Tools",
        target_kind="issue",
        target_ref="#1",
        operator_prompt="",
        prompt="",
        argv=[],
        branch="test",
        lease_ritual=True,
    )
    monkeypatch.setattr(runner.ops, "fail_if_cli_outdated", lambda *a: False)
    monkeypatch.setattr(runner.ops, "prepare_run_workdir", lambda *a: tmp_path)
    monkeypatch.setattr(lease, "acquire", lambda *a, **kw: "leased")
    monkeypatch.setattr(worker, "_execute", Mock(side_effect=RuntimeError("crash")))
    release = Mock()
    monkeypatch.setattr(lease, "release", release)
    monkeypatch.setattr(runner, "handle_run_status_change", Mock())
    monkeypatch.setattr(runner.retry_mod, "handle_post_execution_retry", Mock())
    worker._worker(rec, plan)
    release.assert_called_once()
    assert release.call_args.kwargs["reason"] == "failed: crash"


def test_reconcile_releases_with_restart_reason(monkeypatch):
    release = Mock()
    monkeypatch.setattr(lease, "release", release)
    reconcile._release_lease_with_retry(Mock(), record(lease_id="staff-reason-run"))
    release.assert_called_once()
    assert release.call_args.kwargs["reason"] == "orphaned: interrupted by backend restart"
