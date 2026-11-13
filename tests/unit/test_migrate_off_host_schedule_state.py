"""Regression: migrate-off-host must not replace full scheduler state (#3927)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from staff import schedule_placement as sp
from staff import store as store_mod
from staff.budget import BudgetGuard
from staff.holds import HoldsList
from staff.roles import parse_role
from staff.runner import StaffRunner
from staff.scheduler import StaffScheduler

pytestmark = pytest.mark.unit


def test_purge_schedule_role_state_drops_named_jobs_without_full_replace(tmp_path: Path) -> None:
    state_file = tmp_path / "staff_schedule_state.json"
    state_file.write_text(
        json.dumps(
            {
                "archivist": {"cursor": "2026-01-01T00:00:00+00:00"},
                "archivist::job-a": {"cursor": "2026-01-01T00:00:00+00:00"},
                "night-watch": {"cursor": "2026-01-02T00:00:00+00:00"},
            }
        ),
        encoding="utf-8",
    )
    runner = StaffRunner(store=store_mod.RunStore(tmp_path / "runs.sqlite3"))
    sched = StaffScheduler(
        runner,
        HoldsList(roles_loader=lambda: {}),
        BudgetGuard(runner.store),
        state_file=state_file,
    )
    sp.purge_schedule_role_state(sched, ("archivist",))
    raw = json.loads(state_file.read_text(encoding="utf-8"))
    assert "archivist" not in raw and "archivist::job-a" not in raw
    assert "night-watch" in raw
