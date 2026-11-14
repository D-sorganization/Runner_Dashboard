"""Security regressions for hub run-token and run-jwt minting (RM#2855)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from identity import Principal, TokenRecord, identity_manager, principal_has_scope
from server import app
from staff.github_run_env import StaffGithubToken
from staff.roles import RoleSpec
from staff.run_jwt_service import RunJwtCredential
from staff.store import RunRecord

_FLEET_SECRET = "fleet-hub-secret-scope-test"  # pragma: allowlist secret
_XHR = {"X-Requested-With": "XMLHttpRequest"}
_FAKE_GHS = "ghs_" + "S" * 40 + "_scoped"


def _role(repos: tuple[str, ...] = ("Runner_Dashboard",)) -> RoleSpec:
    return RoleSpec(
        name="maintenance",
        title="Maintenance",
        providers=("dummy",),
        permissions={"lease": False, "push_branch": True, "open_pr": False, "merge": False},
        repos=repos,
    )


@pytest.fixture
def hub_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    monkeypatch.setenv("HUB_FLEET_TOKEN", _FLEET_SECRET)
    monkeypatch.setenv("MACHINE_ROLE", "hub")
    monkeypatch.setenv("RUN_JWT_SIGNING_KEY", "x" * 32)
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(tmp_path / "identity"))
    import dashboard_config

    dashboard_config.MACHINE_ROLE = "hub"

    id_dir = tmp_path / "identity"
    id_dir.mkdir()
    identity_manager.config_dir = id_dir
    identity_manager.principals_path = id_dir / "principals.yml"
    identity_manager.tokens_path = id_dir / "tokens.yml"
    identity_manager.principals = {
        "viewer-user": Principal(id="viewer-user", type="human", name="Viewer", roles=["viewer"]),
        "agent-claude": Principal(id="agent-claude", type="bot", name="Claude", roles=["bot"]),
        "agent-codex": Principal(id="agent-codex", type="bot", name="Codex", roles=["bot"]),
        "admin-user": Principal(id="admin-user", type="human", name="Admin", roles=["admin"]),
    }
    for pid, raw in (
        ("viewer-user", "viewer-raw"),
        ("agent-claude", "claude-shared-raw"),
        ("agent-codex", "codex-shared-raw"),
        ("admin-user", "admin-raw"),
    ):
        identity_manager.tokens.append(
            TokenRecord(
                token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                principal_id=pid,
                created_at=0.0,
                name=pid,
            )
        )
    identity_manager.save_principals()
    identity_manager.save_tokens()

    from staff import runner as runner_mod
    from staff import store as store_mod
    from staff.runner import get_runner

    store_mod.reset_store()
    runner_mod.reset_runner()
    runner = get_runner()
    runner._roles_loader = lambda: {"maintenance": _role()}
    store = runner.store

    rec = RunRecord(
        id="run-scope-1",
        role="maintenance",
        provider="claude",
        model="default",
        machine="local",
        repo="Runner_Dashboard",
        target_kind="prompt",
        target_ref="",
        prompt="scope test",
        status="running",
        lease_id="claude-sess-1",
    )
    store.create_run(rec)

    return {"client": TestClient(app, raise_server_exceptions=False), "store": store}


def test_viewer_cannot_mint_run_jwt(hub_env: dict[str, Any]) -> None:
    resp = hub_env["client"].post(
        "/api/fleet/run-jwt",
        json={"run_id": "run-scope-1"},
        headers={"Authorization": "Bearer viewer-raw", "X-Fleet-Node": "node-c", **_XHR},
    )
    assert resp.status_code == 403


def test_viewer_cannot_mint_run_token(hub_env: dict[str, Any]) -> None:
    with patch("staff.fleet_run_token_service.assert_registered_fleet_node", return_value="node-c"):
        resp = hub_env["client"].post(
            "/api/fleet/run-token",
            json={"run_id": "run-scope-1"},
            headers={"Authorization": "Bearer viewer-raw", "X-Fleet-Node": "node-c", **_XHR},
        )
    assert resp.status_code == 403


def test_shared_token_cannot_mint_jwt_for_another_lease_principal(hub_env: dict[str, Any]) -> None:
    """agent-codex static token cannot mint for a run whose lease agent is claude."""
    with patch("staff.run_jwt_service.mint_run_jwt") as mint:
        resp = hub_env["client"].post(
            "/api/fleet/run-jwt",
            json={"run_id": "run-scope-1"},
            headers={"Authorization": "Bearer codex-shared-raw", "X-Fleet-Node": "node-c", **_XHR},
        )
    assert resp.status_code == 403
    mint.assert_not_called()


def test_fleet_peer_minted_jwt_uses_lease_principal_not_body(hub_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_mint(_mgr, *, run_id, principal_id, caller_node):  # noqa: ANN001
        calls.append(principal_id)
        return RunJwtCredential(token="a.b.c", expires_at=9.0, jti="j1")

    monkeypatch.setattr("routers.run_jwt.mint_run_jwt", fake_mint)
    with patch("staff.fleet_run_token_service.assert_registered_fleet_node", return_value="node-c"):
        resp = hub_env["client"].post(
            "/api/fleet/run-jwt",
            json={"run_id": "run-scope-1"},
            headers={"Authorization": f"Bearer {_FLEET_SECRET}", "X-Fleet-Node": "node-c", **_XHR},
        )
    assert resp.status_code == 200
    assert calls == ["agent-claude"]


def test_admin_lease_principal_is_not_mintable(hub_env: dict[str, Any]) -> None:
    identity_manager.principals["agent-claude"] = Principal(
        id="agent-claude", type="bot", name="Claude", roles=["admin"]
    )
    identity_manager.save_principals()
    with patch("staff.fleet_run_token_service.assert_registered_fleet_node", return_value="node-c"):
        resp = hub_env["client"].post(
            "/api/fleet/run-jwt",
            json={"run_id": "run-scope-1"},
            headers={"Authorization": f"Bearer {_FLEET_SECRET}", "X-Fleet-Node": "node-c", **_XHR},
        )
    assert resp.status_code == 403


def test_repo_outside_role_list_is_403(hub_env: dict[str, Any]) -> None:
    hub_env["store"].update_run("run-scope-1", repo="Other_Repo")
    with patch("staff.fleet_run_token_service.assert_registered_fleet_node", return_value="node-c"):
        with patch("staff.fleet_run_mint_policy.load_roles", return_value={"maintenance": _role(repos=("Runner_Dashboard",))}):
            with patch("staff.fleet_run_token_service.load_roles", return_value={"maintenance": _role(repos=("Runner_Dashboard",))}):
                resp = hub_env["client"].post(
                    "/api/fleet/run-token",
                    json={"run_id": "run-scope-1"},
                    headers={"Authorization": f"Bearer {_FLEET_SECRET}", "X-Fleet-Node": "node-c", **_XHR},
                )
    assert resp.status_code == 403


def test_unknown_run_id_is_403(hub_env: dict[str, Any]) -> None:
    with patch("staff.fleet_run_token_service.assert_registered_fleet_node", return_value="node-c"):
        resp = hub_env["client"].post(
            "/api/fleet/run-jwt",
            json={"run_id": "run-missing"},
            headers={"Authorization": f"Bearer {_FLEET_SECRET}", "X-Fleet-Node": "node-c", **_XHR},
        )
    assert resp.status_code == 403


def test_expired_run_id_is_403(hub_env: dict[str, Any]) -> None:
    hub_env["store"].update_run("run-scope-1", status="succeeded")
    with patch("staff.fleet_run_token_service.assert_registered_fleet_node", return_value="node-c"):
        resp = hub_env["client"].post(
            "/api/fleet/run-token",
            json={"run_id": "run-scope-1"},
            headers={"Authorization": f"Bearer {_FLEET_SECRET}", "X-Fleet-Node": "node-c", **_XHR},
        )
    assert resp.status_code == 403


def test_fleet_peer_run_token_mints_with_derived_role_and_repo(hub_env: dict[str, Any]) -> None:
    fake = StaffGithubToken(token=_FAKE_GHS, expires_at=1_800_000_000.0, source="github_app")
    with patch("staff.fleet_run_token_service.assert_registered_fleet_node", return_value="node-c"):
        with patch("staff.fleet_run_mint_policy.load_roles", return_value={"maintenance": _role()}):
            with patch("staff.fleet_run_token_service.load_roles", return_value={"maintenance": _role()}):
                with patch("staff.fleet_run_token_service.mint_staff_run_github_token_local", return_value=fake) as mint:
                    resp = hub_env["client"].post(
                        "/api/fleet/run-token",
                        json={"run_id": "run-scope-1"},
                        headers={"Authorization": f"Bearer {_FLEET_SECRET}", "X-Fleet-Node": "node-c", **_XHR},
                    )
    assert resp.status_code == 200
    mint.assert_called_once()
    assert mint.call_args.args[0].name == "maintenance"


def test_fleet_peer_has_mint_scope() -> None:
    peer = Principal(id="fleet-peer", type="bot", name="Fleet Peer", roles=["fleet-peer"])
    assert principal_has_scope(peer, "fleet.run_credential.mint")
