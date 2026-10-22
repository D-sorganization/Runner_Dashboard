"""Rollups over stored delivery metrics (#2120)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from delivery_metrics.db import init_db
from delivery_metrics.models import PrSnapshot
from delivery_metrics.parse import parse_check_runs, parse_pr_snapshot
from delivery_metrics.report import build_metrics_report, pr_drill_down
from delivery_metrics.rollup import ci_rounds_to_green, top_failing_checks
from delivery_metrics.sync import collect_delivery_metrics_once

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "delivery_metrics"
REPO = "D-sorganization/Runner_Dashboard"


class _FixtureCollector:
    def __init__(self, pr_payload: dict, checks_payload: dict) -> None:
        self._pr = parse_pr_snapshot(REPO, pr_payload)
        head = checks_payload["check_runs"][0]["head_sha"]
        self._runs = parse_check_runs(REPO, head, checks_payload)

    async def list_pulls_updated_since(self, repo_full: str, since: datetime, *, max_pages: int = 3):
        if repo_full != REPO or self._pr.updated_at < since:
            return ()
        return (self._pr,)

    async def fetch_pull(self, repo_full: str, number: int) -> PrSnapshot:
        return self._pr

    async def list_check_runs(self, repo_full: str, head_sha: str):
        return self._runs


@pytest.fixture
async def seeded_conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = init_db(tmp_path / "dm.db")
    pr = json.loads((FIXTURES / "pr_2111.json").read_text(encoding="utf-8"))
    checks = json.loads((FIXTURES / "check_runs_pr_2111.json").read_text(encoding="utf-8"))
    await collect_delivery_metrics_once(_FixtureCollector(pr, checks), [REPO], db_path=db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def test_ci_rounds_from_recorded_check_runs() -> None:
    payload = json.loads((FIXTURES / "check_runs_pr_2111.json").read_text(encoding="utf-8"))
    head = payload["check_runs"][0]["head_sha"]
    runs = parse_check_runs(REPO, head, payload)
    rounds = ci_rounds_to_green(runs)
    assert rounds is not None and rounds >= 1
    assert isinstance(top_failing_checks(runs, limit=3), list)


@pytest.mark.asyncio
async def test_build_metrics_report_from_seeded_db(seeded_conn: sqlite3.Connection) -> None:
    since = datetime(2026, 10, 1, tzinfo=UTC)
    until = datetime(2026, 10, 31, tzinfo=UTC)
    report = build_metrics_report(seeded_conn, since=since, until=until, repo_full=REPO)
    assert report["summary"]["prs"] >= 1
    assert report["weekly_trends"]
    assert report["pr_drill_down"]
    drill = pr_drill_down(seeded_conn, REPO, 2111)
    assert drill["pull"]["number"] == 2111
    assert drill["check_runs"]
