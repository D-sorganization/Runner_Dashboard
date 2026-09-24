"""Tests for staff mutation and read scopes (issue #1295, SC-F1)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import (
    Principal,
    TokenRecord,
    identity_manager,
    principal_has_scope,
)

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.15", 52000)


@pytest.fixture
def auth_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    """Configure identity directory with tokens for admin, operator, bot, and viewer."""
    id_dir = tmp_path / "identity"
    id_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(id_dir))
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-peer-secret-12345")
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "roles"))

    # Create dummy role dir so ad-hoc role is present
    from staff import runner as runner_mod
    from staff import store as store_mod

    store_mod.reset_store()
    runner_mod.reset_runner()

    # Reinitialize identity_manager with temp directory
    identity_manager.config_dir = id_dir
    identity_manager.principals_path = id_dir / "principals.yml"
    identity_manager.tokens_path = id_dir / "tokens.yml"

    principals = [
        Principal(id="admin-user", type="human", name="Admin", roles=["admin"]),
        Principal(id="operator-user", type="human", name="Operator", roles=["operator"]),
        Principal(id="bot-user", type="bot", name="Bot Agent", roles=["bot"]),
        Principal(id="viewer-user", type="human", name="Viewer", roles=["viewer"]),
    ]
    tokens = {
        "admin-token": "admin-user",
        "operator-token": "operator-user",
        "bot-token": "bot-user",
        "viewer-token": "viewer-user",
    }

    import hashlib

    token_records = []
    for raw_tok, p_id in tokens.items():
        token_hash = hashlib.sha256(raw_tok.encode()).hexdigest()
        token_records.append(
            TokenRecord(
                token_hash=token_hash,
                principal_id=p_id,
                created_at=0.0,
                name=p_id,
            )
        )

    identity_manager.principals = {p.id: p for p in principals}
    identity_manager.tokens = token_records
    identity_manager.save_principals()
    identity_manager.save_tokens()

    yield {
        "admin": "admin-token",
        "operator": "operator-token",
        "bot": "bot-token",
        "viewer": "viewer-token",
        "fleet": "fleet-peer-secret-12345",
    }

    store_mod.reset_store()
    runner_mod.reset_runner()


@pytest.fixture
def remote_client(auth_env: dict[str, str]) -> Iterator[TestClient]:
    from server import app

    yield TestClient(app, raise_server_exceptions=False, client=_REMOTE)


@pytest.mark.unit
def test_scope_presets_matrix() -> None:
    """Verify scope presets correctly distinguish staff capabilities."""
    admin = Principal(id="a", type="human", name="A", roles=["admin"])
    operator = Principal(id="o", type="human", name="O", roles=["operator"])
    bot = Principal(id="b", type="bot", name="B", roles=["bot"])
    viewer = Principal(id="v", type="human", name="V", roles=["viewer"])

    # staff.read
    assert principal_has_scope(admin, "staff.read")
    assert principal_has_scope(operator, "staff.read")
    assert principal_has_scope(bot, "staff.read")
    assert principal_has_scope(viewer, "staff.read")

    # staff.dispatch
    assert principal_has_scope(admin, "staff.dispatch")
    assert principal_has_scope(operator, "staff.dispatch")
    assert principal_has_scope(bot, "staff.dispatch")
    assert not principal_has_scope(viewer, "staff.dispatch")

    # staff.cancel
    assert principal_has_scope(admin, "staff.cancel")
    assert principal_has_scope(operator, "staff.cancel")
    assert principal_has_scope(bot, "staff.cancel")
    assert not principal_has_scope(viewer, "staff.cancel")

    # staff.holds.write
    assert principal_has_scope(admin, "staff.holds.write")
    assert principal_has_scope(operator, "staff.holds.write")
    assert not principal_has_scope(bot, "staff.holds.write")
    assert not principal_has_scope(viewer, "staff.holds.write")

    # staff.approve
    assert principal_has_scope(admin, "staff.approve")
    assert principal_has_scope(operator, "staff.approve")
    assert not principal_has_scope(bot, "staff.approve")
    assert not principal_has_scope(viewer, "staff.approve")

    # staff.admin
    assert principal_has_scope(admin, "staff.admin")
    assert principal_has_scope(operator, "staff.admin")
    assert not principal_has_scope(bot, "staff.admin")
    assert not principal_has_scope(viewer, "staff.admin")


@pytest.mark.integration
def test_dispatch_rejects_token_missing_staff_dispatch(remote_client: TestClient, auth_env: dict[str, str]) -> None:
    resp = remote_client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "test prompt", "dry_run": True},
        headers={**_XHR, "Authorization": f"Bearer {auth_env['viewer']}"},
    )
    assert resp.status_code == 403
    data = resp.json()
    assert data["detail"]["error"] == "Authorization failed"
    assert data["detail"]["required_scope"] == "staff.dispatch"
    assert data["detail"]["principal"] == "viewer-user"


@pytest.mark.integration
def test_dispatch_allows_token_with_staff_dispatch(remote_client: TestClient, auth_env: dict[str, str]) -> None:
    # Bot token has staff.dispatch
    resp = remote_client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "test prompt", "provider": "claude", "dry_run": True},
        headers={**_XHR, "Authorization": f"Bearer {auth_env['bot']}"},
    )
    assert resp.status_code == 200
    assert resp.json()["dry_run"] is True


@pytest.mark.integration
def test_cancel_rejects_token_missing_staff_cancel(remote_client: TestClient, auth_env: dict[str, str]) -> None:
    resp = remote_client.post(
        "/api/staff/runs/run-nonexistent/cancel",
        headers={**_XHR, "Authorization": f"Bearer {auth_env['viewer']}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["required_scope"] == "staff.cancel"


@pytest.mark.integration
def test_cancel_allows_token_with_staff_cancel(remote_client: TestClient, auth_env: dict[str, str]) -> None:
    # Operator token has staff.cancel
    resp = remote_client.post(
        "/api/staff/runs/run-nonexistent/cancel",
        headers={**_XHR, "Authorization": f"Bearer {auth_env['operator']}"},
    )
    # 404 because run does not exist, but auth/scope check passed (not 401 or 403)
    assert resp.status_code == 404


@pytest.mark.integration
def test_holds_write_rejects_token_missing_staff_holds_write(
    remote_client: TestClient, auth_env: dict[str, str]
) -> None:
    # Bot token lacks staff.holds.write
    resp = remote_client.put(
        "/api/staff/holds",
        json={"holds": []},
        headers={**_XHR, "Authorization": f"Bearer {auth_env['bot']}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["required_scope"] == "staff.holds.write"


@pytest.mark.integration
def test_holds_write_allows_token_with_staff_holds_write(remote_client: TestClient, auth_env: dict[str, str]) -> None:
    # Operator token has staff.holds.write
    resp = remote_client.put(
        "/api/staff/holds",
        json={"holds": []},
        headers={**_XHR, "Authorization": f"Bearer {auth_env['operator']}"},
    )
    assert resp.status_code == 200


@pytest.mark.integration
def test_fleet_peer_token_admitted_for_dispatch_and_cancel_and_read(
    remote_client: TestClient, auth_env: dict[str, str]
) -> None:
    fleet_auth = {"Authorization": f"Bearer {auth_env['fleet']}"}

    # Dispatch: fleet-peer has staff.dispatch
    d_resp = remote_client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "fleet dispatch", "provider": "claude", "dry_run": True},
        headers={**_XHR, **fleet_auth},
    )
    assert d_resp.status_code == 200

    # Read: fleet-peer has staff.read
    r_resp = remote_client.get("/api/staff/roster", headers=fleet_auth)
    assert r_resp.status_code == 200

    # Cancel: fleet-peer has staff.cancel
    c_resp = remote_client.post(
        "/api/staff/runs/run-dummy/cancel",
        headers={**_XHR, **fleet_auth},
    )
    assert c_resp.status_code == 404  # passed auth, 404 missing run

    # Holds write: fleet-peer lacks staff.holds.write -> 403
    h_resp = remote_client.put(
        "/api/staff/holds",
        json={"holds": []},
        headers={**_XHR, **fleet_auth},
    )
    assert h_resp.status_code == 403
    assert h_resp.json()["detail"]["required_scope"] == "staff.holds.write"


@pytest.mark.integration
def test_loopback_auth_scope_enforcement(monkeypatch: pytest.MonkeyPatch, auth_env: dict[str, str]) -> None:
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "1")
    from server import app

    loopback_client = TestClient(app, raise_server_exceptions=False, client=("127.0.0.1", 53000))

    # Loopback has staff.dispatch
    d_resp = loopback_client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "local dev", "provider": "claude", "dry_run": True},
        headers=_XHR,
    )
    assert d_resp.status_code == 200

    # Loopback has staff.read
    r_resp = loopback_client.get("/api/staff/roster")
    assert r_resp.status_code == 200

    # Loopback lacks staff.admin -> 403
    a_resp = loopback_client.post("/api/staff/usage/export", headers=_XHR)
    assert a_resp.status_code == 403
    assert a_resp.json()["detail"]["required_scope"] == "staff.admin"
