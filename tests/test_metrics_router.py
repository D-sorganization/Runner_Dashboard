"""Unit tests for lightweight Prometheus `/api/metrics` router (#2611)."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from routers import metrics as metrics_router  # noqa: E402


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(metrics_router.router)
    return TestClient(app)


def test_metrics_router_renders_all_gauges_and_counters(monkeypatch) -> None:
    fake_snapshot = {
        "nodes": {
            "node-c": {"online": 1, "cordon": 0, "disk_free_bytes": 53687091200},
            "node-a": {"online": 0, "cordon": 1, "disk_free_bytes": 10737418240},
        },
        "in_flight": 2,
        "runs_24h": {"ok": 14, "failed": 1, "blocked": 3},
        "spend_today_usd": {"claude": 0.0, "codex": 0.0},
        "attention_items": 1,
        "liveness_alerts": 0,
        "holds_active": 2,
    }
    monkeypatch.setattr(metrics_router, "_SNAPSHOT_OVERRIDE", lambda: fake_snapshot)
    client = _build_client()

    for path in ("/api/metrics", "/api/v1/metrics"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "no-store"
        assert resp.headers["content-type"].startswith("text/plain; version=0.0.4")
        body = resp.text
        assert 'rd_node_online{node="node-c"} 1' in body
        assert 'rd_node_online{node="node-a"} 0' in body
        assert 'rd_node_cordon{node="node-a"} 1' in body
        assert 'rd_node_disk_free_bytes{node="node-c"} 53687091200' in body
        assert "rd_staff_runs_in_flight 2" in body
        assert 'rd_staff_runs_24h_total{status="ok"} 14' in body
        assert 'rd_staff_runs_24h_total{status="failed"} 1' in body
        assert 'rd_staff_runs_24h_total{status="blocked"} 3' in body
        assert 'rd_spend_today_usd{provider="claude"} 0.0000' in body
        assert "rd_attention_items_total 1" in body
        assert "rd_liveness_alerts_total 0" in body
        assert "rd_holds_active_total 2" in body


def test_sanitize_metric_label_strips_unsafe_characters() -> None:
    assert metrics_router.sanitize_metric_label('node"1\nsecret=foo') == "node_1_secret_foo"
    assert metrics_router.sanitize_metric_label("   ") == "unknown"
