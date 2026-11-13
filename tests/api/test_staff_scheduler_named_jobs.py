"""Named per-job schedules inside ``role.scope.schedules`` fire independently (#3790).

Bob's canonical role declares a single flat ``schedule`` (10/16/17 weekdays) plus a
``scope.schedules`` map with a Monday-only ``weekly_value_watch`` entry that the flat
cron never reaches. The scheduler must evaluate each named entry on its own cursor so
every job becomes due on its own cron, and must never ask a report-only role for a PR.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from staff import budget as budget_mod
from staff import holds as holds_mod
from staff import roles as roles_mod
from staff import scheduler as scheduler_mod
from staff import store as store_mod
from staff.runner import RunRequest

LA = ZoneInfo("America/Los_Angeles")


def _la(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=LA)


_NAMED_SCHEDULES = {
    "wave_scoring": "0 10,16 * * 1-5",
    "daily_note": "0 17 * * 1-5",
    "weekly_value_watch": "0 8 * * 1",
}


def _bob_role() -> roles_mod.RoleSpec:
    return roles_mod.parse_role(
        {
            "name": "bob",
            "title": "Bob — Productivity Watchdog",
            "providers": ["fake"],
            "schedule": "0 10,16,17 * * 1-5",
            "window": None,
            "repos": ["Runner_Dashboard"],
            "scope": {"schedules": dict(_NAMED_SCHEDULES), "timezone": "America/Los_Angeles", "report_only": True},
            "permissions": {"open_pr": False, "push_branch": False, "merge": False, "code_read_only": True},
        }
    )


class _FakeRunner:
    machine = "TestNode"

    def __init__(self, store: store_mod.RunStore, roles: dict[str, roles_mod.RoleSpec]) -> None:
        self.store = store
        self._roles = roles
        self.submitted: list[RunRequest] = []

    def roles(self) -> dict[str, roles_mod.RoleSpec]:
        return self._roles

    def submit(self, req: RunRequest) -> store_mod.RunRecord:
        self.submitted.append(req)
        rec = store_mod.RunRecord(
            id=f"run-{len(self.submitted)}",
            role=req.role,
            provider="fake",
            model=None,
            machine=self.machine,
            repo=req.repo,
            target_kind="prompt",
            target_ref="",
            prompt=req.prompt,
            requested_by=req.requested_by,
        )
        return self.store.create_run(rec)


@pytest.fixture
def sched(tmp_path: Path) -> tuple[scheduler_mod.StaffScheduler, _FakeRunner, dict[str, datetime]]:
    store = store_mod.RunStore(tmp_path / "runs.sqlite3")
    runner = _FakeRunner(store, {"bob": _bob_role()})
    clock = {"now": _la(2026, 11, 2, 7, 59)}  # Monday 07:59 — anchors the cursor just before the due slot
    holds = holds_mod.HoldsList(tmp_path / "holds.json", roles_loader=dict)
    holds.replace([])
    guard = budget_mod.BudgetGuard(store, alert_sink=lambda *_: None, clock=lambda: clock["now"])
    s = scheduler_mod.StaffScheduler(
        runner,
        holds,
        guard,
        tick_seconds=30,
        clock=lambda: clock["now"],
        state_file=tmp_path / "state.json",
    )
    return s, runner, clock


@pytest.mark.unit
def test_monday_value_watch_fires_exactly_once_with_job_identity(
    sched: tuple[scheduler_mod.StaffScheduler, _FakeRunner, dict[str, datetime]],
) -> None:
    s, runner, clock = sched
    assert s.tick() == []  # 07:59 anchors the cursor; nothing due yet

    clock["now"] = _la(2026, 11, 2, 8, 0, 5)  # Monday 08:00
    decisions = s.tick()
    fired = [d for d in decisions if d["job"] == "weekly_value_watch"]
    assert len(fired) == 1
    assert fired[0]["fired"] is True
    assert len(runner.submitted) == 1
    req = runner.submitted[0]
    assert req.task_type == "weekly_value_watch"
    lowered = req.prompt.lower()
    assert "ship" not in lowered and "automerge_guard" not in lowered and "never push" in lowered

    clock["now"] = _la(2026, 11, 2, 8, 0, 40)  # same slot, re-ticked
    repeat = [d for d in s.tick() if d["job"] == "weekly_value_watch"]
    assert repeat == []  # no duplicate fire
    assert len(runner.submitted) == 1


@pytest.mark.unit
def test_value_watch_does_not_fire_on_tuesday(
    sched: tuple[scheduler_mod.StaffScheduler, _FakeRunner, dict[str, datetime]],
) -> None:
    s, runner, clock = sched
    s.tick()  # anchor Monday 07:59 cursor
    clock["now"] = _la(2026, 11, 2, 8, 0, 5)  # Monday 08:00 — consume the Monday slot
    assert [d for d in s.tick() if d["job"] == "weekly_value_watch" and d["fired"]]
    clock["now"] = _la(2026, 11, 3, 8, 0, 5)  # Tuesday 08:00 — cron excludes Tuesday
    decisions = s.tick()
    assert [d for d in decisions if d["job"] == "weekly_value_watch"] == []
    assert len(runner.submitted) == 1  # only Monday's run, nothing new on Tuesday


@pytest.mark.unit
def test_wave_and_daily_jobs_still_fire_with_their_own_identity(
    sched: tuple[scheduler_mod.StaffScheduler, _FakeRunner, dict[str, datetime]],
) -> None:
    s, runner, clock = sched
    s.tick()  # anchor at 07:59
    clock["now"] = _la(2026, 11, 2, 8, 0, 5)  # consume the Monday value-watch slot first
    s.tick()
    runner.store.update_run("run-1", status="succeeded")  # frees the per-role active-run gate

    clock["now"] = _la(2026, 11, 2, 10, 0, 5)  # Monday 10:00 — wave_scoring slot
    decisions = s.tick()
    jobs_fired = {d["job"]: d["fired"] for d in decisions if d.get("fired")}
    assert jobs_fired == {"wave_scoring": True}
    assert runner.submitted[-1].task_type == "wave_scoring"
    runner.store.update_run("run-2", status="succeeded")

    clock["now"] = _la(2026, 11, 2, 16, 0, 5)  # Monday 16:00 — wave_scoring's second daily slot
    s.tick()
    runner.store.update_run("run-3", status="succeeded")

    clock["now"] = _la(2026, 11, 2, 17, 0, 5)  # Monday 17:00 — daily_note slot
    decisions = s.tick()
    jobs_fired = {d["job"]: d["fired"] for d in decisions if d.get("fired")}
    assert jobs_fired == {"daily_note": True}
    assert runner.submitted[-1].task_type == "daily_note"

    # the flat role.schedule (10,16,17) is superseded by the named jobs: no bare "bob" entry fires
    assert all(d["role"] == "bob" and "job" in d for d in decisions)
