"""The lease ritual must not abort on a hold it caused itself (#4063).

``claim_guard.apply_claim`` tags an admitted run's issue with its own role
label (``claim:staff``/``claim:issue-remediator``/``claim:pr-remediator``)
before the worker reaches ``lease.acquire``. RM's ``check_agent_claim`` then
reports that bare label as ``held`` (no RM lease posted yet), which must not
be mistaken for a foreign claim and abort the run.
"""

import json
from unittest.mock import Mock

from coordination.rm_scripts import ScriptResult
from staff import lease
from staff.store import RunRecord, RunStore


def record(**fields):
    return RunRecord(
        id="self-claim-run",
        role="worker",
        provider="fake",
        model=None,
        machine="test",
        repo="Runner_Dashboard",
        target_kind="issue",
        target_ref="#4063",
        prompt="test",
        **fields,
    )


def _invoke_stub(results: dict[str, ScriptResult]):
    def invoke(name, *_args, **_kwargs):
        return results[name]

    return invoke


def test_acquire_continues_on_own_admission_label(tmp_path, monkeypatch):
    store = RunStore(tmp_path / "runs.db")
    store.create_run(record())
    monkeypatch.setattr(lease, "rm_root", lambda: tmp_path)
    held_by_own_label = ScriptResult(
        rc=0,
        stdout=json.dumps({"held": True, "agent": "staff", "reason": "claim label claim:staff (no active lease)"}),
        stderr="",
    )
    monkeypatch.setattr(
        lease,
        "run_module",
        _invoke_stub(
            {
                "check_agent_claim": held_by_own_label,
                "post_agent_lease": ScriptResult(rc=0, stdout="{}", stderr=""),
                "agent_communicate": ScriptResult(rc=0, stdout="{}", stderr=""),
            }
        ),
    )
    note = lease.acquire(store, "self-claim-run", repo="Runner_Dashboard", issue="4063", agent="claude", branch="b")
    assert note is not None
    rec = store.get_run("self-claim-run")
    assert rec.status != "blocked"


def test_acquire_blocks_on_genuine_foreign_claim(tmp_path, monkeypatch):
    store = RunStore(tmp_path / "runs.db")
    store.create_run(record())
    monkeypatch.setattr(lease, "rm_root", lambda: tmp_path)
    held_by_other_agent = ScriptResult(
        rc=0,
        stdout=json.dumps({"held": True, "agent": "codex", "reason": "active lease"}),
        stderr="",
    )
    release_claim = Mock()
    monkeypatch.setattr(lease.claim_guard, "release_claim", release_claim)
    monkeypatch.setattr(
        lease,
        "run_module",
        _invoke_stub({"check_agent_claim": held_by_other_agent}),
    )
    note = lease.acquire(store, "self-claim-run", repo="Runner_Dashboard", issue="4063", agent="claude", branch="b")
    assert note is None
    rec = store.get_run("self-claim-run")
    assert rec.status == "blocked"
    assert rec.failure_class == "lease_blocked"
    release_claim.assert_called_once_with("self-claim-run", "blocked")


def test_acquire_blocks_when_check_agent_claim_output_is_unparseable(tmp_path, monkeypatch):
    store = RunStore(tmp_path / "runs.db")
    store.create_run(record())
    monkeypatch.setattr(lease, "rm_root", lambda: tmp_path)
    monkeypatch.setattr(lease.claim_guard, "release_claim", Mock())
    unparseable = ScriptResult(rc=1, stdout="", stderr="gh api failed: rate limited")
    monkeypatch.setattr(
        lease,
        "run_module",
        _invoke_stub(
            {
                "check_agent_claim": unparseable,
                "post_agent_lease": ScriptResult(rc=0, stdout="{}", stderr=""),
                "agent_communicate": ScriptResult(rc=0, stdout="{}", stderr=""),
            }
        ),
    )
    note = lease.acquire(store, "self-claim-run", repo="Runner_Dashboard", issue="4063", agent="claude", branch="b")
    assert note is not None
    rec = store.get_run("self-claim-run")
    assert rec.status != "blocked"
