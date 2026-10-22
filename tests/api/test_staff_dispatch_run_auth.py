"""Bearer auth and model defaults for POST /api/staff/{role}/run (#2053)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_LOOPBACK = ("127.0.0.1", 53000)
_REMOTE = ("100.64.0.9", 51000)


@pytest.fixture
def dispatch_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    import hashlib
    import time

    from identity import Principal, TokenRecord, identity_manager
    from staff import runner as runner_mod
    from staff import store as store_mod

    id_dir = tmp_path / "identity"
    id_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(id_dir))
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-secret-token")
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "1")
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "roles"))

    store_mod.reset_store()
    runner_mod.reset_runner()

    identity_manager.config_dir = id_dir
    identity_manager.principals_path = id_dir / "principals.yml"
    identity_manager.tokens_path = id_dir / "tokens.yml"
    identity_manager.principals = {
        "operator-user": Principal(id="operator-user", type="human", name="Operator", roles=["operator"]),
    }
    token_hash = hashlib.sha256(b"operator-token").hexdigest()
    identity_manager.tokens = [
        TokenRecord(
            token_hash=token_hash,
            principal_id="operator-user",
            name="operator-token",
            created_at=time.time(),
            expires_at=time.time() + 86400,
        )
    ]
    identity_manager.save_principals()
    identity_manager.save_tokens()

    yield {"operator": "operator-token", "fleet": "fleet-secret-token"}

    store_mod.reset_store()
    runner_mod.reset_runner()


@pytest.fixture
def loopback_client(dispatch_env: dict[str, str]) -> Iterator[TestClient]:
    from server import app

    yield TestClient(app, raise_server_exceptions=False, client=_LOOPBACK)
    app.dependency_overrides.clear()


@pytest.fixture
def remote_client(dispatch_env: dict[str, str]) -> Iterator[TestClient]:
    from server import app

    yield TestClient(app, raise_server_exceptions=False, client=_REMOTE)
    app.dependency_overrides.clear()


@pytest.mark.unit
def test_loopback_dispatch_without_bearer_is_rejected(loopback_client: TestClient) -> None:
    resp = loopback_client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "supervised dry-run", "provider": "codex", "dry_run": True},
        headers=_XHR,
    )
    assert resp.status_code == 401


@pytest.mark.unit
def test_loopback_dispatch_with_bad_bearer_is_rejected(loopback_client: TestClient) -> None:
    resp = loopback_client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "x", "provider": "codex", "dry_run": True},
        headers={**_XHR, "Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code in (401, 403)


@pytest.mark.unit
def test_operator_token_allows_dry_run_and_sets_codex_model(
    loopback_client: TestClient, dispatch_env: dict[str, str], staff_cli_installed
) -> None:
    staff_cli_installed("codex")
    resp = loopback_client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "supervised dry-run", "provider": "codex", "dry_run": True},
        headers={**_XHR, "Authorization": f"Bearer {dispatch_env['operator']}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is True
    plan = body["plan"]
    assert plan["model"] == "gpt-6-luna"
    argv = plan["argv"]
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "gpt-6-luna"


@pytest.mark.unit
def test_fleet_token_allows_remote_dry_run(
    remote_client: TestClient, dispatch_env: dict[str, str], staff_cli_installed
) -> None:
    staff_cli_installed("antigravity")
    resp = remote_client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "preview", "provider": "antigravity", "dry_run": True},
        headers={**_XHR, "Authorization": f"Bearer {dispatch_env['fleet']}"},
    )
    assert resp.status_code == 200, resp.text
    plan = resp.json()["plan"]
    assert plan["model"] == "gemini-3.8-flash-high"
    argv = plan["argv"]
    assert argv[argv.index("--model") + 1] == "gemini-3.8-flash-high"
