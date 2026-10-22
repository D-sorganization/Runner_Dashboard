"""Delivery metrics store + sync (#2119)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from delivery_metrics.db import get_repo_sync_cursor, init_db, upsert_pull
from delivery_metrics.models import PrSnapshot
from delivery_metrics.parse import parse_check_runs, parse_pr_snapshot
from delivery_metrics.ports import GitHubDeliveryCollectorPort
from delivery_metrics.sync import collect_delivery_metrics_once

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "delivery_metrics"
REPO = "D-sorganization/Runner_Dashboard"


class _FixtureCollector:
    """Port fake backed by recorded GitHub JSON."""

    def __init__(self, pr_payload: dict, checks_payload: dict) -> None:
        self._pr = parse_pr_snapshot(REPO, pr_payload)
        head = checks_payload["check_runs"][0]["head_sha"]
        self._runs = parse_check_runs(REPO, head, checks_payload)
        self.pull_calls = 0

    async def list_pulls_updated_since(self, repo_full: str, since: datetime, *, max_pages: int = 3):
        self.pull_calls += 1
        if repo_full != REPO:
            return ()
        if self._pr.updated_at < since:
            return ()
        return (self._pr,)

    async def fetch_pull(self, repo_full: str, number: int) -> PrSnapshot:
        return self._pr

    async def list_check_runs(self, repo_full: str, head_sha: str):
        return self._runs


@pytest.fixture
def mem_db(tmp_path: Path) -> Path:
    return init_db(tmp_path / "dm.db")


@pytest.fixture
def collector() -> GitHubDeliveryCollectorPort:
    pr = json.loads((FIXTURES / "pr_2111.json").read_text(encoding="utf-8"))
    checks = json.loads((FIXTURES / "check_runs_pr_2111.json").read_text(encoding="utf-8"))
    return _FixtureCollector(pr, checks)


@pytest.mark.asyncio
async def test_collect_once_is_idempotent(mem_db: Path, collector: GitHubDeliveryCollectorPort) -> None:
    first = await collect_delivery_metrics_once(collector, [REPO], db_path=mem_db)
    second = await collect_delivery_metrics_once(collector, [REPO], db_path=mem_db)
    assert first.pulls_upserted >= 1
    assert first.check_runs_upserted >= 20
    assert second.pulls_upserted == 0
    assert second.check_runs_upserted == 0
    assert collector.pull_calls == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_sync_cursor_advances(mem_db: Path, collector: GitHubDeliveryCollectorPort) -> None:
    await collect_delivery_metrics_once(collector, [REPO], db_path=mem_db)
    conn = sqlite3.connect(str(mem_db))
    conn.row_factory = sqlite3.Row
    cursor = get_repo_sync_cursor(conn, REPO)
    conn.close()
    assert cursor is not None
    assert cursor.tzinfo is not None


def test_upsert_pull_detects_change(mem_db: Path) -> None:
    pr = json.loads((FIXTURES / "pr_2111.json").read_text(encoding="utf-8"))
    snap = parse_pr_snapshot(REPO, pr)
    conn = sqlite3.connect(str(mem_db))
    conn.row_factory = sqlite3.Row
    now = datetime.now(UTC)
    assert upsert_pull(conn, snap, synced_at=now) is True
    assert upsert_pull(conn, snap, synced_at=now) is False
    conn.close()
