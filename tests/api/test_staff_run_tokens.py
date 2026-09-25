"""API integration tests for staff run credentials and endpoint authorization (#1310, SC-E2)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from server import app
from staff.reconcile import reconcile_orphaned_runs
from staff.runner import RunRecord, RunRequest, StaffRunner
from staff.store import RunStore
from staff.tokens import mint_run_token, revoke_run_token


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_fleet_api_token_endpoint_authorization_and_revocation(client: TestClient) -> None:
    """A maintenance run token allows authorized endpoints, blocks unauthorized with 403, and 401s after revoke."""
    run_id = "run-auth-test-01"
    # Role with runner actions only
    token = mint_run_token(
        role="maintenance",
        run_id=run_id,
        fleet_actions=["runner.restart", "runner.stop"],
        ttl_seconds=300.0,
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Requested-With": "XMLHttpRequest",
    }

    # 1. Allowed endpoint requiring runners.control: /api/runners/test-runner/restart
    # Auth passes: does NOT return 401 (auth failed) or 403 (forbidden).
    resp_allowed = client.post("/api/runners/nonexistent-runner/restart", headers=headers)
    assert resp_allowed.status_code not in (401, 403), f"Expected auth pass, got {resp_allowed.status_code}"

    # 2. Disallowed endpoint requiring fleet.control: /api/fleet/control/{action}
    resp_disallowed = client.post("/api/fleet/control/test-action", json={}, headers=headers)
    assert resp_disallowed.status_code == 403, f"Expected 403 Forbidden, got {resp_disallowed.status_code}"
    data = resp_disallowed.json()
    assert data["detail"]["required_scope"] == "fleet.control"
    assert data["detail"]["principal"] == f"staff:maintenance:{run_id}"

    # 3. Disallowed endpoint requiring workflows.control: /api/queue/purge-stale
    resp_disallowed_wf = client.post("/api/queue/purge-stale", json={}, headers=headers)
    assert resp_disallowed_wf.status_code == 403
    assert resp_disallowed_wf.json()["detail"]["required_scope"] == "workflows.control"

    # 4. Revoke token at run end
    revoke_run_token(run_id)

    # 5. Token is now invalid (401 Unauthorized)
    resp_revoked = client.post("/api/runners/nonexistent-runner/restart", headers=headers)
    assert resp_revoked.status_code == 401, f"Expected 401 Unauthorized, got {resp_revoked.status_code}"


def test_runner_injects_and_revokes_fleet_api_token(tmp_path: Any) -> None:
    """StaffRunner injects FLEET_API_TOKEN into env and revokes it in finally block."""
    store = RunStore(tmp_path / "runs.db")
    runner = StaffRunner(store=store)

    captured_env: dict[str, str] = {}
    captured_token: list[str] = []

    class DummyAdapter:
        executable = "dummy"
        prompt_via_stdin = False
        lease_agent = "dummy"

        def installed(self) -> bool:
            return True

        def build_command(self, prompt: str, workdir: str, model: str | None = None) -> list[str]:
            return ["dummy"]

        def runtime_env(self) -> dict[str, str]:
            return {}

        def parse_line(self, line: str) -> dict[str, Any]:
            return {"kind": "text", "text": line}

    runner._adapters = {"dummy": DummyAdapter()}

    from staff.roles import RoleSpec

    dummy_role = RoleSpec(
        name="maintenance",
        title="Maintenance",
        providers=("dummy",),
        permissions={
            "lease": False,
            "push_branch": False,
            "open_pr": False,
            "merge": False,
            "host_shell": False,
            "notify_user": False,
            "fleet_actions": ["runner.start", "runner.stop"],
        },
    )
    runner._roles_loader = lambda: {"maintenance": dummy_role}

    rec = RunRecord(
        id="run-exec-test-01",
        role="maintenance",
        provider="dummy",
        model="default",
        machine="local",
        repo="",
        target_kind="prompt",
        target_ref="",
        prompt="do maintenance",
    )
    store.create_run(rec)
    plan = runner.plan(RunRequest(role="maintenance", prompt="do maintenance", provider="dummy"))

    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.stdout = iter(["STAFF_RESULT: done\n"])
        mock_proc.wait.return_value = 0
        mock_proc.poll.return_value = 0

        def capture_call(*args: Any, **kwargs: Any) -> Any:
            nonlocal captured_env
            captured_env = dict(kwargs.get("env", {}))
            if "FLEET_API_TOKEN" in captured_env:
                captured_token.append(captured_env["FLEET_API_TOKEN"])
            return mock_proc

        mock_popen.side_effect = capture_call

        workdir = tmp_path / "work"
        workdir.mkdir()
        runner._execute(rec, plan, workdir, lease_note="")

    assert captured_token, "FLEET_API_TOKEN was not injected into subprocess env"
    raw_token = captured_token[0]
    assert raw_token.startswith("run_")

    # Verify that token is revoked after _execute finishes
    from identity import identity_manager

    assert identity_manager.verify_token(raw_token) is None, "Token must be revoked after execution"


def test_runner_minting_failure_sets_workspace_error(tmp_path: Any) -> None:
    """If token minting fails, run fails before starting CLI with failure_class='workspace_error'."""
    store = RunStore(tmp_path / "runs.db")

    class DummyAdapter:
        executable = "dummy"
        prompt_via_stdin = False
        lease_agent = "dummy"

        def installed(self) -> bool:
            return True

        def build_command(self, prompt: str, workdir: str, model: str | None = None) -> list[str]:
            return ["dummy"]

        def runtime_env(self) -> dict[str, str]:
            return {}

        def parse_line(self, line: str) -> dict[str, Any]:
            return {"kind": "text", "text": line}

    runner = StaffRunner(store=store, adapters={"dummy": DummyAdapter()})

    from staff.roles import RoleSpec

    dummy_role = RoleSpec(
        name="maintenance",
        title="Maintenance",
        providers=("dummy",),
        permissions={
            "lease": False,
            "push_branch": False,
            "open_pr": False,
            "merge": False,
            "host_shell": False,
            "notify_user": False,
        },
    )
    runner._roles_loader = lambda: {"maintenance": dummy_role}

    rec = RunRecord(
        id="run-mint-fail-01",
        role="maintenance",
        provider="dummy",
        model="default",
        machine="local",
        repo="",
        target_kind="prompt",
        target_ref="",
        prompt="do maintenance",
    )
    store.create_run(rec)
    plan = runner.plan(RunRequest(role="maintenance", prompt="do maintenance", provider="dummy"))

    workdir = tmp_path / "work"
    workdir.mkdir()

    with patch("staff.runner.mint_run_token", side_effect=RuntimeError("entropy failure")):
        with patch("subprocess.Popen") as mock_popen:
            runner._execute(rec, plan, workdir, lease_note="")
            assert not mock_popen.called, "CLI subprocess must not be called when token minting fails"

    updated = store.get_run(rec.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.failure_class == "workspace_error"
    assert "token minting failed" in (updated.error or "")


def test_reconcile_orphaned_runs_revokes_tokens(tmp_path: Any) -> None:
    """Reconciling orphaned runs revokes their active run tokens."""
    store = RunStore(tmp_path / "runs.db")
    runner = StaffRunner(store=store)

    run_id = "run-orphan-tok-01"
    raw_token = mint_run_token(
        role="maintenance",
        run_id=run_id,
        fleet_actions=["runner.start"],
        ttl_seconds=300.0,
    )

    from identity import identity_manager

    assert identity_manager.verify_token(raw_token) is not None

    rec = RunRecord(
        id=run_id,
        role="maintenance",
        provider="dummy",
        model="default",
        status="running",
        machine=runner.machine,
        repo="",
        target_kind="prompt",
        target_ref="",
        prompt="do maintenance",
        workdir="",
    )
    store.create_run(rec)

    reconciled = reconcile_orphaned_runs(runner)
    assert run_id in reconciled

    # Token must be revoked
    assert identity_manager.verify_token(raw_token) is None
