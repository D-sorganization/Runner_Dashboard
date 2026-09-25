"""Unit tests for staff run token minting, scoping, TTL, and revocation (#1310, SC-E2)."""

from __future__ import annotations

import time
from typing import Any

import pytest
from identity import identity_manager, principal_has_scope
from staff.tokens import (
    mint_run_token,
    resolve_run_scopes,
    revoke_run_token,
)


@pytest.fixture(autouse=True)
def _cleanup_ephemeral_tokens() -> Any:
    yield
    # Clean up all ephemeral tokens after each test
    identity_manager._ephemeral_principals.clear()
    identity_manager._ephemeral_tokens.clear()


@pytest.mark.unit
def test_resolve_run_scopes_intersects_with_action_policy() -> None:
    # Standard maintenance actions
    actions = ["runner.start", "runner.stop", "queue.diagnose"]
    scopes = resolve_run_scopes(actions)
    assert "runners.control" in scopes
    assert "fleet.maintain" in scopes
    assert "workflows.control" not in scopes

    # Action with fleet.control
    actions_fleet = ["fleet.node_up", "fleet.node_down"]
    scopes_fleet = resolve_run_scopes(actions_fleet)
    assert "fleet.control" in scopes_fleet
    assert "runners.control" not in scopes_fleet

    # Action with workflows.control
    actions_wf = ["queue.purge_stale", "run.cancel", "run.rerun"]
    scopes_wf = resolve_run_scopes(actions_wf)
    assert "workflows.control" in scopes_wf
    assert "fleet.control" not in scopes_wf

    # Unknown / unapproved actions are excluded by intersection with ACTION_POLICY
    actions_unknown = ["runner.destroy_fleet", "runner.burn_money", "runner.start"]
    scopes_unknown = resolve_run_scopes(actions_unknown)
    assert "runners.control" in scopes_unknown
    assert "fleet.maintain" in scopes_unknown
    # Only scopes for runner.start are granted

    # Empty actions -> empty scopes
    assert resolve_run_scopes([]) == []


@pytest.mark.unit
def test_mint_run_token_creates_ephemeral_principal_and_token() -> None:
    run_id = "run-test-mint-01"
    role = "maintenance"
    actions = ["runner.start", "fleet.node_up"]
    raw_token = mint_run_token(role=role, run_id=run_id, fleet_actions=actions, ttl_seconds=300.0)

    assert raw_token.startswith("run_")

    # Verify token resolves principal
    prin = identity_manager.verify_token(raw_token)
    assert prin is not None
    assert prin.id == f"staff:{role}:{run_id}"
    assert prin.type == "bot"
    assert f"staff-run:{run_id}" in prin.roles

    # Verify scopes
    assert principal_has_scope(prin, "runners.control")
    assert principal_has_scope(prin, "fleet.control")
    assert principal_has_scope(prin, "fleet.maintain")
    assert not principal_has_scope(prin, "workflows.control")


@pytest.mark.unit
def test_revoke_run_token_invalidates_token() -> None:
    run_id = "run-test-revoke-01"
    role = "maintenance"
    raw_token = mint_run_token(role=role, run_id=run_id, fleet_actions=["runner.restart"], ttl_seconds=300.0)

    assert identity_manager.verify_token(raw_token) is not None

    # Revoke
    revoke_run_token(run_id)

    # Token is now invalid
    assert identity_manager.verify_token(raw_token) is None
    # Principal is gone
    assert identity_manager.get_principal(f"staff:{role}:{run_id}") is None


@pytest.mark.unit
def test_run_token_expires_after_ttl() -> None:
    run_id = "run-test-ttl-01"
    role = "maintenance"
    # Mint with 0.1s TTL
    raw_token = mint_run_token(role=role, run_id=run_id, fleet_actions=["queue.diagnose"], ttl_seconds=0.1)

    assert identity_manager.verify_token(raw_token) is not None

    # Sleep past expiration
    time.sleep(0.15)

    # Expired token returns None
    assert identity_manager.verify_token(raw_token) is None


@pytest.mark.unit
def test_mint_run_token_validation_errors() -> None:
    with pytest.raises(ValueError, match="role and run_id are required"):
        mint_run_token(role="", run_id="run-1", fleet_actions=[], ttl_seconds=10.0)

    with pytest.raises(ValueError, match="role and run_id are required"):
        mint_run_token(role="maintenance", run_id="", fleet_actions=[], ttl_seconds=10.0)

    with pytest.raises(ValueError, match="ttl_seconds must be positive"):
        mint_run_token(role="maintenance", run_id="run-1", fleet_actions=[], ttl_seconds=-5.0)
