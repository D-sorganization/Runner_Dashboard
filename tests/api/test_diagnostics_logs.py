"""Tests for RD #2235: Run-correlated structured logs and capped diagnostics log API."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from request_context import RequestIdLogFilter, run_id_ctx
from server import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _principal(scopes: list[str]) -> Principal:
    return Principal(id="agent-test", type="bot", name="TestAgent", roles=["staff"], scopes=scopes)


def test_run_id_in_log_records() -> None:
    token = run_id_ctx.set("run-test-999")
    try:
        record = logging.LogRecord("test.logger", logging.INFO, "path", 1, "test message", (), None)
        assert RequestIdLogFilter().filter(record)
        assert getattr(record, "run_id", "") == "run-test-999"
    finally:
        run_id_ctx.reset(token)


def test_log_ring_buffer_redaction_and_caps() -> None:
    from diagnostics_logs import LogRingBuffer

    buf = LogRingBuffer(max_records=10, max_bytes=2048)
    fake_token = "ghp_" + "A" * 36
    secret_msg = (
        f"Token: {fake_token} Bearer sample-bearer-token" + " Authorization: Bearer secret-token " + ("x" * 3000)
    )
    buf.add_record("2026-10-22T20:00:00Z", "INFO", "test.logger", secret_msg, "run-1", "req-1")
    records, _ = buf.query(limit=10)
    assert len(records) == 1
    assert "[REDACTED" in records[0]["message"]
    assert fake_token not in records[0]["message"]
    assert len(records[0]["message"]) <= 2000


def test_get_diagnostics_logs_auth_and_caps(client: TestClient) -> None:
    from diagnostics_logs import get_log_ring_buffer

    buf = get_log_ring_buffer()
    buf.clear()
    buf.add_record("2026-10-22T20:00:01Z", "INFO", "dash.test", "msg 1", run_id="run-A", request_id="req-1")
    buf.add_record("2026-10-22T20:00:02Z", "ERROR", "dash.err", "msg 2", run_id="run-B", request_id="req-2")

    app.dependency_overrides[require_principal] = lambda: _principal(["unrelated.read"])
    assert client.get("/api/v1/diagnostics/logs").status_code == 403

    app.dependency_overrides[require_principal] = lambda: _principal(["staff.read"])
    resp = client.get("/api/v1/diagnostics/logs")
    assert resp.status_code == 200 and resp.json()["count"] >= 2

    resp_filtered = client.get("/api/v1/diagnostics/logs?run_id=run-A")
    assert resp_filtered.status_code == 200
    assert len(resp_filtered.json()["logs"]) == 1 and resp_filtered.json()["logs"][0]["run_id"] == "run-A"

    assert client.get("/api/v1/diagnostics/logs?limit=501").status_code == 422
    assert client.post("/api/v1/diagnostics/logs").status_code in (405, 403)
    app.dependency_overrides.clear()


def test_run_detail_returns_logs_ref(client: TestClient) -> None:
    from staff.runner import get_runner
    from staff.store import RunRecord

    rec = RunRecord(
        id="run-detail-test-1",
        role="ad-hoc",
        provider="claude",
        model="sonnet",
        machine="local",
        target_kind="issue",
        target_ref="1",
        prompt="test",
        repo="Tools",
        status="running",
        created_at="2026-10-22T20:00:00Z",
        started_at="2026-10-22T20:00:01Z",
    )
    get_runner().store.create_run(rec)
    app.dependency_overrides[require_principal] = lambda: _principal(["staff.read"])
    resp = client.get("/api/v1/staff/runs/run-detail-test-1")
    assert resp.status_code == 200
    assert resp.json().get("logs_ref") == "/api/v1/diagnostics/logs?run_id=run-detail-test-1"
    app.dependency_overrides.clear()
