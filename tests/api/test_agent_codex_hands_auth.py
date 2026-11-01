"""Integration: agent-codex-hands bearer auth boundaries."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import identity_manager

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.20", 52001)


@pytest.fixture
def hands_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    id_dir = tmp_path / "identity"
    id_dir.mkdir(parents=True)
    tok_dir = id_dir / "agent-tokens"
    tok_dir.mkdir()
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(id_dir))
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

    raw = "svc_codex_hands_integration"  # pragma: allowlist secret
    (tok_dir / "agent-codex-hands.token").write_text(raw + "\n", encoding="utf-8")

    identity_manager.config_dir = id_dir
    identity_manager.principals_path = id_dir / "principals.yml"
    identity_manager.tokens_path = id_dir / "tokens.yml"
    identity_manager.principals = {}
    identity_manager.tokens = []
    identity_manager.load_principals()
    identity_manager.load_tokens()
    from agent_principal_defaults import merge_agent_principal_defaults  # noqa: PLC0415

    merge_agent_principal_defaults(identity_manager)

    from staff import runner as runner_mod  # noqa: PLC0415
    from staff import store as store_mod  # noqa: PLC0415

    store_mod.reset_store()
    runner_mod.reset_runner()

    return {"token": raw}


@pytest.fixture
def remote_client(hands_env: dict[str, str]) -> Iterator[TestClient]:
    from server import app  # noqa: PLC0415

    yield TestClient(app, raise_server_exceptions=False, client=_REMOTE)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.integration
def test_codex_hands_reads_priorities_and_writes_as_barb_peer(
    remote_client: TestClient, hands_env: dict[str, str]
) -> None:
    """Dots is a Barb peer (#3648, owner decision #3647): writes allowed, never admin (#3687)."""
    from identity import principal_has_scope  # noqa: PLC0415

    prin = identity_manager.verify_token(hands_env["token"])
    assert prin is not None and prin.id == "agent-codex-hands"
    assert principal_has_scope(prin, "priorities.write")
    assert principal_has_scope(prin, "staff.holds.write")
    assert not principal_has_scope(prin, "staff.admin")
    assert "admin" not in prin.roles

    assert remote_client.get("/api/priorities", headers=_auth(hands_env["token"])).status_code == 200

    headers = {**_XHR, **_auth(hands_env["token"])}
    directives = remote_client.put(
        "/api/priorities/directives",
        json={"directives": [{"text": "peer directive", "priority": 1}]},
        headers=headers,
    )
    assert directives.status_code == 200, directives.text

    holds = remote_client.put("/api/staff/holds", json={"holds": []}, headers=headers)
    assert holds.status_code == 200, holds.text


@pytest.mark.integration
def test_codex_hands_staff_dispatch_dry_run(
    remote_client: TestClient, hands_env: dict[str, str], staff_cli_installed
) -> None:
    staff_cli_installed("claude")
    dispatch = remote_client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "dry", "provider": "claude", "dry_run": True},
        headers={**_XHR, **_auth(hands_env["token"])},
    )
    assert dispatch.status_code == 200
    assert dispatch.json()["dry_run"] is True
