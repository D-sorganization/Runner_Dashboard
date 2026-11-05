"""StaffScheduler consults the Lost Code Finder fire guard (#3869).

#3873 added ``staff.scheduler_lost_code_finder.fire_guard`` but nothing called it, so a
scheduled ``lost-code-finder`` slot would fire even while a Grok fallback lease was held
or for a job the weekly Tools pilot does not define. These drive a real
``StaffScheduler.tick()`` (fixtures shared with the Cartographer scheduler test) and assert
the guard's skip reasons reach the decision.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from staff import persona_fallback_lease
from staff import runner as runner_mod
from staff.barb_continuity import BarbContinuityStore
from staff.role_capsule import RoleCapsuleStore

from tests.api import test_staff_scheduler_cartographer as cartographer_test

LA = cartographer_test.LA
staff = cartographer_test.staff  # shared fixture; ``role_dir`` below overrides its role file
ROLE = "lost-code-finder"


def _write_role(d: Path, job: str) -> None:
    (d / f"{ROLE}.yml").write_text(
        "\n".join(
            [
                f"name: {ROLE}",
                "title: Lost Code Finder",
                "summary: Read-only auditor for suspected dead code.",
                "playbook: docs/agents/repository-guide.md",
                "prompt_template: null",
                "instructions: Record findings in the evidence ledger only.",
                "providers: [fake]",
                "model: default",
                "schedule: '0 6 * * 0'",
                "window: null",
                "repos: [Tools]",
                f"scope: {{schedules: {{{job}: '0 6 * * 0'}}, pilot_repos: [Tools], ledger_only: true}}",
                "budget: {usd_per_run: 2, usd_per_day: 2}",
                (
                    "permissions: {lease: false, push_branch: false, open_pr: false, "
                    "merge: false, host_shell: false, notify_user: false}"
                ),
                "reports_to: barb",
                "holds: []",
                "surface: dashboard",
            ]
        ),
        encoding="utf-8",
    )


@pytest.fixture
def role_dir(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    d = tmp_path / "roles"
    d.mkdir()
    _write_role(d, getattr(request, "param", "weekly_audit"))
    return d


def _tick_at_sunday_six(
    staff: runner_mod.StaffRunner, tmp_path: Path, continuity: BarbContinuityStore, *, hold_lease: bool = False
) -> list:
    clock = {"now": datetime(2026, 11, 8, 5, 59, tzinfo=LA)}
    sched = cartographer_test._scheduler(
        staff,
        tmp_path,
        clock,
        continuity=continuity,
        capsules=RoleCapsuleStore(tmp_path / "capsules"),
        notify_opts={"sender": lambda *_a: None},
    )
    assert sched.tick() == []  # cursor seeded; the 06:00 Sunday slot is not yet due
    clock["now"] = datetime(2026, 11, 8, 6, 0, 30, tzinfo=LA)
    if hold_lease:
        persona_fallback_lease.acquire_persona_lease(
            continuity, ROLE, holder=persona_fallback_lease.GROK_FALLBACK_HOLDER, now=clock["now"]
        )
    return sched.tick()


@pytest.mark.unit
def test_tick_skips_lost_code_finder_while_a_fallback_lease_is_held(
    staff: runner_mod.StaffRunner, tmp_path: Path
) -> None:
    decisions = _tick_at_sunday_six(staff, tmp_path, BarbContinuityStore(tmp_path / "continuity.db"), hold_lease=True)
    assert [(d["role"], d["fired"], d["reason"]) for d in decisions] == [(ROLE, False, "fallback_lease_held")]
    assert staff.store.active_runs() == []


@pytest.mark.unit
@pytest.mark.parametrize("role_dir", ["nightly_sweep"], indirect=True)
def test_tick_refuses_a_lost_code_finder_job_outside_the_weekly_pilot(
    staff: runner_mod.StaffRunner, tmp_path: Path
) -> None:
    decisions = _tick_at_sunday_six(staff, tmp_path, BarbContinuityStore(tmp_path / "continuity.db"))
    assert [(d["role"], d["fired"], d["reason"]) for d in decisions] == [(ROLE, False, "unknown_job")]
    assert staff.store.active_runs() == []
