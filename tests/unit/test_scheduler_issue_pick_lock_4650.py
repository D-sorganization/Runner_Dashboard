"""Regression tests for Runner_Dashboard#4650: scheduler lock + issue-pick short-circuit."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from staff.holds import HoldsList
from staff.scheduler import StaffScheduler
from staff.scheduler_issue_pick import MAX_CLAIM_CHECKS, pick_issue_target

from staff import scheduler as scheduler_mod

TZ = ZoneInfo("America/Los_Angeles")


def _issue_remediator_role(repos: tuple[str, ...] = ("Tools", "UpstreamDrift")) -> Any:
    return SimpleNamespace(
        name="issue-remediator",
        title="Issue Remediator",
        schedule="0 23 * * *",
        repos=repos,
        dispatchable=True,
        scope={},
        window=None,
        host="TestNode",
        retired=False,
        model=None,
        provider=None,
        strategy={},
    )


def test_pick_issue_target_short_circuits_on_first_unclaimed_highest_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sort candidates by priority first and stop at the first unclaimed issue."""
    monkeypatch.setattr("staff.claim_guard.scheduler_blocker", lambda *a, **kw: "")
    now = datetime(2026, 11, 23, 23, 5, tzinfo=TZ)
    role = _issue_remediator_role(("Tools", "UpstreamDrift"))

    open_by_repo = {
        "Tools": [{"number": n, "labels": [{"name": "low"}]} for n in range(1, 31)],
        "UpstreamDrift": [
            {"number": 99, "labels": [{"name": "medium"}]},
            {"number": 42, "labels": [{"name": "critical"}, {"name": "quick-win"}]},
        ],
    }
    claim_calls: list[tuple[str, int]] = []

    def _fetch_open(repo: str):
        return open_by_repo.get(repo, []), None

    def _fetch_claim(repo: str, issue: int):
        claim_calls.append((repo, issue))
        return {"available": True, "held": False}

    picked = pick_issue_target(role, now, fetch_open=_fetch_open, fetch_claim=_fetch_claim)
    assert picked == ("UpstreamDrift", 42)
    assert claim_calls == [("UpstreamDrift", 42)]


def test_pick_issue_target_bounds_claim_checks_when_all_held(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cap subprocess claim checks per tick so a busy backlog cannot stall the scheduler."""
    monkeypatch.setattr("staff.claim_guard.scheduler_blocker", lambda *a, **kw: "")
    now = datetime(2026, 11, 23, 23, 5, tzinfo=TZ)
    role = _issue_remediator_role(("Tools",))

    issues = [{"number": n, "labels": [{"name": "medium"}]} for n in range(1, MAX_CLAIM_CHECKS + 20)]
    claim_calls: list[int] = []

    def _fetch_open(repo: str):
        return issues, None

    def _fetch_claim(repo: str, issue: int):
        claim_calls.append(issue)
        return {"available": True, "held": True, "reason": "claimed elsewhere"}

    picked = pick_issue_target(role, now, fetch_open=_fetch_open, fetch_claim=_fetch_claim)
    assert picked == (None, None)
    assert len(claim_calls) == MAX_CLAIM_CHECKS


def test_scheduler_tick_does_not_hold_lock_during_pick_issue_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """StaffScheduler.tick() must never hold self._lock while running pick_issue_target I/O."""
    monkeypatch.setenv("STAFF_SCHEDULE_STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr("staff.claim_guard.scheduler_blocker", lambda *a, **kw: "")
    monkeypatch.setattr("staff.scheduler_named_jobs.skip_due_slot_during_startup", lambda _name: False)

    role = _issue_remediator_role(("Tools",))
    submitted: list[dict] = []

    class _FakeStore:
        def active_runs(self):
            return []

        def list_runs(self, **_kw):
            return []

    class _FakeRunner:
        machine = "TestNode"
        store = _FakeStore()

        def roles(self):
            return {role.name: role}

        def submit(self, req: Any):
            submitted.append(req)
            return SimpleNamespace(id="run-test-4650")

    class _FakeBudget:
        def can_run(self, _role):
            return True, "ok"

    now = datetime(2026, 11, 23, 23, 5, tzinfo=TZ)
    sched = StaffScheduler(
        runner=_FakeRunner(),  # type: ignore[arg-type]
        holds=HoldsList(path=tmp_path / "holds.json"),
        budget=_FakeBudget(),  # type: ignore[arg-type]
        clock=lambda: now,
    )
    # Seed cursor before 23:00 so 23:00 slot is due.
    sched._state = {"issue-remediator": {"cursor": "2026-11-23T22:00:00-08:00"}}
    sched._save_state()

    lock_was_free_during_pick = False

    def _fetch_open(_repo: str):
        return [{"number": 101, "labels": [{"name": "high"}]}], None

    def _fetch_claim(_repo: str, _issue: int):
        nonlocal lock_was_free_during_pick
        acquired = sched._lock.acquire(blocking=False)
        if acquired:
            lock_was_free_during_pick = True
            sched._lock.release()
        return {"available": True, "held": False}

    monkeypatch.setattr(scheduler_mod, "fetch_open_issues", _fetch_open)
    monkeypatch.setattr(scheduler_mod, "fetch_issue_claim_status", _fetch_claim)

    decisions = sched.tick(now=now)
    assert lock_was_free_during_pick, "StaffScheduler._lock was held during pick_issue_target I/O!"
    assert any(d.get("role") == "issue-remediator" and d.get("fired") for d in decisions)
    assert submitted and submitted[0].issue == 101
