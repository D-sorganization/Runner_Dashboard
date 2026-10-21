"""Agent-grok (Barb) fleet read scopes and agent principal defaults."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import identity
import pytest
from fastapi.testclient import TestClient
from identity import Principal, TokenRecord, identity_manager

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.15", 52000)
_HUB = "hub-fleet-secret"  # pragma: allowlist secret


@pytest.fixture
def grok_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    id_dir = tmp_path / "identity"
    id_dir.mkdir(parents=True)
    tok_dir = id_dir / "agent-tokens"
    tok_dir.mkdir()
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(id_dir))
    monkeypatch.setenv("HUB_FLEET_TOKEN", _HUB)
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "roles"))
    monkeypatch.setenv("STAFF_DIRECTIVES_FILE", str(tmp_path / "directives.json"))
    monkeypatch.setenv("STAFF_RM_ROOT", str(tmp_path / "rm"))
    (tmp_path / "rm" / "config").mkdir(parents=True)
    (tmp_path / "rm" / "config" / "fleet_manifest.yaml").write_text(
        "portfolios:\n  infra:\n    description: x\n    wip_limit: 1\n    repos: []\n",
        encoding="utf-8",
    )

    raw_grok = "svc_grok_test_token_for_ci"  # pragma: allowlist secret
    (tok_dir / "agent-grok.token").write_text(raw_grok + "\n", encoding="utf-8")

    identity_manager.config_dir = id_dir
    identity_manager.principals_path = id_dir / "principals.yml"
    identity_manager.tokens_path = id_dir / "tokens.yml"
    identity_manager.principals = {}
    identity_manager.tokens = []
    identity_manager.load_principals()
    identity_manager.load_tokens()
    from agent_principal_defaults import merge_agent_principal_defaults  # noqa: PLC0415

    merge_agent_principal_defaults(identity_manager)

    raw_bot = "svc_plain_bot_no_fleet_read"  # pragma: allowlist secret
    identity_manager.principals["agent-codex"] = Principal(
        id="agent-codex", type="bot", name="Codex", roles=["bot"]
    )
    identity_manager.tokens.append(
        TokenRecord(
            token_hash=hashlib.sha256(raw_bot.encode()).hexdigest(),
            principal_id="agent-codex",
            created_at=0.0,
            name="codex",
        )
    )

    from staff import runner as runner_mod  # noqa: PLC0415
    from staff import store as store_mod  # noqa: PLC0415

    store_mod.reset_store()
    runner_mod.reset_runner()

    return {"grok": raw_grok, "codex": raw_bot}


@pytest.fixture
def remote_client(grok_env: dict[str, str]) -> Iterator[TestClient]:
    from server import app  # noqa: PLC0415

    yield TestClient(app, raise_server_exceptions=False, client=_REMOTE)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.integration
def test_grok_ingested_with_fleet_read_and_reads_priorities(
    remote_client: TestClient, grok_env: dict[str, str]
) -> None:
    prin = identity_manager.verify_token(grok_env["grok"])
    assert prin is not None and prin.id == "agent-grok"
    assert identity.principal_has_scope(prin, "fleet.read")
    assert identity.principal_has_scope(prin, "staff.dispatch")
    assert not identity.principal_has_scope(prin, "priorities.write")
    assert not identity.principal_has_scope(prin, "staff.holds.write")

    resp = remote_client.get("/api/priorities", headers=_auth(grok_env["grok"]))
    assert resp.status_code == 200, resp.text

    fleet = remote_client.get("/api/fleet/status", headers=_auth(grok_env["grok"]))
    assert fleet.status_code not in (401, 403), fleet.text

    coord = remote_client.get("/api/coordination/briefing", headers=_auth(grok_env["grok"]))
    assert coord.status_code == 200, coord.text


@pytest.mark.integration
def test_codex_bot_without_fleet_read_denied_priorities(remote_client: TestClient, grok_env: dict[str, str]) -> None:
    resp = remote_client.get("/api/priorities", headers=_auth(grok_env["codex"]))
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["required_scope"] == "fleet.read"
    assert detail["principal"] == "agent-codex"


@pytest.mark.integration
def test_grok_cannot_write_directives_or_holds(remote_client: TestClient, grok_env: dict[str, str]) -> None:
    headers = {**_XHR, **_auth(grok_env["grok"])}
    directives = remote_client.put(
        "/api/priorities/directives",
        json={"directives": [{"text": "nope", "priority": 1}]},
        headers=headers,
    )
    assert directives.status_code == 403
    assert directives.json()["detail"]["required_scope"] == "priorities.write"

    holds = remote_client.put("/api/staff/holds", json={"holds": []}, headers=headers)
    assert holds.status_code == 403
    assert holds.json()["detail"]["required_scope"] == "staff.holds.write"


@pytest.mark.integration
def test_grok_staff_summary_and_dispatch_dry_run(remote_client: TestClient, grok_env: dict[str, str]) -> None:
    summary = remote_client.get("/api/staff/summary", headers=_auth(grok_env["grok"]))
    assert summary.status_code == 200

    dispatch = remote_client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "dry", "provider": "claude", "dry_run": True},
        headers={**_XHR, **_auth(grok_env["grok"])},
    )
    assert dispatch.status_code == 200
    assert dispatch.json()["dry_run"] is True
