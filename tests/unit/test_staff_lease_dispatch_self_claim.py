"""Dispatch admission applies a claim label before the worker lease ritual (#4063).

Regression: ``lease.acquire`` must not treat our own dispatch label as a foreign hold.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from staff import claim_guard, lease as lease_mod
from staff.store import RunRecord, RunStore
from tests.api.coordination_fake_rm import FakeRM, claim_status, lease_result

RUN_ID = "run-selfclaim1"
REPO = "Runner_Dashboard"
ISSUE = "4063"
AGENT = "antigravity"


def _seed_run(store: RunStore, run_id: str = RUN_ID) -> None:
    store.create_run(
        RunRecord(
            id=run_id,
            role="issue-remediator",
            provider="antigravity",
            model=None,
            machine="test",
            repo=REPO,
            target_kind="issue",
            target_ref=f"#{ISSUE}",
            prompt="fix",
            branch="cursor/4063-fix",
        )
    )


@pytest.fixture
def rm_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRM:
    fake = FakeRM(tmp_path / "rm")
    monkeypatch.setenv("STAFF_RM_ROOT", str(fake.root))
    monkeypatch.setenv("STAFF_RM_PYTHON", sys.executable)
    claim_guard.reset_applied()
    yield fake
    claim_guard.reset_applied()


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RunStore:
    path = tmp_path / "runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(path))
    return RunStore(path)


def test_acquire_continues_when_check_reports_dispatch_label_hold(
    rm_root: FakeRM, store: RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failing case before fix: held due to claim:staff from apply_claim aborts the worker."""
    claim_guard._applied[RUN_ID] = (f"D-sorganization/{REPO}", int(ISSUE), "claim:staff")
    rm_root.respond(
        "check_agent_claim",
        claim_status(
            True,
            "staff",
            f"issue carries claim:staff from staff dispatch (run {RUN_ID})",
        ),
    )
    rm_root.respond("post_agent_lease", lease_result(agent=AGENT))
    rm_root.respond("agent_communicate:register", {"ok": True})

    _seed_run(store)
    note = lease_mod.acquire(store, RUN_ID, repo=REPO, issue=ISSUE, agent=AGENT, branch="cursor/4063-fix")
    assert note is not None
    assert "lease and presence" in note.lower()
    rec = store.get_run(RUN_ID)
    assert rec is not None and rec.status != "blocked"
    names = [name for name, _ in rm_root.calls()]
    assert "post_agent_lease" in names


def test_acquire_still_blocks_foreign_agent_hold(
    rm_root: FakeRM, store: RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    claim_guard._applied[RUN_ID] = (f"D-sorganization/{REPO}", int(ISSUE), "claim:staff")
    rm_root.respond(
        "check_agent_claim",
        {**claim_status(True, "codex", "active lease"), "session": "codex-other"},
    )

    _seed_run(store)
    note = lease_mod.acquire(store, RUN_ID, repo=REPO, issue=ISSUE, agent=AGENT, branch="b")
    assert note is None
    rec = store.get_run(RUN_ID)
    assert rec is not None and rec.failure_class == "lease_blocked"
    assert "post_agent_lease" not in [name for name, _ in rm_root.calls()]


def test_acquire_still_blocks_merged_pr_guard_even_with_dispatch_label(
    rm_root: FakeRM, store: RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    claim_guard._applied[RUN_ID] = (f"D-sorganization/{REPO}", int(ISSUE), "claim:staff")
    rm_root.respond(
        "check_agent_claim",
        claim_status(True, None, "merged PR #999 already closes this issue"),
    )

    _seed_run(store)
    note = lease_mod.acquire(store, RUN_ID, repo=REPO, issue=ISSUE, agent=AGENT, branch="b")
    assert note is None
    assert store.get_run(RUN_ID).failure_class == "lease_blocked"


def test_is_own_dispatch_claim_hold_unit() -> None:
    check: dict[str, Any] = {
        "held": True,
        "agent": "staff",
        "reason": "claim:issue-remediator present",
    }
    claim_guard._applied["run-x"] = ("D-sorganization/Runner_Dashboard", 1, "claim:issue-remediator")
    assert claim_guard.is_own_dispatch_claim_hold(
        "run-x", check, agent="antigravity", session="staff-run-x"
    )
    foreign = {**claim_status(True, "codex", "active lease"), "session": "codex-1"}
    assert not claim_guard.is_own_dispatch_claim_hold(
        "run-x", foreign, agent="antigravity", session="staff-run-x"
    )
