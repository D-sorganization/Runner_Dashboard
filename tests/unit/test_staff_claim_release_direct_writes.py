"""Direct status writes release the run's ``claim:*`` label (#3033 PR1: RD-08, RD-43).

The worker crash (``failed``), the lease-blocked run (``blocked``) and the
dispatch ``launch_failed`` path write the run status directly instead of going
through ``handle_run_status_change``. Each must still release the claim label,
or the issue stays falsely claimed and redispatch is 409'd. Fakes only: no
provider CLI, no Repository_Management checkout, no GitHub.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from coordination.rm_scripts import ScriptResult
from staff import claim_guard, dispatch_service
from staff import lease as lease_mod
from staff import runner as runner_mod

REPO = "D-sorganization/Runner_Dashboard"


@pytest.fixture(autouse=True)
def _clean_applied() -> Any:
    claim_guard.reset_applied()
    yield
    claim_guard.reset_applied()


def _plan(**overrides: Any) -> SimpleNamespace:
    base = {
        "provider": "fake",
        "execution_mode": "local",
        "lease_ritual": True,
        "repo": "Runner_Dashboard",
        "issue_number": "7",
        "branch": "staff/run-1",
    }
    return SimpleNamespace(**{**base, **overrides})


def _worker_runner(monkeypatch: pytest.MonkeyPatch, store: MagicMock) -> runner_mod.StaffRunner:
    """A runner whose worker reaches lease + execute without touching disk or a CLI."""
    monkeypatch.setattr(runner_mod.ops, "fail_if_cli_outdated", lambda *_a: False)
    monkeypatch.setattr(runner_mod.ops, "prepare_run_workdir", lambda *_a: Path("wt"))
    monkeypatch.setattr(runner_mod.retry_mod, "handle_post_execution_retry", lambda *_a: None)
    return runner_mod.StaffRunner(store=store, adapters={"fake": SimpleNamespace(lease_agent="claude")})


def _fake_rm(monkeypatch: pytest.MonkeyPatch, claim_output: str) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def fake_run_module(module: str, *args: str, root: Any) -> ScriptResult:
        calls.append((module, *args))
        return ScriptResult(0, claim_output if module == "check_agent_claim" else "{}", "")

    monkeypatch.setattr(lease_mod, "rm_root", lambda: Path("rm"))
    monkeypatch.setattr(lease_mod, "run_module", fake_run_module)
    return calls


def test_lease_acquire_invoked_from_worker_with_lease_ritual(monkeypatch: pytest.MonkeyPatch) -> None:
    """RD-43: the worker runs the real lease ritual and hands its note to ``_execute``."""
    store = MagicMock()
    store.get_run.return_value = SimpleNamespace(lease_id="staff-run-1")
    calls = _fake_rm(monkeypatch, '{"held": false}')
    runner = _worker_runner(monkeypatch, store)
    executed: list[str] = []
    monkeypatch.setattr(runner, "_execute", lambda _rec, _plan, _wd, note: executed.append(note))
    monkeypatch.setattr(runner_mod, "handle_run_status_change", MagicMock())

    runner._worker(SimpleNamespace(id="run-1", target_kind="issue"), _plan())

    modules = [c[0] for c in calls]
    assert modules[:3] == ["check_agent_claim", "post_agent_lease", "agent_communicate"]
    assert executed and "session=staff-run-1" in executed[0]
    store.update_run.assert_any_call("run-1", lease_id="staff-run-1")


def test_claim_label_released_when_lease_blocks_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """RD-08: a lease-blocked worker leaves the run ``blocked`` and releases its claim label."""
    store = MagicMock()
    store.get_run.return_value = SimpleNamespace(lease_id="")
    _fake_rm(monkeypatch, '{"held": true, "agent": "other"}')
    released: list[tuple[str, str]] = []
    monkeypatch.setattr(lease_mod.claim_guard, "release_claim", lambda run_id, st: released.append((run_id, st)))
    runner = _worker_runner(monkeypatch, store)
    monkeypatch.setattr(runner, "_execute", MagicMock(side_effect=AssertionError("blocked run must not execute")))

    runner._worker(SimpleNamespace(id="run-1", target_kind="issue"), _plan())

    statuses = [c.kwargs.get("status") for c in store.update_run.call_args_list]
    assert statuses == ["preparing", "blocked"]
    assert released == [("run-1", "blocked")]


def test_claim_label_released_on_worker_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """RD-08: a worker crash writes ``failed`` and routes it through ``handle_run_status_change``."""
    store = MagicMock()
    crashed = SimpleNamespace(id="run-1", target_kind="issue", status="failed")
    store.get_run.return_value = crashed
    runner = _worker_runner(monkeypatch, store)
    monkeypatch.setattr(runner, "_execute", MagicMock(side_effect=RuntimeError("boom")))
    status_change = MagicMock()
    monkeypatch.setattr(runner_mod, "handle_run_status_change", status_change)

    runner._worker(SimpleNamespace(id="run-1", target_kind="issue"), _plan(lease_ritual=False))

    assert [c.args[1] for c in status_change.call_args_list] == ["preparing", "failed"]
    assert status_change.call_args_list[-1].args[0] is crashed


def test_claim_label_released_on_launch_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """RD-08: a dispatch whose worker never starts is ``failed`` and its claim released."""
    rec = SimpleNamespace(id="run-1", target_kind="issue", status="failed")
    runner = SimpleNamespace(store=MagicMock())
    runner.store.get_run.return_value = rec
    status_change = MagicMock()
    monkeypatch.setattr(dispatch_service, "handle_run_status_change", status_change)
    monkeypatch.setattr(dispatch_service, "record_audit", lambda **_kw: None)

    dispatch_service._record_launch_failure(runner, "run-1", RuntimeError("no thread"), {})

    assert runner.store.update_run.call_args.kwargs["failure_class"] == "launch_failed"
    status_change.assert_called_once_with(rec, "failed")


def test_release_claim_accepts_blocked_status() -> None:
    """RD-08: ``blocked`` is not terminal, but it ends the run, so its label is released."""
    claim_guard._applied["run-1"] = (REPO, 7, "claim:staff")
    calls: list[tuple[str, str]] = []

    removed = claim_guard.release_claim(
        "run-1", "blocked", write=lambda ep, m: calls.append((ep, m)), others_active=lambda *_: False
    )

    assert removed is True
    assert calls == [(f"/repos/{REPO}/issues/7/labels/claim%3Astaff", "DELETE")]
    assert "blocked" not in claim_guard.TERMINAL_STATUSES  # run-status semantics unchanged
