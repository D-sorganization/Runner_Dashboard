"""Loopback registration for desktop agent-bus sessions (#2982)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import identity_manager, principal_has_scope

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.9", 51000)


@pytest.fixture
def loopback_bus_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    id_dir = tmp_path / "identity"
    id_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(id_dir))
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "roles"))
    monkeypatch.delenv("HUB_FLEET_TOKEN", raising=False)
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "1")

    from agent_bus.store import reset_store as reset_agent_bus_store
    from staff import store as store_mod

    store_mod.reset_store()
    reset_agent_bus_store()
    identity_manager.config_dir = id_dir
    identity_manager.principals_path = id_dir / "principals.yml"
    identity_manager.tokens_path = id_dir / "tokens.yml"
    identity_manager._ephemeral_principals.clear()
    identity_manager._ephemeral_tokens.clear()
    identity_manager.principals = {}
    identity_manager.tokens = []
    identity_manager.save_principals()
    identity_manager.save_tokens()
    yield


_LOOPBACK = ("127.0.0.1", 50000)


@pytest.fixture
def loopback_client(loopback_bus_env: None) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=_LOOPBACK)


@pytest.fixture
def remote_client(loopback_bus_env: None) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=_REMOTE)


@pytest.mark.unit
def test_loopback_register_mints_session_token(loopback_client: TestClient) -> None:
    sess = "codex-hub-a-20261027-a1b2c3"
    reg = loopback_client.post(
        "/api/v1/agent/register",
        headers=_XHR,
        json={
            "session": sess,
            "role": "codex",
            "provider": "codex",
            "node": "hub-a",
            "lease_seconds": 120,
        },
    )
    assert reg.status_code == 200, reg.text
    body = reg.json()
    assert body["session"] == sess
    token = body.get("token")
    assert isinstance(token, str) and token
    assert principal_has_scope(identity_manager.verify_token(token), "coordination.write")

    hb = loopback_client.post(
        "/api/v1/agent/heartbeat",
        headers={**_XHR, "Authorization": f"Bearer {token}"},
        json={"session": sess, "lease_seconds": 120},
    )
    assert hb.status_code == 200


@pytest.mark.unit
def test_remote_register_without_credentials_is_rejected(remote_client: TestClient) -> None:
    reg = remote_client.post(
        "/api/v1/agent/register",
        headers=_XHR,
        json={
            "session": "codex-remote-20261027-xyz",
            "role": "codex",
            "provider": "codex",
            "node": "Hub",
            "lease_seconds": 120,
        },
    )
    assert reg.status_code == 401


@pytest.mark.unit
def test_loopback_register_disabled_without_flag(loopback_bus_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    from server import app  # noqa: PLC0415

    client = TestClient(app, raise_server_exceptions=False, client=_LOOPBACK)
    reg = client.post(
        "/api/v1/agent/register",
        headers=_XHR,
        json={
            "session": "claude-local-20261027-abc",
            "role": "claude",
            "provider": "claude",
            "node": "node-c",
            "lease_seconds": 120,
        },
    )
    assert reg.status_code == 401
