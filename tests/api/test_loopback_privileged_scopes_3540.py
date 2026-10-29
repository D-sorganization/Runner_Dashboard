"""Issue #3540: loopback principal must not get dispatch, approve, or runners.control scopes."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import (
    LOOPBACK_SCOPES,
    Principal,
    TokenRecord,
    _loopback_principal,
    identity_manager,
    principal_has_scope,
)

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_LOOPBACK = ("127.0.0.1", 53000)

_PRIVILEGED_SCOPES = (
    "staff.dispatch",
    "staff.cancel",
    "staff.approve",
    "workflows.dispatch",
    "workflows.control",
    "runners.control",
    "fleet.control",
    "fleet.maintain",
    "remediation.dispatch",
    "heavy-tests.dispatch",
    "tests.rerun",
    "staff.admin",
    "admin",
)

_ALLOWED_LOOPBACK_SCOPES = (
    "staff.read",
    "staff.chat",
    "fleet.read",
    "coordination.write",
    "proposals.write",
    "memory.read",
    "activity.read",
    "activity.write",
)


@pytest.fixture
def loopback_auth_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    """Configure loopback auth=1 alongside an explicit operator bearer token."""
    id_dir = tmp_path / "identity"
    id_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(id_dir))
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-peer-secret-3540")
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "1")
    monkeypatch.delenv("DASHBOARD_LOOPBACK_OPERATOR_WRITES", raising=False)
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "roles"))

    from staff import conversations as conv_mod
    from staff import runner as runner_mod
    from staff import store as store_mod

    store_mod.reset_store()
    runner_mod.reset_runner()
    conv_mod.reset_conversation_store()

    identity_manager.config_dir = id_dir
    identity_manager.principals_path = id_dir / "principals.yml"
    identity_manager.tokens_path = id_dir / "tokens.yml"

    operator = Principal(id="operator-3540", type="human", name="Operator", roles=["operator"])
    raw_token = "operator-token-3540"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    identity_manager.principals = {operator.id: operator}
    identity_manager.tokens = [
        TokenRecord(
            token_hash=token_hash,
            principal_id=operator.id,
            created_at=0.0,
            name="operator-token",
        )
    ]
    identity_manager.save_principals()
    identity_manager.save_tokens()

    yield {"operator": raw_token}

    conv_mod.reset_conversation_store()
    store_mod.reset_store()
    runner_mod.reset_runner()


@pytest.fixture
def loopback_client(loopback_auth_env: dict[str, str]) -> Iterator[TestClient]:
    from server import app

    with TestClient(app, raise_server_exceptions=False, client=_LOOPBACK) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.unit
@pytest.mark.parametrize("scope", _PRIVILEGED_SCOPES)
def test_loopback_principal_lacks_privileged_scopes(scope: str) -> None:
    """Loopback principal must never hold dispatch, approve, or control scopes."""
    prin = _loopback_principal()
    assert scope not in LOOPBACK_SCOPES
    assert not principal_has_scope(prin, scope)


@pytest.mark.unit
@pytest.mark.parametrize("scope", _ALLOWED_LOOPBACK_SCOPES)
def test_loopback_principal_retains_read_and_agent_scopes(scope: str) -> None:
    """Loopback principal keeps read-only and local agent coordination scopes."""
    prin = _loopback_principal()
    assert scope in LOOPBACK_SCOPES
    assert principal_has_scope(prin, scope)


@pytest.mark.unit
def test_loopback_dispatch_routes_reject_unauthenticated_and_allow_operator(
    loopback_client: TestClient,
    loopback_auth_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    staff_cli_installed,
) -> None:
    """Unauthenticated loopback POST to dispatch routes returns 401/403; operator token succeeds."""
    staff_cli_installed("codex")
    op_headers = {**_XHR, "Authorization": f"Bearer {loopback_auth_env['operator']}"}

    # 1. Staff run dispatch (/api/staff/{role}/run) -> 401 without bearer, 200 with operator token
    unauth_run = loopback_client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "check", "provider": "codex", "dry_run": True},
        headers=_XHR,
    )
    assert unauth_run.status_code == 401

    auth_run = loopback_client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "check", "provider": "codex", "dry_run": True},
        headers=op_headers,
    )
    assert auth_run.status_code == 200, auth_run.text

    # 2. Staff followup sweep (require_scope("staff.dispatch")) -> 403 on loopback, 200 with operator token
    unauth_sweep = loopback_client.post("/api/v1/staff/followup/sweep", headers=_XHR)
    assert unauth_sweep.status_code == 403
    assert "staff.dispatch" in str(unauth_sweep.json())

    from staff import followup as followup_mod

    monkeypatch.setattr(
        followup_mod.FollowupEngine,
        "sweep",
        lambda self, now=None: type("_R", (), {"to_dict": lambda s: {"items_scanned": 0}})(),
    )
    auth_sweep = loopback_client.post("/api/v1/staff/followup/sweep", headers=op_headers)
    assert auth_sweep.status_code == 200, auth_sweep.text

    # 3. Workflow dispatch (require_scope("workflows.control")) -> 403 on loopback, 200 with operator token
    from routers import runs_workflows as rw_router

    async def fake_run_cmd(*_args, **_kwargs):  # noqa: ANN002, ANN003
        return 0, "", ""

    monkeypatch.setattr(rw_router, "run_cmd", fake_run_cmd)
    unauth_wf = loopback_client.post(
        "/api/workflows/dispatch",
        json={"repository": "Runner_Dashboard", "workflow_id": "ci.yml"},
        headers=_XHR,
    )
    assert unauth_wf.status_code == 403
    assert unauth_wf.json()["detail"]["required_scope"] == "workflows.control"

    auth_wf = loopback_client.post(
        "/api/workflows/dispatch",
        json={"repository": "Runner_Dashboard", "workflow_id": "ci.yml"},
        headers=op_headers,
    )
    assert auth_wf.status_code == 200, auth_wf.text


@pytest.mark.unit
def test_loopback_approve_routes_reject_unauthenticated_and_allow_operator(
    loopback_client: TestClient,
    loopback_auth_env: dict[str, str],
) -> None:
    """Unauthenticated loopback POST to proposal decide/execute routes returns 403; operator token succeeds."""
    from staff.approval_authority import params_hash as canonical_params_hash
    from staff.conversations import get_conversation_store

    store = get_conversation_store()
    th = store.create_thread(title="Approve Gate 3540", kind="direct", participants=["barb", "user"])
    msg = store.add_message(
        thread_id=th.id,
        author_kind="role",
        author="barb",
        body_md="Proposal",
    )
    prop = store.create_proposal(
        message_id=msg.id,
        thread_id=th.id,
        action="staff.review_pr",
        params={"repo": "Runner_Dashboard", "pr_number": 3540},
        risk="low",
        principal="barb",
    )
    p_hash = canonical_params_hash(prop.action, prop.params)

    # Unauthenticated loopback decide -> 403 (missing staff.approve)
    unauth_decide = loopback_client.post(
        f"/api/v1/staff/proposals/{prop.id}/decide",
        json={"decision": "approved", "params_hash": p_hash, "reason": "loopback try", "execute": False},
        headers=_XHR,
    )
    assert unauth_decide.status_code == 403
    assert "staff.approve" in str(unauth_decide.json())

    # Unauthenticated loopback execute -> 403 (missing staff.approve)
    unauth_exec = loopback_client.post(
        f"/api/v1/staff/proposals/{prop.id}/execute",
        params={"params_hash": p_hash},
        headers=_XHR,
    )
    assert unauth_exec.status_code == 403
    assert "staff.approve" in str(unauth_exec.json())

    # Authenticated operator token -> 200 approved
    op_headers = {**_XHR, "Authorization": f"Bearer {loopback_auth_env['operator']}"}
    auth_decide = loopback_client.post(
        f"/api/v1/staff/proposals/{prop.id}/decide",
        json={"decision": "approved", "params_hash": p_hash, "reason": "operator approve", "execute": False},
        headers=op_headers,
    )
    assert auth_decide.status_code == 200, auth_decide.text
    assert auth_decide.json()["state"] == "approved"


@pytest.mark.unit
def test_loopback_runner_control_routes_reject_unauthenticated_and_allow_operator(
    loopback_client: TestClient,
    loopback_auth_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unauthenticated loopback POST to runner start/stop/restart returns 403; operator token succeeds."""
    from routers import runners as runners_router

    async def fake_fetch_org_runners(_gh_fn, _org):  # noqa: ANN001
        return {"runners": [{"id": 101, "name": "local-runner-1"}]}

    async def fake_run_runner_svc(_num: int, action: str) -> tuple[int, str, str]:
        return 0, f"{action} ok", ""

    monkeypatch.setattr(runners_router, "fetch_org_runners", fake_fetch_org_runners)
    monkeypatch.setattr(runners_router, "runner_num_from_id", lambda rid, _r: 1 if rid == 101 else None)
    monkeypatch.setattr(runners_router, "run_runner_svc", fake_run_runner_svc)

    for action in ("start", "stop", "restart"):
        unauth_resp = loopback_client.post(f"/api/runners/101/{action}", headers=_XHR)
        assert unauth_resp.status_code == 403, f"expected 403 for unauthenticated loopback {action}"
        assert unauth_resp.json()["detail"]["required_scope"] == "runners.control"

    op_headers = {**_XHR, "Authorization": f"Bearer {loopback_auth_env['operator']}"}
    auth_start = loopback_client.post("/api/runners/101/start", headers=op_headers)
    assert auth_start.status_code == 200, auth_start.text
    assert auth_start.json()["status"] == "started"
