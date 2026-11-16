"""Tests for ``POST /api/fleet/run-jwt`` (#3879 phase B).

Mint-correctness is exercised in ``tests/unit/test_run_jwt_service.py``;
these tests cover only the router's wiring — auth, hub-only guard, and the
response shape — following the same ``app.dependency_overrides`` precedent
as ``tests/unit/test_staff_relay_endpoint.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_fleet_peer
from routers import run_jwt
from routers.fleet_run_token import require_fleet_run_token_caller
from routers.run_jwt import router as run_jwt_router
from staff.run_jwt_service import RunJwtCredential
from staff.store import RunRecord


@pytest.fixture
def client_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(run_jwt.dashboard_config, "MACHINE_ROLE", "hub")
    app = FastAPI()
    app.include_router(run_jwt_router)
    app.dependency_overrides[require_fleet_peer] = lambda: "fleet-peer"
    app.dependency_overrides[require_fleet_run_token_caller] = lambda: Principal(
        id="fleet-peer", type="bot", name="Fleet Peer", roles=["fleet-peer"]
    )
    rec = RunRecord(
        id="run-1",
        role="maintenance",
        provider="claude",
        model="default",
        machine="local",
        repo="Runner_Dashboard",
        target_kind="prompt",
        target_ref="",
        prompt="x",
        status="running",
    )
    monkeypatch.setattr(run_jwt, "load_active_run_for_mint", lambda _rid: rec)
    monkeypatch.setattr(run_jwt, "lease_principal_id_for_run", lambda _rec: "agent-claude")
    monkeypatch.setattr(run_jwt, "assert_mint_caller_bound_to_run", lambda *args, **kwargs: None)
    target = Principal(id="agent-claude", type="bot", name="Claude", roles=["bot"])
    monkeypatch.setattr(run_jwt.identity_manager, "get_principal", lambda _pid: target)
    return {"app": app, "client": TestClient(app)}


def test_post_run_jwt_mints_and_returns_the_credential(
    client_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_mint_run_jwt(identity_manager, *, run_id, principal_id, caller_node):  # noqa: ANN001
        calls.append({"run_id": run_id, "principal_id": principal_id, "caller_node": caller_node})
        return RunJwtCredential(token="header.payload.sig", expires_at=1234.0, jti="jti-1")

    monkeypatch.setattr(run_jwt, "mint_run_jwt", fake_mint_run_jwt)

    resp = client_env["client"].post(
        "/api/fleet/run-jwt",
        json={"run_id": "run-1"},
        headers={"X-Fleet-Node": "desk-hub"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"token": "header.payload.sig", "expires_at": 1234.0, "jti": "jti-1"}
    assert calls == [{"run_id": "run-1", "principal_id": "agent-claude", "caller_node": "desk-hub"}]


def test_post_run_jwt_404s_off_hub(client_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_jwt.dashboard_config, "MACHINE_ROLE", "node")

    resp = client_env["client"].post(
        "/api/fleet/run-jwt",
        json={"run_id": "run-1"},
        headers={"X-Fleet-Node": "desk-hub"},
    )

    assert resp.status_code == 404


def test_post_run_jwt_rejects_blank_body_fields(client_env: dict[str, Any]) -> None:
    resp = client_env["client"].post(
        "/api/fleet/run-jwt",
        json={"run_id": ""},
        headers={"X-Fleet-Node": "desk-hub"},
    )

    assert resp.status_code == 422
