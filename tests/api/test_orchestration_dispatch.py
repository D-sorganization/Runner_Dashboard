"""Tests for Fleet Orchestration dispatch and deploy endpoints (issue #1502).

Acceptance criteria:
- a gh failure produces dispatched: false + a classified error (upstream_error)
- machine_target reaches the workflow inputs
- deploy returns 501 not_wired while the audit still records the attempt
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import orchestration_audit as _audit
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from routers import orchestration
from routers.orchestration import OrchestrationDeps, orchestration_deps


@pytest.fixture
def mock_deps() -> OrchestrationDeps:
    return OrchestrationDeps(
        fleet_control_local=AsyncMock(return_value={"machine": "test-host", "status": "ok"}),
        remote_fleet_control=AsyncMock(return_value={"machine": "remote-host", "status": "ok"}),
        get_fleet_nodes_impl=AsyncMock(return_value={"nodes": []}),
        run_cmd=AsyncMock(return_value=(0, "{}", "")),
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_deps: OrchestrationDeps) -> TestClient:
    audit_file = tmp_path / "orchestration_audit.ndjson"
    monkeypatch.setattr(_audit, "ORCHESTRATION_AUDIT_PATH", audit_file)

    app = FastAPI()
    app.state.orchestration_deps = mock_deps
    app.dependency_overrides[orchestration_deps] = lambda: mock_deps
    app.dependency_overrides[require_scope("fleet.control")] = lambda: Principal(
        id="test-operator",
        type="user",
        name="Test Operator",
        scopes={"fleet.control"},
    )
    app.include_router(orchestration.router)
    return TestClient(app, raise_server_exceptions=True)


def test_dispatch_gh_failure_returns_classified_error_and_dispatched_false(
    client: TestClient,
    mock_deps: OrchestrationDeps,
) -> None:
    mock_deps.run_cmd.return_value = (1, "", "gh: repository not found or workflow disabled")

    resp = client.post(
        "/api/fleet/orchestration/dispatch",
        json={
            "repo": "Tools",
            "workflow": "ci.yml",
            "ref": "main",
            "machine_target": "DeskComputer",
            "approved_by": "test-operator",
        },
    )

    assert resp.status_code == 502
    data = resp.json()
    assert data["dispatched"] is False
    assert data["error"] == "upstream_error"
    assert "repository not found" in data["detail"]

    # Audit attempt must still be recorded
    audit_entries = _audit.load_orchestration_audit(limit=10)
    assert len(audit_entries) >= 1
    entry = audit_entries[0]
    assert entry.get("orchestration_type") == "workflow_dispatch"
    assert entry.get("repo") == "Tools"


def test_dispatch_injects_machine_target_into_inputs(
    client: TestClient,
    mock_deps: OrchestrationDeps,
) -> None:
    captured_payload: dict[str, Any] = {}

    async def fake_run_cmd(cmd: list[str], **kwargs: Any) -> tuple[int, str, str]:
        if "--input" in cmd:
            input_file = cmd[cmd.index("--input") + 1]
            content = Path(input_file).read_text(encoding="utf-8")
            captured_payload.update(json.loads(content))
        return (0, "{}", "")

    mock_deps.run_cmd.side_effect = fake_run_cmd

    resp = client.post(
        "/api/fleet/orchestration/dispatch",
        json={
            "repo": "Tools",
            "workflow": "ci.yml",
            "ref": "main",
            "machine_target": "DeskComputer",
            "inputs": {"test_suite": "unit"},
            "approved_by": "test-operator",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["dispatched"] is True
    assert captured_payload.get("inputs", {}).get("machine_target") == "DeskComputer"
    assert captured_payload.get("inputs", {}).get("test_suite") == "unit"


def test_dispatch_exception_returns_classified_error_and_dispatched_false(
    client: TestClient,
    mock_deps: OrchestrationDeps,
) -> None:
    mock_deps.run_cmd.side_effect = RuntimeError("gh process execution failed")

    resp = client.post(
        "/api/fleet/orchestration/dispatch",
        json={
            "repo": "Tools",
            "workflow": "ci.yml",
            "ref": "main",
            "approved_by": "test-operator",
        },
    )

    assert resp.status_code == 502
    data = resp.json()
    assert data["dispatched"] is False
    assert data["error"] == "upstream_error"
    assert "gh process execution failed" in data["detail"]


def test_dispatch_success_returns_dispatched_true(
    client: TestClient,
    mock_deps: OrchestrationDeps,
) -> None:
    mock_deps.run_cmd.return_value = (0, "{}", "")

    resp = client.post(
        "/api/fleet/orchestration/dispatch",
        json={
            "repo": "Tools",
            "workflow": "ci.yml",
            "ref": "main",
            "approved_by": "test-operator",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["dispatched"] is True
    assert data["repo"] == "Tools"
    assert data["workflow"] == "ci.yml"


def test_deploy_returns_501_not_wired_and_records_audit(
    client: TestClient,
) -> None:
    resp = client.post(
        "/api/fleet/orchestration/deploy",
        json={
            "machine": "DeskComputer",
            "action": "restart_runner",
            "confirmed": True,
        },
    )

    assert resp.status_code == 501
    data = resp.json()
    assert data["error"] == "not_wired"

    # Audit attempt must still be recorded
    audit_entries = _audit.load_orchestration_audit(limit=10)
    assert len(audit_entries) >= 1
    entry = audit_entries[0]
    assert entry.get("orchestration_type") == "fleet_deploy"
    assert entry.get("machine") == "DeskComputer"
    assert entry.get("deploy_action") == "restart_runner"
