"""Unit tests for fleet state audit (#3640)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from staff import fleet_state_audit as fsa

LA = ZoneInfo("America/Los_Angeles")
SAMPLE = (
    "| item | owner | source | check | verified_at (PT) | ttl |\n"
    "| ---- | ----- | ------ | ----- | ---------------- | --- |\n"
    "| hold ok | Dieter | RD #100 | check | 2026-10-30 12:00 | 1h |\n"
    "| stale | Dieter | RM #200 | autoMergeRequest null | 2026-10-29 09:00 | 1h |\n"
    "| no source | Dieter | | check | 2026-10-30 12:00 | 1h |\n"
    "| **— Red mains —** | | | | | |\n"
    "| main row | rd-barb | RD | push CI on main | UNVERIFIED | 1h |\n"
)


def test_parse_flags_diff_and_live() -> None:
    rows = fsa.parse_state_table(SAMPLE)
    assert len(rows) == 4
    now = datetime(2026, 10, 30, 12, 30, tzinfo=LA)
    flags = fsa.evaluate_static_flags(rows, now=now)
    kinds = {(f["kind"], f["item"]) for f in flags}
    assert ("stale", "stale") in kinds and ("stale", "main row") in kinds
    assert ("missing_source", "no source") in kinds
    assert ("stale", "hold ok") not in kinds
    report = fsa.build_report(
        path=Path("state.md"),
        rows=rows,
        static_flags=flags,
        live_results=[],
        previous={"tally": {"stale": 1}, "flags": []},
        live_enabled=False,
    )
    assert report["diff"]["tally"]["stale"]["after"] == 2


@pytest.mark.asyncio
async def test_live_auto_merge_expectation() -> None:
    row = fsa.parse_state_table(SAMPLE)[1]

    async def fetch_pr(full: str, number: int) -> dict:
        assert full.endswith("Repository_Management") and number == 200
        return {"merged": False, "state": "open", "auto_merge": None, "head": {"sha": "x"}}

    ok = await fsa.live_check_row(row, fetch_pr=fetch_pr, fetch_red_main=fsa._noop_red_main)
    assert ok["ok"] is True

    async def armed(_f: str, _n: int) -> dict:
        return {"merged": False, "state": "open", "auto_merge": {"merge_method": "squash"}, "head": {}}

    bad = await fsa.live_check_row(row, fetch_pr=armed, fetch_red_main=fsa._noop_red_main)
    assert bad["ok"] is False
