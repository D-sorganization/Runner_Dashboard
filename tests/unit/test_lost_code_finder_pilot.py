"""Weekly Tools pilot for Lost Code Finder (#3869)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from staff.barb_continuity import BarbContinuityStore
from staff.lost_code_finder_pilot import (
    EXIT_CRITERIA,
    PILOT_REPO,
    pilot_scope_matches,
    run_tools_weekly_pilot,
)
from staff.scheduler_lost_code_finder import fire_guard

pytestmark = pytest.mark.unit


def test_pilot_scope_requires_tools_and_weekly_job() -> None:
    assert pilot_scope_matches({"pilot_repos": ["Tools"], "schedules": {"weekly_audit": "0 6 * * 0"}})
    assert not pilot_scope_matches({"pilot_repos": ["Runner_Dashboard"], "schedules": {"weekly_audit": "0 6 * * 0"}})


def test_scheduler_fire_guard_allows_fresh_slot(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "c.db")
    assert (
        fire_guard(
            "lost-code-finder",
            continuity=store,
            job="weekly_audit",
            slot="2026-11-08T06:00",
            now=datetime.now(UTC),
        )
        == ""
    )


def test_run_tools_weekly_pilot_persists_validated_ledger(tmp_path: Path) -> None:
    repo = tmp_path / "Tools"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "x.py").write_text("def dead() -> None:\n    pass\n", encoding="utf-8")
    lines = ["backend/x.py:1: unused function 'dead' (100% confidence)"]
    out = tmp_path / "ledger.json"
    result = run_tools_weekly_pilot(repo, head_commit="c0ffee", tier1_vulture_lines=lines, ledger_path=out)
    assert result["status"] == "executed" and result["finding_count"] == 1
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["repo"] == PILOT_REPO
    assert doc["pilot"]["exit_criteria"] == list(EXIT_CRITERIA)
    assert doc["findings"][0]["verdict"] == "UNKNOWN"
