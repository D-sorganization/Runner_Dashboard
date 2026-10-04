"""Tests for session telemetry, 30-day PR metrics, and post-PR alerts (issue #1849 / RD-4).

Verifies:
- Session metadata ingestion and validation.
- Post-PR alert triggers (>5 wake-ups or >$20 spent after PR opened).
- 30-day metrics:
  - cost per merged PR (median and p90).
  - cost after PR opened (total, median, p90).
  - sessions with >3 wake-ups after PR opened.
  - startup context size per environment.
  - pre-push hook duration (median, p90, p95).
  - merge conflicts in docs files.
- 30-day daily trend series.
- API endpoints for telemetry ingestion and metric retrieval.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from routers.usage_metrics import create_usage_metrics_router  # noqa: E402
from session_telemetry import (  # noqa: E402
    ALERT_COST_AFTER_PR_THRESHOLD_USD,
    ALERT_WAKEUPS_AFTER_PR_THRESHOLD,
    SessionRecord,
    SessionTelemetryStore,
    calculate_percentile,
)


@pytest.fixture
def temp_store(tmp_path: Path) -> SessionTelemetryStore:
    data_file = tmp_path / "test_session_telemetry.json"
    return SessionTelemetryStore(data_file=data_file, retention_days=30)


@pytest.fixture
def client(temp_store: SessionTelemetryStore) -> TestClient:
    app = FastAPI()
    router = create_usage_metrics_router(telemetry_store=temp_store)
    app.include_router(router)
    return TestClient(app)


def test_percentile_calculation() -> None:
    assert calculate_percentile([], 50) == 0.0
    assert calculate_percentile([10.0], 50) == 10.0
    assert calculate_percentile([10.0], 90) == 10.0
    # Even-length and odd-length lists
    data = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert calculate_percentile(data, 50) == 30.0
    assert calculate_percentile(data, 0) == 10.0
    assert calculate_percentile(data, 100) == 50.0
    assert calculate_percentile(data, 90) == 46.0


def test_alert_threshold_constants_and_record_dataclass() -> None:
    assert ALERT_WAKEUPS_AFTER_PR_THRESHOLD == 5
    assert ALERT_COST_AFTER_PR_THRESHOLD_USD == 20.0
    rec = SessionRecord(session_id="manual-rec", cost_usd=1.5)
    assert rec.session_id == "manual-rec"
    assert rec.cost_usd == 1.5
    d = rec.to_dict()
    assert d["session_id"] == "manual-rec"


def test_record_validation_and_persistence(temp_store: SessionTelemetryStore) -> None:
    rec = temp_store.record_session(
        {
            "session_id": "sess-001",
            "cost_usd": 12.50,
            "context_size": 35000,
            "environment": "cloud",
            "turns_count": 14,
            "wakeups_count": 2,
            "wakeups_after_pr": 1,
            "cost_after_pr_usd": 2.10,
            "origin": "dashboard-dispatched",
            "linked_pr": 1845,
            "pr_merged": True,
            "model": "claude-3-7-sonnet",
            "pre_push_duration_s": 42.5,
            "docs_merge_conflicts": 0,
        }
    )
    assert rec.session_id == "sess-001"
    assert rec.cost_usd == 12.50
    assert rec.is_alert is False
    assert len(rec.alert_reasons) == 0

    fetched = temp_store.get_session("sess-001")
    assert fetched is not None
    assert fetched.session_id == "sess-001"
    assert fetched.pr_merged is True
    assert fetched.cost_after_pr_usd == 2.10

    # Negative values must raise ValueError
    with pytest.raises(ValueError, match="cost_usd must be non-negative"):
        temp_store.record_session({"session_id": "sess-err", "cost_usd": -1.0})

    with pytest.raises(ValueError, match="wakeups_after_pr must be non-negative"):
        temp_store.record_session({"session_id": "sess-err", "wakeups_after_pr": -2})


def test_alert_fires_on_wakeups_after_pr(temp_store: SessionTelemetryStore) -> None:
    # Threshold is > 5 wakeups after PR
    rec = temp_store.record_session(
        {
            "session_id": "sess-wakeups-alert",
            "cost_usd": 8.0,
            "wakeups_after_pr": 6,
            "cost_after_pr_usd": 3.0,
        }
    )
    assert rec.is_alert is True
    assert any("wake-ups" in reason for reason in rec.alert_reasons)

    # At threshold 5: should not trigger
    rec_ok = temp_store.record_session(
        {
            "session_id": "sess-wakeups-ok",
            "cost_usd": 8.0,
            "wakeups_after_pr": 5,
            "cost_after_pr_usd": 3.0,
        }
    )
    assert rec_ok.is_alert is False


def test_alert_fires_on_cost_after_pr(temp_store: SessionTelemetryStore) -> None:
    # Threshold is > $20 spent after PR
    rec = temp_store.record_session(
        {
            "session_id": "sess-cost-alert",
            "cost_usd": 35.0,
            "wakeups_after_pr": 2,
            "cost_after_pr_usd": 24.50,
        }
    )
    assert rec.is_alert is True
    assert any("$20" in reason or "spend" in reason for reason in rec.alert_reasons)

    # At threshold $20.00: should not trigger
    rec_ok = temp_store.record_session(
        {
            "session_id": "sess-cost-ok",
            "cost_usd": 25.0,
            "wakeups_after_pr": 2,
            "cost_after_pr_usd": 20.00,
        }
    )
    assert rec_ok.is_alert is False


def test_alert_fires_on_both_triggers(temp_store: SessionTelemetryStore) -> None:
    rec = temp_store.record_session(
        {
            "session_id": "sess-both-alert",
            "cost_usd": 40.0,
            "wakeups_after_pr": 8,
            "cost_after_pr_usd": 28.0,
        }
    )
    assert rec.is_alert is True
    assert len(rec.alert_reasons) == 2


def test_30d_metrics_cost_per_merged_pr(temp_store: SessionTelemetryStore) -> None:
    # Ingest 3 merged PR sessions and 1 unmerged session
    temp_store.record_session(
        {
            "session_id": "sess-m1",
            "cost_usd": 10.0,
            "linked_pr": 101,
            "pr_merged": True,
        }
    )
    temp_store.record_session(
        {
            "session_id": "sess-m2",
            "cost_usd": 20.0,
            "linked_pr": 102,
            "pr_merged": True,
        }
    )
    temp_store.record_session(
        {
            "session_id": "sess-m3",
            "cost_usd": 30.0,
            "linked_pr": 103,
            "pr_merged": True,
        }
    )
    temp_store.record_session(
        {
            "session_id": "sess-unmerged",
            "cost_usd": 100.0,
            "linked_pr": 104,
            "pr_merged": False,
        }
    )

    metrics = temp_store.get_metrics(window_days=30)
    merged_metrics = metrics["cost_per_merged_pr"]
    assert merged_metrics["count"] == 3
    assert merged_metrics["median_usd"] == 20.0
    assert merged_metrics["p90_usd"] == 28.0


def test_30d_metrics_cost_after_pr(temp_store: SessionTelemetryStore) -> None:
    temp_store.record_session(
        {
            "session_id": "sess-pr1",
            "cost_after_pr_usd": 4.0,
            "linked_pr": 201,
        }
    )
    temp_store.record_session(
        {
            "session_id": "sess-pr2",
            "cost_after_pr_usd": 8.0,
            "linked_pr": 202,
        }
    )
    temp_store.record_session(
        {
            "session_id": "sess-pr3",
            "cost_after_pr_usd": 12.0,
            "linked_pr": 203,
        }
    )

    metrics = temp_store.get_metrics(window_days=30)
    cost_after_pr = metrics["cost_after_pr"]
    assert cost_after_pr["count"] == 3
    assert cost_after_pr["total_usd"] == 24.0
    assert cost_after_pr["median_usd"] == 8.0
    assert cost_after_pr["mean_usd"] == 8.0


def test_30d_metrics_wakeups_after_pr_gt_3(temp_store: SessionTelemetryStore) -> None:
    temp_store.record_session(
        {
            "session_id": "sess-w1",
            "wakeups_after_pr": 1,
            "linked_pr": 301,
        }
    )
    temp_store.record_session(
        {
            "session_id": "sess-w2",
            "wakeups_after_pr": 4,  # > 3
            "linked_pr": 302,
        }
    )
    temp_store.record_session(
        {
            "session_id": "sess-w3",
            "wakeups_after_pr": 7,  # > 3
            "linked_pr": 303,
        }
    )

    metrics = temp_store.get_metrics(window_days=30)
    wakeups = metrics["post_pr_wakeups"]
    assert wakeups["sessions_gt_3_count"] == 2
    assert "sess-w2" in wakeups["sessions_gt_3_ids"]
    assert "sess-w3" in wakeups["sessions_gt_3_ids"]
    assert wakeups["total_pr_sessions"] == 3


def test_30d_metrics_startup_context_per_environment(temp_store: SessionTelemetryStore) -> None:
    temp_store.record_session(
        {
            "session_id": "sess-c1",
            "environment": "cloud",
            "context_size": 42000,
        }
    )
    temp_store.record_session(
        {
            "session_id": "sess-c2",
            "environment": "cloud",
            "context_size": 44000,
        }
    )
    temp_store.record_session(
        {
            "session_id": "sess-l1",
            "environment": "linux",
            "context_size": 95000,
        }
    )

    metrics = temp_store.get_metrics(window_days=30)
    env_metrics = metrics["startup_context_by_environment"]
    assert "cloud" in env_metrics
    assert env_metrics["cloud"]["count"] == 2
    assert env_metrics["cloud"]["median"] == 43000
    assert "linux" in env_metrics
    assert env_metrics["linux"]["count"] == 1
    assert env_metrics["linux"]["median"] == 95000


def test_30d_metrics_pre_push_and_docs_conflicts(temp_store: SessionTelemetryStore) -> None:
    temp_store.record_session(
        {
            "session_id": "sess-p1",
            "pre_push_duration_s": 60.0,
            "docs_merge_conflicts": 1,
        }
    )
    temp_store.record_session(
        {
            "session_id": "sess-p2",
            "pre_push_duration_s": 120.0,
            "docs_merge_conflicts": 0,
        }
    )
    temp_store.record_session(
        {
            "session_id": "sess-p3",
            "pre_push_duration_s": 180.0,
            "docs_merge_conflicts": 2,
        }
    )

    metrics = temp_store.get_metrics(window_days=30)
    pre_push = metrics["pre_push_duration"]
    assert pre_push["count"] == 3
    assert pre_push["median_s"] == 120.0
    assert pre_push["p95_s"] == 174.0

    docs = metrics["docs_merge_conflicts"]
    assert docs["total_conflicts"] == 3
    assert docs["sessions_with_conflicts"] == 2


def test_30d_trends_series(temp_store: SessionTelemetryStore) -> None:
    today = datetime.now(UTC)
    yesterday = today - timedelta(days=1)

    temp_store.record_session(
        {
            "session_id": "sess-y",
            "cost_usd": 15.0,
            "linked_pr": 401,
            "pr_merged": True,
            "recorded_at": yesterday.isoformat(),
        }
    )
    temp_store.record_session(
        {
            "session_id": "sess-t",
            "cost_usd": 25.0,
            "linked_pr": 402,
            "pr_merged": True,
            "recorded_at": today.isoformat(),
        }
    )

    metrics = temp_store.get_metrics(window_days=30)
    trends = metrics["trends_30d"]
    assert isinstance(trends, list)
    assert len(trends) > 0
    # Each trend item contains required trend keys
    point = trends[-1]
    assert "date" in point
    assert "merged_pr_cost_median" in point
    assert "cost_after_pr_total" in point
    assert "wakeups_after_pr_gt_3_count" in point
    assert "docs_conflicts_total" in point


def test_api_session_telemetry_endpoints(client: TestClient) -> None:
    # 1. Post telemetry record with alert trigger
    resp = client.post(
        "/api/usage/session-telemetry",
        json={
            "session_id": "sess-api-01",
            "cost_usd": 32.0,
            "context_size": 41000,
            "environment": "cloud",
            "turns_count": 20,
            "wakeups_count": 7,
            "wakeups_after_pr": 6,  # triggers alert (>5)
            "cost_after_pr_usd": 22.50,  # triggers alert (>$20)
            "origin": "dashboard-dispatched",
            "linked_pr": 1849,
            "pr_merged": True,
            "model": "claude-3-7-sonnet",
            "pre_push_duration_s": 85.0,
            "docs_merge_conflicts": 0,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "sess-api-01"
    assert data["is_alert"] is True
    assert len(data["alert_reasons"]) == 2

    # 2. Get session telemetry list
    list_resp = client.get("/api/usage/session-telemetry")
    assert list_resp.status_code == 200
    records = list_resp.json()
    assert len(records) >= 1
    assert records[0]["session_id"] == "sess-api-01"

    # 3. Get session metrics summary
    metrics_resp = client.get("/api/usage/session-metrics")
    assert metrics_resp.status_code == 200
    metrics_data = metrics_resp.json()
    assert "cost_per_merged_pr" in metrics_data
    assert "cost_after_pr" in metrics_data
    assert "post_pr_wakeups" in metrics_data
    assert "startup_context_by_environment" in metrics_data
    assert "pre_push_duration" in metrics_data
    assert "docs_merge_conflicts" in metrics_data
    assert "trends_30d" in metrics_data
    assert "alerts" in metrics_data
    assert len(metrics_data["alerts"]) == 1
    assert metrics_data["alerts"][0]["session_id"] == "sess-api-01"
