"""API tests for the remaining work-request kinds (SC-G5-1 slice B, #1497).

Covers kinds:
- ``ci.remediate`` (remediation.dispatch)
- ``issue.act`` (workflows.dispatch)
- ``pr.act`` (workflows.dispatch)
- ``code_request.dispatch`` (code_requests.write)
- ``assessment.run`` (assessments.dispatch)
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from server import app
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.thread_bus import reset_thread_bus
from staff.work_items import get_work_item_store, reset_work_item_store

_XHR = {"X-Requested-With": "XMLHttpRequest"}
URL = "/api/v1/staff/requests"

OPERATOR = Principal(
    id="operator-user",
    type="human",
    name="Operator",
    roles=["operator"],
    scopes=[
        "staff.read",
        "staff.chat",
        "staff.dispatch",
        "staff.approve",
        "remediation.dispatch",
        "workflows.dispatch",
        "code_requests.write",
        "assessments.dispatch",
    ],
)
READER = Principal(id="reader", type="human", name="Reader", roles=[], scopes=["staff.read"])
UNPRIVILEGED_DISPATCHER = Principal(
    id="dispatcher",
    type="human",
    name="Dispatcher",
    roles=[],
    scopes=[
        "staff.read",
        "remediation.dispatch",
        "workflows.dispatch",
        "code_requests.write",
        "assessments.dispatch",
    ],
)


@pytest.fixture(autouse=True)
def _stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_runs.sqlite3"))
    reset_conversation_store()
    reset_work_item_store()
    reset_thread_bus()
    app.dependency_overrides[require_principal] = lambda: OPERATOR
    yield
    app.dependency_overrides.clear()
    get_conversation_store().close()
    reset_conversation_store()
    reset_work_item_store()
    reset_thread_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, base_url="http://testserver")


def _as(principal: Principal) -> None:
    app.dependency_overrides[require_principal] = lambda: principal


# ─── ci.remediate tests ────────────────────────────────────────────────────────


def test_ci_remediate_dry_run_returns_plan(client: TestClient) -> None:
    body = {
        "kind": "ci.remediate",
        "target": {"repo": "Tools", "run_id": 12345},
        "provider": "claude",
        "prompt": "Fix flakiness",
        "dry_run": True,
    }
    resp = client.post(URL, json=body, headers=_XHR)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["state"] == "planned"
    assert data["kind"] == "ci.remediate"
    assert data["action"] == "ci.remediate"
    assert data["plan"]["repo"] == "Tools"
    assert data["plan"]["run_id"] == 12345
    assert get_work_item_store().list_work_items() == []


def test_ci_remediate_executes_and_creates_work_item(client: TestClient) -> None:
    body = {
        "kind": "ci.remediate",
        "target": {"repo": "Tools", "run_id": 12345},
        "provider": "claude",
        "prompt": "Fix flakiness",
    }
    with patch("staff.work_request_executors._dispatch_remediation_workflow", new_callable=AsyncMock) as mock_disp:
        mock_disp.return_value = {"workflow": "Agent-CI-Remediation.yml", "status": "dispatched"}
        resp = client.post(URL, json=body, headers=_XHR)

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["state"] == "executed"
    assert data["action"] == "ci.remediate"
    wi = get_work_item_store().get_work_item(data["work_item_id"])
    assert wi is not None


def test_ci_remediate_rejects_incompatible_targets(client: TestClient) -> None:
    body = {
        "kind": "ci.remediate",
        "target": {"repo": "Tools", "issue": 42},
    }
    resp = client.post(URL, json=body, headers=_XHR)
    assert resp.status_code == 422, resp.text
    assert "does not accept issue" in resp.text


# ─── issue.act and pr.act tests ────────────────────────────────────────────────


def test_issue_act_dry_run_returns_plan(client: TestClient) -> None:
    body = {
        "kind": "issue.act",
        "target": {"repo": "Tools", "issue": 42},
        "provider": "claude",
        "prompt": "Investigate bug",
        "dry_run": True,
    }
    resp = client.post(URL, json=body, headers=_XHR)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["state"] == "planned"
    assert data["action"] == "issue.act"
    assert data["plan"]["issue"] == 42


def test_issue_act_executes_and_records_work_item(client: TestClient) -> None:
    body = {
        "kind": "issue.act",
        "target": {"repo": "Tools", "issue": 42},
        "provider": "claude",
        "prompt": "Investigate bug",
    }
    with patch("staff.work_request_executors._dispatch_issue_action", new_callable=AsyncMock) as mock_disp:
        mock_disp.return_value = {"status": "dispatched", "envelope_id": "env-123"}
        resp = client.post(URL, json=body, headers=_XHR)

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["state"] == "executed"
    assert data["action"] == "issue.act"


def test_pr_act_dry_run_and_execution(client: TestClient) -> None:
    body = {
        "kind": "pr.act",
        "target": {"repo": "Tools", "pr": 99},
        "provider": "claude",
        "prompt": "Review changes",
        "dry_run": True,
    }
    resp = client.post(URL, json=body, headers=_XHR)
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "planned"

    with patch("staff.work_request_executors._dispatch_pr_action", new_callable=AsyncMock) as mock_disp:
        mock_disp.return_value = {"status": "dispatched", "envelope_id": "env-456"}
        resp = client.post(URL, json={**body, "dry_run": False}, headers=_XHR)
    assert resp.status_code == 201, resp.text
    assert resp.json()["state"] == "executed"


# ─── code_request.dispatch tests ───────────────────────────────────────────────


def test_code_request_dispatch_dry_run_and_execution(client: TestClient) -> None:
    body = {
        "kind": "code_request.dispatch",
        "target": {"repo": "Tools", "ref": "feat/my-feature"},
        "provider": "claude",
        "prompt": "Implement calculator",
        "profile_id": "planner-strong",
        "dry_run": True,
    }
    resp = client.post(URL, json=body, headers=_XHR)
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "planned"

    with patch("staff.work_request_executors._dispatch_code_request_workflow", new_callable=AsyncMock) as mock_disp:
        mock_disp.return_value = {"status": "dispatched", "code": 0}
        resp = client.post(URL, json={**body, "dry_run": False}, headers=_XHR)
    assert resp.status_code == 201, resp.text
    assert resp.json()["state"] == "executed"


# ─── assessment.run tests ─────────────────────────────────────────────────────


def test_assessment_run_dry_run_and_execution(client: TestClient) -> None:
    body = {
        "kind": "assessment.run",
        "target": {"repo": "Tools"},
        "provider": "jules_api",
        "prompt": "Run quality assessment",
        "dry_run": True,
    }
    resp = client.post(URL, json=body, headers=_XHR)
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "planned"
    assert resp.json()["action"] == "assessment.run"

    with patch("staff.work_request_executors._dispatch_assessment_workflow", new_callable=AsyncMock) as mock_disp:
        mock_disp.return_value = {"status": "dispatched", "repository": "Tools"}
        resp = client.post(URL, json={**body, "dry_run": False}, headers=_XHR)
    assert resp.status_code == 201, resp.text
    assert resp.json()["state"] == "executed"


# ─── Permissions & Approvals ──────────────────────────────────────────────────


def test_requester_lacking_scope_is_forbidden(client: TestClient) -> None:
    _as(READER)
    for kind, target in [
        ("ci.remediate", {"repo": "Tools", "run_id": 1}),
        ("issue.act", {"repo": "Tools", "issue": 1}),
        ("pr.act", {"repo": "Tools", "pr": 1}),
        ("code_request.dispatch", {"repo": "Tools", "ref": "main"}),
        ("assessment.run", {"repo": "Tools"}),
    ]:
        resp = client.post(URL, json={"kind": kind, "target": target, "prompt": "p"}, headers=_XHR)
        assert resp.status_code == 403, f"Expected 403 for {kind}, got {resp.status_code}: {resp.text}"


def test_unprivileged_requester_triggers_approval_required(client: TestClient) -> None:
    _as(UNPRIVILEGED_DISPATCHER)  # Has action scope but lacks staff.approve
    body = {
        "kind": "ci.remediate",
        "target": {"repo": "Tools", "run_id": 12345},
        "prompt": "Fix build",
    }
    resp = client.post(URL, json=body, headers=_XHR)
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["state"] == "approval_required"
    assert "staff.approve" in data["approval"]
