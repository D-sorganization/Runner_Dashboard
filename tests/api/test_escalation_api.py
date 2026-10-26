"""Escalation ingest and staff listing API."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from escalation.store import reset_escalation_store
from fastapi.testclient import TestClient
from identity import Principal, TokenRecord, identity_manager

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.15", 52000)
_HUB = "hub-fleet-secret-test"  # pragma: allowlist secret


@pytest.fixture
def escalation_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    id_dir = tmp_path / "identity"
    id_dir.mkdir(parents=True)
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(id_dir))
    monkeypatch.setenv("HUB_FLEET_TOKEN", _HUB)
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("ESCALATION_EVENTS_DB", str(tmp_path / "esc.sqlite3"))
    reset_escalation_store()
    raw_op = "svc_operator_escalation_test"  # pragma: allowlist secret
    identity_manager.config_dir = id_dir
    identity_manager.principals = {
        "dieter": Principal(id="dieter", type="human", name="Dieter", roles=["operator", "admin"]),
    }
    identity_manager.tokens = [
        TokenRecord(
            token_hash=hashlib.sha256(raw_op.encode()).hexdigest(),
            principal_id="dieter",
            created_at=0.0,
            name="dieter-test",
        )
    ]
    return {"operator": raw_op}


@pytest.fixture
def remote_client(escalation_env: dict[str, str]) -> Iterator[TestClient]:
    from server import app  # noqa: PLC0415

    yield TestClient(app, raise_server_exceptions=False, client=_REMOTE)


@pytest.mark.integration
def test_escalation_ingest_and_list(remote_client: TestClient, escalation_env: dict[str, str]) -> None:
    hub = {"Authorization": f"Bearer {_HUB}", **_XHR}
    assert remote_client.post(
        "/api/fleet/escalation-events",
        json={"repo": "Runner_Dashboard", "pr_number": 1, "github_actor": "x", "outcome": "applied"},
        headers=_XHR,
    ).status_code == 401
    post = remote_client.post(
        "/api/fleet/escalation-events",
        json={
            "repo": "D-sorganization/Runner_Dashboard",
            "pr_number": 42,
            "github_actor": "dieterolson",
            "outcome": "applied",
        },
        headers=hub,
    )
    assert post.status_code == 200 and post.json()["recorded"] is True
    listing = remote_client.get(
        "/api/staff/escalations",
        headers={"Authorization": f"Bearer {escalation_env['operator']}", **_XHR},
    )
    assert listing.status_code == 200
    assert listing.json()["active"][0]["pr_number"] == 42
