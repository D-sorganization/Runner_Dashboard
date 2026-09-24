"""Tests for usage metrics router and page view tracking (issue #1302 / SC-G1).

Verifies:
- Page view recording per tab.
- API call tracking per endpoint.
- Usage summary aggregation with keep / merge / retire annotations.
- Markdown table formatting for SC-G epic reporting.
- Disabling counter via DASHBOARD_USAGE_METRICS_ENABLED environment variable.
- Validation and error handling on malformed inputs.
- 14-day rolling window retention.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from routers.usage_metrics import (  # noqa: E402
    UsageTracker,
    create_usage_metrics_router,
)


@pytest.fixture
def temp_tracker(tmp_path: Path) -> UsageTracker:
    data_file = tmp_path / "usage_metrics.json"
    return UsageTracker(data_file=data_file, retention_days=14, enabled=True)


@pytest.fixture
def client(temp_tracker: UsageTracker) -> TestClient:
    from fastapi import FastAPI

    app = FastAPI()
    router = create_usage_metrics_router(tracker=temp_tracker)
    app.include_router(router)
    return TestClient(app)


def test_record_page_view(client: TestClient, temp_tracker: UsageTracker) -> None:
    resp = client.post("/api/usage/page-view", json={"tab_id": "overview"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "recorded"
    assert data["tab_id"] == "overview"
    assert data["count"] == 1

    resp2 = client.post("/api/usage/page-view", json={"tab_id": "overview"})
    assert resp2.status_code == 200
    assert resp2.json()["count"] == 2

    resp3 = client.post("/api/usage/page-view", json={"tab_id": "queue"})
    assert resp3.status_code == 200
    assert resp3.json()["count"] == 1


def test_record_endpoint_call(temp_tracker: UsageTracker) -> None:
    temp_tracker.record_api_call("GET", "/api/system")
    temp_tracker.record_api_call("GET", "/api/system")
    temp_tracker.record_api_call("POST", "/api/runners/1/start")

    summary = temp_tracker.get_summary()
    assert summary["endpoint_calls"]["GET /api/system"] == 2
    assert summary["endpoint_calls"]["POST /api/runners/1/start"] == 1
    assert summary["total_api_calls"] == 3


def test_summary_tab_analysis(client: TestClient, temp_tracker: UsageTracker) -> None:
    client.post("/api/usage/page-view", json={"tab_id": "overview"})
    client.post("/api/usage/page-view", json={"tab_id": "overview"})
    client.post("/api/usage/page-view", json={"tab_id": "cline-launcher"})
    temp_tracker.record_api_call("GET", "/api/fleet/status")

    resp = client.get("/api/usage/summary")
    assert resp.status_code == 200
    body = resp.json()

    assert body["enabled"] is True
    assert body["total_page_views"] == 3
    assert body["total_api_calls"] == 1
    assert body["page_views"]["overview"] == 2
    assert body["page_views"]["cline-launcher"] == 1

    analysis = {item["tab_id"]: item for item in body["tab_analysis"]}
    assert "overview" in analysis
    assert analysis["overview"]["views"] == 2
    assert analysis["overview"]["recommendation"] == "keep"

    assert "cline-launcher" in analysis
    assert analysis["cline-launcher"]["views"] == 1
    assert analysis["cline-launcher"]["recommendation"] == "retire"

    assert "reports" in analysis
    assert analysis["reports"]["views"] == 0
    assert analysis["reports"]["recommendation"] == "merge"


def test_markdown_table_generation(client: TestClient) -> None:
    client.post("/api/usage/page-view", json={"tab_id": "overview"})
    client.post("/api/usage/page-view", json={"tab_id": "queue"})

    resp = client.get("/api/usage/table")
    assert resp.status_code == 200
    markdown = resp.text

    assert "| Tab ID | Views | Recommendation | Notes |" in markdown
    assert "| `overview` |" in markdown
    assert "| `queue` |" in markdown
    assert "keep" in markdown


def test_disabled_via_env_var(tmp_path: Path) -> None:
    with patch.dict(os.environ, {"DASHBOARD_USAGE_METRICS_ENABLED": "false"}):
        tracker = UsageTracker(data_file=tmp_path / "disabled.json")
        assert not tracker.is_enabled()

        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(create_usage_metrics_router(tracker=tracker))
        c = TestClient(app)

        resp = c.post("/api/usage/page-view", json={"tab_id": "overview"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "disabled"
        assert resp.json()["count"] == 0

        tracker.record_api_call("GET", "/api/system")
        summary = tracker.get_summary()
        assert summary["total_api_calls"] == 0
        assert summary["total_page_views"] == 0


def test_validation_errors(client: TestClient) -> None:
    # Empty body
    resp = client.post("/api/usage/page-view", json={})
    assert resp.status_code == 422

    # Invalid empty tab_id string
    resp = client.post("/api/usage/page-view", json={"tab_id": ""})
    assert resp.status_code == 422

    # Excessively long tab_id
    resp = client.post("/api/usage/page-view", json={"tab_id": "a" * 250})
    assert resp.status_code == 422


def test_retention_window_pruning(tmp_path: Path) -> None:
    tracker = UsageTracker(data_file=tmp_path / "retention.json", retention_days=14, enabled=True)

    today = datetime.now(UTC).date()
    old_date = (today - timedelta(days=20)).isoformat()
    recent_date = (today - timedelta(days=3)).isoformat()

    # Manually populate daily entries
    tracker._data["daily"][old_date] = {
        "page_views": {"overview": 99},
        "endpoint_calls": {"GET /api/old": 99},
    }
    tracker._data["daily"][recent_date] = {
        "page_views": {"overview": 5},
        "endpoint_calls": {"GET /api/recent": 10},
    }
    tracker._save()

    summary = tracker.get_summary()
    assert summary["page_views"].get("overview") == 5
    assert "GET /api/old" not in summary["endpoint_calls"]
    assert summary["endpoint_calls"].get("GET /api/recent") == 10

    # Pruned old date from persisted structure
    assert old_date not in tracker._data["daily"]
