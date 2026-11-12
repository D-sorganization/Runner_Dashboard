"""Hub-mediated GitHub installation tokens for staff runs on fleet nodes (#2051)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from server import app
from staff.github_run_env import StaffGithubToken, mint_staff_run_github_token
from staff.roles import RoleSpec
from staff.runner import RunRecord, RunRequest, StaffRunner
from staff.store import RunStore

_FAKE_GHS = "ghs_" + "H" * 40 + "_hubmint"
_FAKE_GHP = "ghp_" + "Y" * 36
_XHR = {"X-Requested-With": "XMLHttpRequest"}
_FLEET_SECRET = "fleet-hub-secret-for-tests"  # pragma: allowlist secret


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _role(repos: tuple[str, ...] = ("Runner_Dashboard",)) -> RoleSpec:
    return RoleSpec(
        name="maintenance",
        title="Maintenance",
        providers=("dummy",),
        permissions={"lease": False, "push_branch": True, "open_pr": False, "merge": False},
        repos=repos,
    )


def _hub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", _FLEET_SECRET)
    monkeypatch.setenv("MACHINE_ROLE", "hub")
    import dashboard_config

    dashboard_config.MACHINE_ROLE = "hub"


def _node_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", _FLEET_SECRET)
    monkeypatch.setenv("MACHINE_ROLE", "node")
    monkeypatch.setenv("HUB_URL", "http://hub-a:8321")
    import dashboard_config

    dashboard_config.MACHINE_ROLE = "node"
    dashboard_config.HUB_URL = "http://hub-a:8321"


def test_fleet_run_token_requires_fleet_bearer(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _hub_env(monkeypatch)  # noqa: ARG001 — fixture side effect
    resp = client.post(
        "/api/fleet/run-token",
        json={"run_id": "run-1", "role": "maintenance", "repositories": ["Runner_Dashboard"]},
        headers=_XHR,
    )
    assert resp.status_code == 401


def test_fleet_run_token_rejects_invalid_bearer(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _hub_env(monkeypatch)
    resp = client.post(
        "/api/fleet/run-token",
        json={"run_id": "run-1", "role": "maintenance", "repositories": ["Runner_Dashboard"]},
        headers={"Authorization": "Bearer wrong", **_XHR},
    )
    assert resp.status_code == 401


def test_fleet_run_token_rejects_unregistered_node_header(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _hub_env(monkeypatch)
    with patch(
        "staff.fleet_run_token_service.assert_registered_fleet_node",
        side_effect=__import__("fastapi").HTTPException(status_code=403, detail="unregistered fleet node"),
    ):
        resp = client.post(
            "/api/fleet/run-token",
            json={"run_id": "run-1", "role": "maintenance", "repositories": ["Runner_Dashboard"]},
            headers={
                "Authorization": f"Bearer {_FLEET_SECRET}",
                "X-Fleet-Node": "unknown-node",
                **_XHR,
            },
        )
    assert resp.status_code == 403


def test_fleet_run_token_rejects_repo_outside_role_scope(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _hub_env(monkeypatch)
    role = _role(repos=("Runner_Dashboard",))
    with patch("staff.fleet_run_token_service.load_roles", return_value={"maintenance": role}):
        with patch("staff.fleet_run_token_service.assert_registered_fleet_node", return_value="node-c"):
            resp = client.post(
                "/api/fleet/run-token",
                json={"run_id": "run-1", "role": "maintenance", "repositories": ["Other_Repo"]},
                headers={
                    "Authorization": f"Bearer {_FLEET_SECRET}",
                    "X-Fleet-Node": "node-c",
                    **_XHR,
                },
            )
    assert resp.status_code == 403


def test_fleet_run_token_mints_on_hub(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _hub_env(monkeypatch)
    fake = StaffGithubToken(token=_FAKE_GHS, expires_at=1_800_000_000.0, source="github_app")
    role = _role()
    with patch("staff.fleet_run_token_service.load_roles", return_value={"maintenance": role}):
        with patch("staff.fleet_run_token_service.assert_registered_fleet_node", return_value="node-c"):
            with patch("staff.fleet_run_token_service.mint_staff_run_github_token_local", return_value=fake) as mint:
                resp = client.post(
                    "/api/fleet/run-token",
                    json={"run_id": "run-node-c-1", "role": "maintenance", "repositories": ["Runner_Dashboard"]},
                    headers={
                        "Authorization": f"Bearer {_FLEET_SECRET}",
                        "X-Fleet-Node": "node-c",
                        **_XHR,
                    },
                )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"] == _FAKE_GHS
    assert body["expires_at"] == fake.expires_at
    mint.assert_called_once()
    assert mint.call_args.args[1] == "D-sorganization/Runner_Dashboard"


def test_fleet_run_token_scopes_installation_token_to_bare_repo_name(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hub_env(monkeypatch)
    from github_app_auth import InstallationToken

    role = _role()
    with patch("staff.fleet_run_token_service.load_roles", return_value={"maintenance": role}):
        with patch("staff.fleet_run_token_service.assert_registered_fleet_node", return_value="node-c"):
            with patch("staff.github_run_env.github_app_auth_configured", return_value=True):
                with patch("staff.github_run_env.mint_installation_access_token") as mint:
                    mint.return_value = InstallationToken(token=_FAKE_GHS, expires_at=1_800_000_000.0)
                    resp = client.post(
                        "/api/fleet/run-token",
                        json={"run_id": "run-node-c-scope", "role": "maintenance", "repositories": ["Runner_Dashboard"]},
                        headers={
                            "Authorization": f"Bearer {_FLEET_SECRET}",
                            "X-Fleet-Node": "node-c",
                            **_XHR,
                        },
                    )
    assert resp.status_code == 200
    mint.assert_called_once()
    assert mint.call_args.kwargs["repositories"] == ["Runner_Dashboard"]


def test_fleet_run_token_rejects_empty_repository_scope_without_minting(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hub_env(monkeypatch)
    role = _role()
    with patch("staff.fleet_run_token_service.load_roles", return_value={"maintenance": role}):
        with patch("staff.fleet_run_token_service.assert_registered_fleet_node", return_value="node-c"):
            with patch("staff.fleet_run_token_service.mint_staff_run_github_token_local") as mint:
                resp = client.post(
                    "/api/fleet/run-token",
                    json={"run_id": "run-node-c-empty-scope", "role": "maintenance", "repositories": []},
                    headers={
                        "Authorization": f"Bearer {_FLEET_SECRET}",
                        "X-Fleet-Node": "node-c",
                        **_XHR,
                    },
                )
    assert resp.status_code == 422
    mint.assert_not_called()


def test_mint_staff_run_github_token_hub_path_on_node(monkeypatch: pytest.MonkeyPatch) -> None:
    _node_env(monkeypatch)
    monkeypatch.delenv("STAFF_ALLOW_OPERATOR_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", _FAKE_GHP)
    fake = StaffGithubToken(token=_FAKE_GHS, expires_at=1_800_000_000.0, source="hub_github_app")
    with patch("staff.github_run_env.github_app_auth_configured", return_value=False):
        with patch("staff.fleet_run_token_client.mint_staff_run_github_token_via_hub", return_value=fake) as hub:
            tok = mint_staff_run_github_token(_role(), repo="Runner_Dashboard", run_id="run-node-1")
    hub.assert_called_once()
    assert tok.token == _FAKE_GHS
    assert tok.source == "hub_github_app"


def test_mint_staff_run_github_token_hub_path_fail_closed_when_hub_down(monkeypatch: pytest.MonkeyPatch) -> None:
    _node_env(monkeypatch)
    monkeypatch.delenv("STAFF_ALLOW_OPERATOR_TOKEN", raising=False)
    with patch("staff.github_run_env.github_app_auth_configured", return_value=False):
        with patch(
            "staff.fleet_run_token_client.mint_staff_run_github_token_via_hub",
            side_effect=RuntimeError("hub unreachable"),
        ):
            with pytest.raises(RuntimeError, match="hub"):
                mint_staff_run_github_token(_role(), repo="Runner_Dashboard", run_id="run-node-2")


def test_mint_staff_run_github_token_node_ignores_operator_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _node_env(monkeypatch)
    monkeypatch.setenv("STAFF_ALLOW_OPERATOR_TOKEN", "1")
    monkeypatch.setenv("GH_TOKEN", _FAKE_GHP)
    with patch("staff.github_run_env.github_app_auth_configured", return_value=False):
        with patch(
            "staff.fleet_run_token_client.mint_staff_run_github_token_via_hub",
            side_effect=RuntimeError("hub required"),
        ):
            with pytest.raises(RuntimeError, match="hub"):
                mint_staff_run_github_token(_role(), repo="Runner_Dashboard", run_id="run-node-3")


def test_mint_staff_run_github_token_hub_role_uses_local_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MACHINE_ROLE", "hub")
    import dashboard_config

    dashboard_config.MACHINE_ROLE = "hub"
    with patch("staff.github_run_env.github_app_auth_configured", return_value=True):
        with patch("staff.github_run_env.mint_installation_access_token") as mint:
            from github_app_auth import InstallationToken

            mint.return_value = InstallationToken(token=_FAKE_GHS, expires_at=0.0)
            with patch("staff.fleet_run_token_client.mint_staff_run_github_token_via_hub") as hub:
                tok = mint_staff_run_github_token(_role(), repo="Runner_Dashboard", run_id="run-hub-1")
    hub.assert_not_called()
    assert tok.source == "github_app"


def test_runner_auth_error_redacts_github_token(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _node_env(monkeypatch)
    store = RunStore(tmp_path / "runs.db")
    runner = StaffRunner(store=store)

    class DummyAdapter:
        executable = "dummy"
        prompt_via_stdin = False
        lease_agent = "dummy"

        def installed(self) -> bool:
            return True

        def build_command(self, prompt: str, workdir: str, model: str | None = None, **kwargs: Any) -> list[str]:
            return ["dummy"]

        def runtime_env(self) -> dict[str, str]:
            return {}

        def parse_line(self, line: str) -> dict[str, Any]:
            return {"kind": "text", "text": line}

    runner._adapters = {"dummy": DummyAdapter()}
    runner._roles_loader = lambda: {"maintenance": _role()}

    rec = RunRecord(
        id="run-redact",
        role="maintenance",
        provider="dummy",
        model="default",
        machine="local",
        repo="Runner_Dashboard",
        target_kind="prompt",
        target_ref="",
        prompt="test",
    )
    store.create_run(rec)
    plan = runner.plan(RunRequest(role="maintenance", prompt="test", provider="dummy"))
    workdir = tmp_path / "work"
    workdir.mkdir()

    leak = RuntimeError(f"mint failed with {_FAKE_GHS}")
    with patch("staff.runner_ops.prepare_staff_run_github_env", side_effect=leak):
        with patch("subprocess.Popen") as mock_popen:
            runner._execute(rec, plan, workdir, lease_note="")
            assert not mock_popen.called

    updated = store.get_run(rec.id)
    assert updated is not None
    blob = (updated.error or "") + (updated.remediation or "")
    assert _FAKE_GHS not in blob
    assert "[REDACTED_GITHUB_TOKEN]" in blob
