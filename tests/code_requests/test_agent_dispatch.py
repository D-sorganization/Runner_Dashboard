"""Tests for Agent-Agnostic Code Request dispatch with customizable profiles (CR-3, Issue #1283)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import dispatch_contract
import pytest
from agent_remediation.provider_registry import PROVIDER_REGISTRY
from code_requests.agent_dispatcher import dispatch_code_request
from code_requests.model import CodeRequest, CodeRequestState, Requester, RequesterKind
from code_requests.profiles import AgentProfile, AgentProfileStore
from code_requests.store import CodeRequestStore
from identity import Principal, identity_manager

ALL_PROVIDER_IDS = [p.dashboard_id for p in PROVIDER_REGISTRY]


@pytest.fixture
def mock_providers_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock all providers in PROVIDER_REGISTRY as available."""
    avail_map = {
        p.dashboard_id: type("_Avail", (), {"available": True, "detail": "ready"})() for p in PROVIDER_REGISTRY
    }
    monkeypatch.setattr("agent_remediation.probe_provider_availability", lambda *a, **k: avail_map)


@pytest.fixture
def authenticated_principal() -> Principal:
    """Register and return a valid test principal with quota."""
    principal = Principal(
        id="operator-tester",
        type="human",
        name="Operator Tester",
        roles=["operator"],
        scopes=["code-requests.manage"],
    )
    identity_manager.principals[principal.id] = principal
    return principal


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id", ALL_PROVIDER_IDS)
async def test_parametrized_dispatch_every_provider(
    provider_id: str,
    tmp_path: Path,
    mock_providers_available: None,  # noqa: ARG001
    authenticated_principal: Principal,
) -> None:
    """Parametrized test for every registry provider: create profile -> dispatch -> envelope valid."""
    store_path = tmp_path / "profiles.json"
    p_store = AgentProfileStore(store_path)
    profile = AgentProfile(
        id=f"profile-{provider_id}",
        name=f"Profile for {provider_id}",
        provider=provider_id,
        model="standard-model",
        effort="medium",
        role="both",
        standards=["tdd", "dbc"],
        budget={"max_cost": 0.25, "max_tokens": 10000},
    )
    p_store.create(profile)

    run_mock = AsyncMock(return_value=(0, "ok", ""))
    audit_history_path = tmp_path / "audit_history.json"

    with patch("code_requests.agent_dispatcher._AUDIT_HISTORY_PATH", audit_history_path):
        code, stderr, envelope = await dispatch_code_request(
            repository="Runner_Dashboard",
            branch="feat/cr-1283",
            provider=provider_id,
            prompt="Implement CR-3 agent-agnostic dispatch test",
            model=profile.model,
            effort=profile.effort,
            principal=authenticated_principal.id,
            budget=profile.budget,
            profile_id=profile.id,
            standards=profile.standards,
            run_cmd_fn=run_mock,
        )

    # 1. Dispatch succeeds
    assert code == 0, f"Dispatch failed for provider {provider_id}: {stderr}"
    assert envelope is not None

    # 2. Envelope is valid and correctly addressed
    val_res = dispatch_contract.validate_envelope(envelope)
    assert val_res.accepted is True
    assert envelope.action == "agents.dispatch.adhoc"
    assert envelope.source == "dashboard.code_requests"
    assert envelope.target == "Runner_Dashboard"
    assert envelope.payload["provider"] == provider_id
    assert envelope.payload["profile_id"] == profile.id
    assert envelope.payload["standards"] == ["tdd", "dbc"]

    # 3. Routing hits Agent-Quick-Dispatch.yml (ADR-0002) rather than legacy Jules-Feature-Request.yml
    run_cmd_call = run_mock.call_args
    assert run_cmd_call is not None
    cmd_args = run_cmd_call[0][0]
    assert any("Agent-Quick-Dispatch.yml" in arg for arg in cmd_args)
    assert not any("Jules-Feature-Request.yml" in arg for arg in cmd_args)


@pytest.mark.asyncio
async def test_budget_overrun_rejected_before_dispatch(
    tmp_path: Path,
    mock_providers_available: None,  # noqa: ARG001
    authenticated_principal: Principal,
) -> None:
    """Dispatch rejects task with 422 if budget exceeds principal quota before any workflow run."""
    # Principal daily spend quota defaults to 10.0
    authenticated_principal.quotas.agent_spend_usd_day = 5.0
    over_budget = {"max_cost": 25.0}  # Exceeds 5.0

    run_mock = AsyncMock(return_value=(0, "", ""))
    code, stderr, envelope = await dispatch_code_request(
        repository="Runner_Dashboard",
        branch="main",
        provider="claude_code_cli",
        prompt="Exceeds budget test",
        principal=authenticated_principal.id,
        budget=over_budget,
        run_cmd_fn=run_mock,
    )

    assert code == 422
    assert "budget_overrun" in stderr
    assert envelope is None
    # Verify no workflow execution happened
    assert run_mock.call_count == 0


@pytest.mark.asyncio
async def test_profile_snapshot_persisted_on_record(tmp_path: Path) -> None:
    """Resolved agent profile is snapshotted on the CodeRequest record for reproducibility."""
    store_path = tmp_path / "code_requests.json"
    p_store = AgentProfileStore(tmp_path / "profiles.json")
    profile = p_store.get_default_or_fallback()

    cr_store = CodeRequestStore(cache_path=store_path)
    now = datetime.now(UTC).isoformat()
    req = CodeRequest(
        id="cr-test-snapshot-1",
        repository="Runner_Dashboard",
        title="Test Snapshot",
        state=CodeRequestState.TRIAGE,
        prompt="Snapshot verification",
        requester=Requester(id="op-1", kind=RequesterKind.HUMAN),
        planner_profile_id=profile.id,
        profile_snapshot=profile.model_dump(mode="json"),
        created_at=now,
        updated_at=now,
    )
    await cr_store.create(req)

    # Verify snapshot stored
    loaded = await cr_store.get("cr-test-snapshot-1")
    assert loaded is not None
    assert loaded.planner_profile_id == profile.id
    assert loaded.profile_snapshot is not None
    assert loaded.profile_snapshot["id"] == profile.id
    assert loaded.profile_snapshot["provider"] == profile.provider
