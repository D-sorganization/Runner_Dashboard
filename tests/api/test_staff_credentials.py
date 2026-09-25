"""Tests for short-lived, scoped staff credentials (SC-E2, Issue #1310).

Acceptance criteria:
1. A maintenance run can call an allowed action; a disallowed action returns 403;
   the token is invalid after the run ends.
2. Mint a per-run token at start: principal staff:<role>:<run_id>,
   scopes = intersection of role's fleet_actions (SC-E1) and action policy,
   TTL = run deadline; revoke at run end (and on reconcile, SC-A4).
3. Inject as FLEET_API_TOKEN environment variable; the fleet CLI/MCP in the run uses it.
4. Error handling: minting failure fails run before CLI starts (failure_class="workspace_error").
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from identity import identity_manager, principal_has_scope
from staff import adapters as adapters_mod
from staff import credential as credential_mod
from staff import runner as runner_mod
from staff import store as store_mod
from staff.reconcile import reconcile_orphaned_runs
from staff.roles import RoleSpec
from staff.store import RunRecord

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.20", 52100)


@pytest.fixture
def identity_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Isolate identity config and database for testing."""
    id_dir = tmp_path / "identity"
    id_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(id_dir))
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "roles"))

    identity_manager.config_dir = id_dir
    identity_manager.principals_path = id_dir / "principals.yml"
    identity_manager.tokens_path = id_dir / "tokens.yml"
    identity_manager.principals = {}
    identity_manager.tokens = []
    identity_manager.save_principals()
    identity_manager.save_tokens()

    store_mod.reset_store()
    runner_mod.reset_runner()

    yield id_dir

    time.sleep(0.05)
    store_mod.reset_store()
    runner_mod.reset_runner()


@pytest.fixture
def remote_client(identity_env: Path) -> Iterator[TestClient]:
    from server import app

    yield TestClient(app, raise_server_exceptions=False, client=_REMOTE)


def test_compute_staff_scopes() -> None:
    """Verify scope computation maps fleet_actions to action policy scopes."""
    # Empty fleet_actions
    assert credential_mod.compute_staff_scopes(()) == []
    assert credential_mod.compute_staff_scopes(("unknown.action",)) == []

    # Single action
    restart_scopes = credential_mod.compute_staff_scopes(("runner.restart",))
    assert restart_scopes == ["runners.control"]

    # Multi-scope action
    scale_scopes = credential_mod.compute_staff_scopes(("runner.scale",))
    assert set(scale_scopes) == {"runners.control", "fleet.control"}

    # Combination of actions
    combined = credential_mod.compute_staff_scopes(("runner.start", "queue.purge_stale", "unknown.action"))
    assert set(combined) == {"runners.control", "workflows.control", "remediation.dispatch"}


def test_mint_and_revoke_staff_credentials(identity_env: Path) -> None:
    """Verify minting creates a valid token and revoking destroys it."""
    role = RoleSpec(
        name="maintainer",
        title="Fleet Maintainer",
        fleet_actions=("runner.restart", "runner.stop"),
    )
    run_id = "run-test-mint-1"

    token, scopes = credential_mod.mint_staff_credentials(role, run_id, ttl_seconds=600)
    assert token.startswith("stf_")
    assert "runners.control" in scopes

    expected_principal_id = f"staff:maintainer:{run_id}"
    principal = identity_manager.verify_token(token)
    assert principal is not None
    assert principal.id == expected_principal_id
    assert principal.type == "bot"
    assert "runners.control" in principal.scopes
    assert principal_has_scope(principal, "runners.control")
    assert not principal_has_scope(principal, "fleet.control")

    # Revoke token
    revoked = credential_mod.revoke_staff_credentials("maintainer", run_id)
    assert revoked == 1
    assert identity_manager.verify_token(token) is None
    assert expected_principal_id not in identity_manager.principals


def test_staff_credential_ttl_expiration(identity_env: Path) -> None:
    """Verify token is rejected after TTL expires."""
    role = RoleSpec(
        name="expiring-role",
        title="Expiring Role",
        fleet_actions=("runner.start",),
    )
    run_id = "run-test-expired"

    # Mint already-expired token
    token, _ = credential_mod.mint_staff_credentials(role, run_id, ttl_seconds=-10)
    principal = identity_manager.verify_token(token)
    assert principal is None


def test_orphan_reconciliation_revokes_tokens(identity_env: Path) -> None:
    """Verify revoke_orphan_staff_credentials removes orphaned tokens."""
    role = RoleSpec(name="maint", title="Maint", fleet_actions=("runner.restart",))

    tok1, _ = credential_mod.mint_staff_credentials(role, "run-orphan-1", ttl_seconds=300)
    tok2, _ = credential_mod.mint_staff_credentials(role, "run-orphan-2", ttl_seconds=300)
    tok3, _ = credential_mod.mint_staff_credentials(role, "run-active-3", ttl_seconds=300)

    assert identity_manager.verify_token(tok1) is not None
    assert identity_manager.verify_token(tok2) is not None
    assert identity_manager.verify_token(tok3) is not None

    # Revoke orphans where run-orphan-1 and run-orphan-2 are orphaned
    revoked_count = credential_mod.revoke_orphan_staff_credentials(orphaned_run_ids=["run-orphan-1", "run-orphan-2"])
    assert revoked_count == 2

    assert identity_manager.verify_token(tok1) is None
    assert identity_manager.verify_token(tok2) is None
    assert identity_manager.verify_token(tok3) is not None

    # Clean up tok3
    credential_mod.revoke_staff_credentials("maint", "run-active-3")


def test_acceptance_criteria_allowed_disallowed_and_revocation(
    remote_client: TestClient, identity_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance criterion 1:
    A maintenance run can call an allowed action; a disallowed action returns 403;
    the token is invalid after the run ends.
    """
    role = RoleSpec(
        name="maintenance",
        title="Fleet Maintenance Role",
        fleet_actions=("runner.restart",),  # maps to runners.control
    )
    run_id = "run-ac-101"

    token, _ = credential_mod.mint_staff_credentials(role, run_id, ttl_seconds=300)
    auth_header = {"Authorization": f"Bearer {token}"}

    from unittest.mock import AsyncMock

    monkeypatch.setattr("routers.runners.fetch_org_runners", AsyncMock(return_value={"runners": []}))

    # 1. Allowed action: /api/runners/999/restart requires runners.control
    allowed_resp = remote_client.post(
        "/api/runners/999/restart",
        headers={**_XHR, **auth_header},
    )
    # The route checks scope first; runner doesn't exist -> 404, but auth passed (not 401 or 403)
    assert allowed_resp.status_code == 404

    # 2. Disallowed action: /api/staff/holds requires staff.holds.write
    disallowed_resp = remote_client.put(
        "/api/staff/holds",
        json={"holds": []},
        headers={**_XHR, **auth_header},
    )
    assert disallowed_resp.status_code == 403
    assert disallowed_resp.json()["detail"]["error"] == "Authorization failed"
    assert disallowed_resp.json()["detail"]["required_scope"] == "staff.holds.write"

    # 3. Disallowed action: /api/staff/ad-hoc/run requires staff.dispatch
    disallowed_resp2 = remote_client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "run something", "dry_run": True},
        headers={**_XHR, **auth_header},
    )
    assert disallowed_resp2.status_code == 403
    assert disallowed_resp2.json()["detail"]["required_scope"] == "staff.dispatch"

    # 4. Token invalid after run ends
    credential_mod.revoke_staff_credentials("maintenance", run_id)

    post_end_resp = remote_client.post(
        "/api/runners/999/restart",
        headers={**_XHR, **auth_header},
    )
    assert post_end_resp.status_code == 401


_INSPECT_ENV_CLI = """
import json, os, sys
env_token = os.environ.get("FLEET_API_TOKEN", "")
usage = {"input_tokens": 10, "output_tokens": 10, "cost_usd": 0.001}
print(json.dumps({
    "type": "result",
    "result": f"STAFF_RESULT: token_present={bool(env_token)} token_prefix={env_token[:4]}",
    "usage": usage,
    "total_cost_usd": 0.001
}))
sys.exit(0)
"""


def test_runner_injects_and_revokes_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, identity_env: Path) -> None:
    """Verify runner injects FLEET_API_TOKEN and revokes it when execution ends."""
    cli_path = tmp_path / "inspect_env_cli.py"
    cli_path.write_text(_INSPECT_ENV_CLI, encoding="utf-8")

    class EnvInspectAdapter(adapters_mod.ProviderAdapter):
        def build_command(self, prompt: str, workdir: str, model: str | None = None) -> list[str]:
            return [sys.executable, str(cli_path)]

    adapter = EnvInspectAdapter(
        provider_id="env-inspect",
        label="Env Inspect Adapter",
        executable=sys.executable,
        argv=(),
        json_lines=True,
    )

    role = RoleSpec(
        name="cli-worker",
        title="CLI Worker",
        providers=("env-inspect",),
        fleet_actions=("runner.restart",),
    )
    roles = {"cli-worker": role}
    store = store_mod.get_store()

    runner = runner_mod.StaffRunner(
        store=store,
        roles_loader=lambda: roles,
        adapters={"env-inspect": adapter},
        machine="test-node",
    )

    rec = runner.submit(runner_mod.RunRequest(role="cli-worker", prompt="test token injection"))

    # Wait for completion
    for _ in range(100):
        row = store.get_run(rec.id)
        if row and row.status in ("succeeded", "failed"):
            break
        time.sleep(0.05)

    final_row = store.get_run(rec.id)
    assert final_row is not None
    assert final_row.status == "succeeded"

    # Verify transcript recorded STAFF_RESULT showing token was present
    transcript = Path(final_row.transcript_path)
    assert transcript.exists()
    content = transcript.read_text(encoding="utf-8")
    assert "token_present=True" in content
    assert "token_prefix=stf_" in content

    # Verify token was revoked in finally
    principal_id = f"staff:cli-worker:{rec.id}"
    assert principal_id not in identity_manager.principals


def test_minting_failure_fails_run_as_workspace_error(monkeypatch: pytest.MonkeyPatch, identity_env: Path) -> None:
    """Error handling: minting failure fails run before CLI starts (failure_class="workspace_error")."""

    def _fail_mint(*args: object, **kwargs: object) -> tuple[str, list[str]]:
        raise RuntimeError("Token vault unavailable")

    monkeypatch.setattr(credential_mod, "mint_staff_credentials", _fail_mint)

    role = RoleSpec(
        name="fragile-worker",
        title="Fragile Worker",
        providers=("fake",),
        fleet_actions=("runner.restart",),
        max_attempts=1,
    )
    roles = {"fragile-worker": role}
    store = store_mod.get_store()

    spawned = False
    original_popen = subprocess.Popen

    def _spy_popen(*args: Any, **kwargs: Any) -> Any:
        nonlocal spawned
        spawned = True
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", _spy_popen)

    class DummyAdapter(adapters_mod.ProviderAdapter):
        def build_command(self, prompt: str, workdir: str, model: str | None = None) -> list[str]:
            return [sys.executable, "-c", "exit(0)"]

    adapter = DummyAdapter(
        provider_id="fake",
        label="Fake Adapter",
        executable=sys.executable,
        argv=(),
    )

    runner = runner_mod.StaffRunner(
        store=store,
        roles_loader=lambda: roles,
        adapters={"fake": adapter},
        machine="test-node",
    )

    rec = runner.submit(runner_mod.RunRequest(role="fragile-worker", prompt="test mint failure"))

    # Wait for completion
    for _ in range(100):
        row = store.get_run(rec.id)
        if row and row.status == "failed":
            break
        time.sleep(0.05)

    time.sleep(0.1)

    final_row = store.get_run(rec.id)
    assert final_row is not None
    assert final_row.status == "failed"
    assert final_row.failure_class == "workspace_error"
    assert "Token vault unavailable" in (final_row.error or "")
    assert not spawned  # CLI subprocess was never spawned


def test_reconcile_orphans_revokes_credentials(identity_env: Path) -> None:
    """Verify reconcile_orphaned_runs revokes credentials of orphaned runs."""
    role = RoleSpec(name="maint", title="Maint", fleet_actions=("runner.restart",))
    store = store_mod.get_store()

    rec = RunRecord(
        id="run-orphaned-creds",
        role="maint",
        provider="fake",
        model=None,
        machine="test-node",
        repo="",
        target_kind="prompt",
        target_ref="",
        prompt="hello",
        status="running",
    )
    store.create_run(rec)

    token, _ = credential_mod.mint_staff_credentials(role, rec.id, ttl_seconds=300)
    assert identity_manager.verify_token(token) is not None

    runner = runner_mod.StaffRunner(store=store, machine="test-node")
    reconciled = reconcile_orphaned_runs(runner)
    assert "run-orphaned-creds" in reconciled

    # Token must have been revoked by reconcile
    assert identity_manager.verify_token(token) is None
