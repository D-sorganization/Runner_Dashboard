"""Weekly delivery + staff summary composition (#2123)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from delivery_metrics.db import init_db
from delivery_metrics.parse import parse_check_runs, parse_pr_snapshot
from delivery_metrics.sync import collect_delivery_metrics_once
from delivery_metrics.weekly_artifact import weekly_summary_marker
from delivery_metrics.weekly_summary import build_weekly_summary, dieter_wait_seconds
from staff.conversation_models import ActionProposalRecord
from staff.store import RunRecord

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "delivery_metrics"
REPO = "D-sorganization/Runner_Dashboard"
SINCE = datetime(2026, 10, 1, tzinfo=UTC)
UNTIL = datetime(2026, 10, 31, tzinfo=UTC)


class _FixtureCollector:
    def __init__(self, pr_payload: dict, checks_payload: dict) -> None:
        self._pr = parse_pr_snapshot(REPO, pr_payload)
        self._runs = parse_check_runs(REPO, checks_payload["check_runs"][0]["head_sha"], checks_payload)

    async def list_pulls_updated_since(self, repo_full: str, since: datetime, *, max_pages: int = 3):
        if repo_full != REPO or self._pr.updated_at < since:
            return ()
        return (self._pr,)

    async def fetch_pull(self, repo_full: str, number: int):
        return self._pr

    async def list_check_runs(self, repo_full: str, head_sha: str):
        return self._runs


@pytest.fixture
def metrics_conn(tmp_path: Path):
    db_path = init_db(tmp_path / "metrics.db")
    pr = json.loads((FIXTURES / "pr_2111.json").read_text(encoding="utf-8"))
    checks = json.loads((FIXTURES / "check_runs_pr_2111.json").read_text(encoding="utf-8"))
    import asyncio

    asyncio.run(collect_delivery_metrics_once(_FixtureCollector(pr, checks), [REPO], db_path=db_path))
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def _run(**kw) -> RunRecord:
    base = {
        "id": "run-1",
        "role": "barb",
        "provider": "claude",
        "model": "sonnet",
        "machine": "node",
        "repo": "Runner_Dashboard",
        "target_kind": "issue",
        "target_ref": "99",
        "prompt": "ship it",
        "status": "succeeded",
        "created_at": "2026-10-20T12:00:00Z",
        "cost_usd": 2.5,
        "pr_number": 2111,
        "artifact": "pr_merged",
    }
    return RunRecord(**{**base, **kw})


def test_weekly_marker_is_stable() -> None:
    assert weekly_summary_marker("2026-W42") == "<!-- delivery-metrics-week=2026-W42 -->"


def test_dieter_wait_sums_human_proposal_decisions() -> None:
    proposals = [
        ActionProposalRecord(
            id="p1",
            message_id="m1",
            state="approved",
            created_at="2026-10-20T10:00:00Z",
            decided_at="2026-10-20T10:30:00Z",
            decided_by="human:dieter",
        ),
        ActionProposalRecord(
            id="p2",
            message_id="m2",
            state="approved",
            created_at="2026-10-20T11:00:00Z",
            decided_at="2026-10-20T11:05:00Z",
            decided_by="silent:auto",
        ),
    ]
    wait = dieter_wait_seconds(
        proposals,
        since=datetime(2026, 10, 20, tzinfo=UTC),
        until=datetime(2026, 10, 21, tzinfo=UTC),
    )
    assert wait == 30 * 60


def test_build_weekly_summary_composes_delivery_staff_and_cost(metrics_conn) -> None:
    session_metrics = {
        "cost_per_merged_pr": {"count": 2, "median_usd": 4.5, "p90_usd": 6.0},
        "total_sessions": 3,
    }
    body = build_weekly_summary(
        metrics_conn,
        since=SINCE,
        until=UNTIL,
        runs=[_run()],
        proposals=[],
        session_metrics=session_metrics,
    )
    assert body["delivery"]["summary"]["prs"] >= 1
    assert body["staff"]["runs"] == 1
    assert body["staff"]["cost_usd"] == 2.5
    assert body["staff"]["cost_per_merged_pr"]["median_usd"] == 4.5
    assert "markdown" in body
    assert "Weekly delivery summary" in body["markdown"]
