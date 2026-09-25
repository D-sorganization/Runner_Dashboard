"""API tests for Stalled-Job Detection and Maintenance Playbooks (SC-E5, Issue #1322)."""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from server import app
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.maintenance import reset_maintenance_cooldowns
from staff.thread_bus import reset_thread_bus

UTC = getattr(_dt, "UTC", _dt.UTC)

TEST_OPERATOR = Principal(
    id="operator-user",
    type="human",
    name="Operator",
    roles=["operator", "owner"],
    scopes=[
        "staff.chat",
        "staff.read",
        "staff.approve",
        "staff.dispatch",
        "fleet.maintain",
        "admin",
        "owner",
    ],
)


@pytest.fixture(autouse=True)
def clean_maintenance_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_file = tmp_path / "api_detect_test.sqlite3"
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


def test_api_detect_stalled_auto_remediate_low_risk(client: TestClient) -> None:
    """Stalled queued run with idle matching runner is auto-remediated via cancel+rerun."""
    now = _dt.datetime.now(UTC)
    old_time = (now - _dt.timedelta(minutes=50)).isoformat()

    payload = {
        "queued_runs": [
            {
                "id": 9901,
                "repo": "UpstreamDrift",
                "workflow_name": "build.yml",
                "status": "queued",
                "created_at": old_time,
                "labels": ["linux"],
            }
        ],
        "runners": [
            {
                "name": "idle-linux-box",
                "status": "online",
                "busy": False,
                "labels": ["linux"],
            }
        ],
        "auto_remediate": True,
    }

    res = client.post(
        "/api/v1/staff/maintenance/detect-stalled",
        json=payload,
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert res.status_code == 200, res.text
    data = res.json()

    assert len(data["detections"]) == 1
    det = data["detections"][0]
    assert det["type"] == "queued_too_long"
    assert det["recommended_action"] == "maintenance.cancel_and_rerun"

    # Verified auto-executed
    assert len(data["auto_executed"]) == 1
    assert data["auto_executed"][0]["action"] == "maintenance.cancel_and_rerun"
    assert data["auto_executed"][0]["success"] is True

    # Verified Maintenance thread message posted
    store = get_conversation_store()
    threads = store.list_threads()
    maint_threads = [t for t in threads if "maintenance" in t.participants or t.title == "Fleet Maintenance"]
    assert len(maint_threads) >= 1


def test_api_detect_stalled_high_risk_creates_proposal_for_approval(
    client: TestClient,
) -> None:
    """Ghost runner detection creates proposal awaiting operator/owner decision."""
    payload = {
        "runners": [
            {
                "id": 8801,
                "name": "ghost-rogue-runner",
                "status": "offline",
                "host": "dead-node",
                "offline_days": 10.0,
            }
        ],
        "known_hosts": ["active-node-1"],
        "auto_remediate": True,
    }

    res = client.post(
        "/api/v1/staff/maintenance/detect-stalled",
        json=payload,
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert res.status_code == 200, res.text
    data = res.json()

    assert len(data["detections"]) == 1
    assert len(data["auto_executed"]) == 0  # High risk NOT auto executed
    assert len(data["proposals_created"]) == 1

    prop = data["proposals_created"][0]
    assert prop["action"] == "maintenance.runner_remove"
    assert prop["risk"] == "high"
    assert prop["state"] == "proposed"

    # Now approve and execute proposal via API
    decide_res = client.post(
        f"/api/v1/staff/proposals/{prop['id']}/decide",
        json={
            "decision": "approved",
            "execute": True,
            "reason": "Decommission dead runner",
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert decide_res.status_code == 200, decide_res.text
    decide_data = decide_res.json()
    assert decide_data["state"] in ("approved", "done")
    assert decide_data["execution_result"]["success"] is True
