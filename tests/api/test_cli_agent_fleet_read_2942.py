"""CLI agent principals (codex/claude/gemini) need fleet.read for coordination GETs (#2942)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import identity
import pytest
from agent_principal_defaults import merge_agent_principal_defaults
from coordination_fake_rm import FakeRM, board, install, session
from fastapi.testclient import TestClient
from identity import IdentityManager, principal_has_scope

_CLI_AGENTS = ("agent-codex", "agent-claude", "agent-gemini")
_REPO_DEFAULTS = Path(__file__).resolve().parents[2] / "config" / "agent_principals.yml"
_CODEX_RAW = "svc_agent_codex_coord_read_2942"  # pragma: allowlist secret
_REMOTE = ("100.64.0.20", 53000)
_HUB = "hub-fleet-token-2942"  # pragma: allowlist secret
_XHR = {"X-Requested-With": "XMLHttpRequest"}


def _provision_cli_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent_id: str, raw_token: str
) -> IdentityManager:
    """Precondition: repo defaults file exists and agent token file is non-empty."""
    assert _REPO_DEFAULTS.is_file()
    assert raw_token.strip()

    id_dir = tmp_path / "identity"
    id_dir.mkdir(parents=True)
    tok_dir = id_dir / "agent-tokens"
    tok_dir.mkdir()
    (tok_dir / f"{agent_id}.token").write_text(raw_token + "\n", encoding="utf-8")

    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(id_dir))
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(_REPO_DEFAULTS))
    monkeypatch.setenv("HUB_FLEET_TOKEN", _HUB)
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")

    mgr = IdentityManager(config_dir=id_dir)
    merge_agent_principal_defaults(mgr)
    identity.identity_manager.config_dir = id_dir
    identity.identity_manager.principals_path = id_dir / "principals.yml"
    identity.identity_manager.tokens_path = id_dir / "tokens.yml"
    identity.identity_manager.principals = dict(mgr.principals)
    identity.identity_manager.tokens = list(mgr.tokens)
    return mgr


@pytest.mark.parametrize("agent_id", _CLI_AGENTS)
def test_repo_defaults_grant_fleet_read_not_fleet_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent_id: str
) -> None:
    raw = f"svc_{agent_id.replace('-', '_')}_2942_test"  # pragma: allowlist secret
    mgr = _provision_cli_agent(tmp_path, monkeypatch, agent_id, raw)
    prin = mgr.verify_token(raw)
    assert prin is not None and prin.id == agent_id
    assert principal_has_scope(prin, "fleet.read")
    assert not principal_has_scope(prin, "fleet.control")
    assert not principal_has_scope(prin, "fleet.maintain")


@pytest.fixture
def codex_rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    mgr = _provision_cli_agent(tmp_path, monkeypatch, "agent-codex", _CODEX_RAW)
    prin = mgr.verify_token(_CODEX_RAW)
    assert prin is not None

    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "roles"))
    monkeypatch.setenv("STAFF_DIRECTIVES_FILE", str(tmp_path / "directives.json"))
    yield from install(tmp_path, monkeypatch, {_CODEX_RAW: prin}, repos="Tools")


@pytest.fixture
def codex_coord_client(codex_rm: FakeRM) -> tuple[TestClient, str]:
    codex_rm.respond(
        "agent_communicate:list",
        stdout=board(session("s1", "Tools"), complete=True),
    )
    from server import app  # noqa: PLC0415

    client = TestClient(app, raise_server_exceptions=False, client=_REMOTE)
    return client, _CODEX_RAW


@pytest.mark.integration
def test_agent_codex_reads_coordination_sessions(codex_coord_client: tuple[TestClient, str]) -> None:
    client, token = codex_coord_client
    resp = client.get("/api/coordination/sessions?repo=Tools", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["sessions"]


@pytest.mark.integration
def test_agent_codex_cannot_write_fleet_control_directives(codex_coord_client: tuple[TestClient, str]) -> None:
    client, token = codex_coord_client
    headers = {**_XHR, "Authorization": f"Bearer {token}"}
    resp = client.put(
        "/api/priorities/directives",
        json={"directives": [{"text": "nope", "priority": 1}]},
        headers=headers,
    )
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["required_scope"] == "priorities.write"


@pytest.mark.integration
def test_unknown_bearer_still_rejected_for_coordination_read(
    codex_rm: FakeRM, monkeypatch: pytest.MonkeyPatch
) -> None:
    del codex_rm  # fixture drives FakeRM lifecycle only
    from server import app  # noqa: PLC0415

    client = TestClient(app, raise_server_exceptions=False, client=_REMOTE)
    monkeypatch.setenv("HUB_FLEET_TOKEN", _HUB)
    resp = client.get(
        "/api/coordination/sessions",
        headers={"Authorization": "Bearer svc_totally_unknown_principal"},
    )
    assert resp.status_code == 401


def test_bare_bot_principal_without_fleet_read_still_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: explicit scopes are required; bot role preset alone is not fleet.read (#2057)."""
    monkeypatch.setenv("HUB_FLEET_TOKEN", _HUB)
    prin = identity.Principal(id="agent-codex", type="bot", name="Codex", roles=["bot"])
    monkeypatch.setattr(identity.identity_manager, "verify_token", lambda raw: prin if raw == "svc-tok" else None)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        identity.require_fleet_peer(
            type("R", (), {"session": {}, "method": "GET", "headers": {}})(),
            header_token="Bearer svc-tok",
        )
    assert exc.value.status_code == 403
    assert exc.value.detail["required_scope"] == "fleet.read"
