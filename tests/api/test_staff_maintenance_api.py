"""API tests for Maintenance Action Catalogue (SC-E3, Issue #1321)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from server import app
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.maintenance import reset_maintenance_cooldowns
from staff.thread_bus import reset_thread_bus

TEST_OPERATOR = Principal(
    id="operator-user",
    type="human",
    name="Operator",
    roles=["operator", "owner"],
    scopes=["staff.chat", "staff.read", "staff.approve", "staff.dispatch", "fleet.maintain", "admin", "owner"],
)


@pytest.fixture(autouse=True)
def clean_maintenance_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_file = tmp_path / "api_maintenance_test.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    reset_conversation_store()
    reset_thread_bus()
    reset_maintenance_cooldowns()

    app.dependency_overrides[require_principal] = lambda: TEST_OPERATOR
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_OPERATOR
    app.dependency_overrides[require_scope("staff.approve")] = lambda: TEST_OPERATOR
    app.dependency_overrides[require_scope("staff.chat")] = lambda: TEST_OPERATOR
    app.dependency_overrides[require_scope("fleet.maintain")] = lambda: TEST_OPERATOR

    yield
    app.dependency_overrides.clear()
    reset_conversation_store()
    reset_thread_bus()
    reset_maintenance_cooldowns()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, base_url="http://testserver")


def test_list_and_get_maintenance_actions_api(client: TestClient) -> None:
    res = client.get("/api/v1/staff/actions")
    assert res.status_code == 200, res.text
    data = res.json()
    action_names = [a["name"] for a in data.get("actions", [])]

    assert "maintenance.runner_start" in action_names
    assert "maintenance.runner_stop" in action_names
    assert "maintenance.runner_restart" in action_names
    assert "maintenance.runner_drain" in action_names
    assert "maintenance.fleet_control" in action_names
    assert "maintenance.vacuum_sqlite" in action_names
    assert "maintenance.trim_worktrees" in action_names

    # Detail check
    stop_res = client.get("/api/v1/staff/actions/maintenance.runner_stop")
    assert stop_res.status_code == 200
    stop_data = stop_res.json()
    assert stop_data["name"] == "maintenance.runner_stop"
    assert stop_data["risk_class"] == "high"
    assert "drain" in stop_data["params_schema"]


def test_maintenance_proposal_create_and_dry_run_api(client: TestClient) -> None:
    store = get_conversation_store()
    th = store.create_thread(title="Maintenance Thread", kind="direct", participants=["barb", "operator"])
    msg = store.add_message(
        thread_id=th.id,
        author_kind="role",
        author="barb",
        body_md="Proposing runner restart",
    )

    headers = {"X-Requested-With": "XMLHttpRequest"}
    prop_res = client.post(
        "/api/v1/staff/proposals",
        json={
            "message_id": msg.id,
            "thread_id": th.id,
            "action": "maintenance.runner_restart",
            "params": {"runner_name": "deskcomputer-1", "host": "local", "dry_run": True},
            "risk": "medium",
        },
        headers=headers,
    )
    assert prop_res.status_code == 200, prop_res.text
    prop_id = prop_res.json()["id"]

    # Decide with immediate execution of dry_run
    decide_res = client.post(
        f"/api/v1/staff/proposals/{prop_id}/decide",
        json={"decision": "approved", "reason": "operator dry-run approval", "execute": True},
        headers=headers,
    )
    assert decide_res.status_code == 200, decide_res.text
    decide_data = decide_res.json()
    assert decide_data["state"] == "done"
    exec_res = decide_data["execution_result"]
    assert exec_res["success"] is True
    assert exec_res["result"]["dry_run"] is True


def test_maintenance_blast_radius_rejection_api(client: TestClient) -> None:
    store = get_conversation_store()
    th = store.create_thread(title="Blast Radius Thread", kind="direct", participants=["barb", "operator"])
    msg = store.add_message(
        thread_id=th.id,
        author_kind="role",
        author="barb",
        body_md="Proposing batch group stop",
    )

    headers = {"X-Requested-With": "XMLHttpRequest"}
    # Create proposal with excessive max_count
    prop_res = client.post(
        "/api/v1/staff/proposals",
        json={
            "message_id": msg.id,
            "thread_id": th.id,
            "action": "maintenance.group_stop",
            "params": {"group_label": "ci", "max_count": 99},
            "risk": "high",
        },
        headers=headers,
    )
    assert prop_res.status_code == 200
    prop_id = prop_res.json()["id"]

    # Approving and executing records failure due to blast-radius limit
    decide_res = client.post(
        f"/api/v1/staff/proposals/{prop_id}/decide",
        json={"decision": "approved", "reason": "operator approval", "execute": True},
        headers=headers,
    )
    assert decide_res.status_code == 200
    decide_data = decide_res.json()
    assert decide_data["state"] == "failed"
    assert decide_data["execution_result"]["success"] is False
    assert "blast-radius" in decide_data["execution_result"]["error"]


def test_maintenance_scan_api(client: TestClient) -> None:
    now = 1770000000.0
    runs = [
        {
            "id": 801,
            "repo": "Runner_Dashboard",
            "status": "queued",
            "created_at_ts": now - 3600,
            "labels": ["self-hosted", "linux"],
        }
    ]
    runners = [
        {
            "name": "idle-runner-1",
            "status": "online",
            "busy": False,
            "labels": ["self-hosted", "linux"],
            "host": "local-node",
        }
    ]

    headers = {"X-Requested-With": "XMLHttpRequest"}
    with patch("time.time", return_value=now):
        res = client.post(
            "/api/v1/staff/maintenance/scan",
            json={"runs": runs, "runners": runners, "known_inventory": ["idle-runner-1"]},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert "issues" in data
        assert len(data["issues"]) >= 1
        assert data["issues"][0]["detector"] == "queued_too_long"


def test_maintenance_remediate_self_heal_api(client: TestClient) -> None:
    headers = {"X-Requested-With": "XMLHttpRequest"}
    with patch("staff.maintenance_playbooks.execute_maintenance") as mock_exec:
        from staff.actions import ActionResult

        mock_exec.return_value = ActionResult(success=True, result={"status": "restarted"})
        res = client.post(
            "/api/v1/staff/maintenance/remediate",
            json={
                "issue_id": "wedged_listener:runner-99",
                "detector": "wedged_listener",
                "severity": "low",
                "target": "runner-99",
                "host": "local-node",
                "suggested_action": "maintenance.runner_restart",
                "risk_class": "low",
                "self_heal_eligible": True,
            },
            headers=headers,
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == "self_healed"
        assert data["action"] == "maintenance.runner_restart"


def test_maintenance_remediate_proposal_api(client: TestClient) -> None:
    headers = {"X-Requested-With": "XMLHttpRequest"}
    res = client.post(
        "/api/v1/staff/maintenance/remediate",
        json={
            "issue_id": "ghost_runners:runner-ghost",
            "detector": "ghost_runners",
            "severity": "high",
            "target": "runner-ghost",
            "host": "unknown-node",
            "suggested_action": "maintenance.fleet_control",
            "risk_class": "high",
            "self_heal_eligible": False,
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "proposed"
    assert data["proposal_id"] is not None
