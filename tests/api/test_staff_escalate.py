"""POST /api/staff/escalate"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from escalation.merge_jump import EscalateResult
from escalation.store import reset_escalation_store
from fastapi.testclient import TestClient
from identity import Principal, TokenRecord, identity_manager

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.15", 52000)
_HUB = "hub-fleet-secret-test"  # pragma: allowlist secret


@pytest.fixture
def admin_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    id_dir = tmp_path / "identity"
    id_dir.mkdir(parents=True)
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(id_dir))
    monkeypatch.setenv("HUB_FLEET_TOKEN", _HUB)
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("ESCALATION_EVENTS_DB", str(tmp_path / "esc.sqlite3"))
    monkeypatch.setenv("STAFF_HOLDS_FILE", str(tmp_path / "holds.json"))
    reset_escalation_store()
    raw = "svc_admin_escalate_test"  # pragma: allowlist secret
    identity_manager.config_dir = id_dir
    identity_manager.principals = {
        "dieter": Principal(id="dieter", type="human", name="Dieter", roles=["operator", "admin"]),
    }
    identity_manager.tokens = [
        TokenRecord(
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            principal_id="dieter",
            created_at=0.0,
            name="dieter-test",
        )
    ]
    return {"token": raw}


@pytest.fixture
def remote_client(admin_env: dict[str, str]) -> Iterator[TestClient]:
    from server import app  # noqa: PLC0415

    yield TestClient(app, raise_server_exceptions=False, client=_REMOTE)


@pytest.mark.integration
def test_staff_escalate_manual_required(remote_client: TestClient, admin_env: dict[str, str]) -> None:
    pr = {
        "state": "open",
        "labels": [],
        "mergeable_state": "clean",
        "id": "PR_kwDO",
        "head": {"sha": "abc123"},
    }
    with (
        patch("routers.escalation.gh_api", new_callable=AsyncMock, return_value=pr),
        patch(
            "routers.escalation.enqueue_jump",
            new_callable=AsyncMock,
            return_value=EscalateResult(
                ok=False,
                message="no bypass",
                manual_command="gh-personal api graphql ...",
            ),
        ),
    ):
        res = remote_client.post(
            "/api/staff/escalate",
            json={"repo": "Runner_Dashboard", "pr": 7, "reason": "production hotfix"},
            headers={"Authorization": f"Bearer {admin_env['token']}", **_XHR},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "manual_required"
    assert "gh-personal" in body["gh_personal_command"]


@pytest.mark.integration
def test_staff_escalate_jump_ignored(remote_client: TestClient, admin_env: dict[str, str]) -> None:
    pr = {
        "state": "open",
        "labels": [],
        "mergeable_state": "clean",
        "id": "PR_kwDO",
        "head": {"sha": "abc123"},
    }
    with (
        patch("routers.escalation.gh_api", new_callable=AsyncMock, return_value=pr),
        patch(
            "routers.escalation.enqueue_jump",
            new_callable=AsyncMock,
            return_value=EscalateResult(
                ok=False,
                jump_ignored=True,
                message="enqueuePullRequest succeeded but jump did not take effect: merge queue position 27 of 27.",
                detail={"position": 27, "queue_depth": 27},
            ),
        ),
    ):
        res = remote_client.post(
            "/api/staff/escalate",
            json={"repo": "Runner_Dashboard", "pr": 2908, "reason": "field trial"},
            headers={"Authorization": f"Bearer {admin_env['token']}", **_XHR},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "jump_ignored"
    assert body["merge_queue_entry"]["position"] == 27
