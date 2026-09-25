"""Tests for staff action executor role resolution against loaded roster (WP-0.1, Issue #1474)."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from staff.action_executors import (
    BOARD_PROPOSAL_ROLE,
    CODE_REQUEST_OWNER_ROLE,
    DEFAULT_REVIEWER_ROLE,
    execute_review_pr,
    validate_action_default_roles,
)
from staff.actions import ActionContext


def test_action_executor_default_roles_resolve_and_are_dispatchable() -> None:
    """Assert every default role string in action_executors.py resolves to a dispatchable role."""
    from staff.roles import load_roles, roles_dir

    r_dir = roles_dir()
    if r_dir is None or not r_dir.is_dir():
        pytest.skip("Repository_Management checkout not found")

    roster = load_roles(r_dir)
    default_roles = [
        DEFAULT_REVIEWER_ROLE,
        CODE_REQUEST_OWNER_ROLE,
        BOARD_PROPOSAL_ROLE,
    ]
    for role_name in default_roles:
        assert role_name in roster, f"Default role '{role_name}' not found in loaded roster"
        spec = roster[role_name]
        assert not spec.retired, f"Default role '{role_name}' is retired: {spec.retired_reason}"
        assert spec.surface in {"dashboard", "both"}, (
            f"Default role '{role_name}' has unexpected surface: {spec.surface}"
        )
        assert spec.dispatchable, f"Default role '{role_name}' is not dispatchable"


def test_execute_review_pr_defaults_to_fleet_critic() -> None:
    """staff.review_pr with no reviewer starts a fleet-critic run."""
    mock_runner = MagicMock()
    mock_rec = MagicMock()
    mock_rec.id = "run-1474"
    mock_rec.role = "fleet-critic"
    mock_rec.repo = "Runner_Dashboard"
    mock_rec.status = "queued"
    mock_runner.submit.return_value = mock_rec

    with patch("staff.runner.get_runner", return_value=mock_runner):
        ctx = ActionContext(caller=None, thread_id="th-test")
        result = execute_review_pr({"repo": "Runner_Dashboard", "pr": 42}, ctx)
        assert result.success is True
        assert mock_runner.submit.called
        call_req = mock_runner.submit.call_args[0][0]
        assert call_req.role == "fleet-critic"
        assert call_req.repo == "Runner_Dashboard"
        assert call_req.pr == 42


def test_validate_action_default_roles_clean_on_valid_roster() -> None:
    """Validation succeeds with zero errors on standard loaded roster."""
    from staff.roles import roles_dir

    if roles_dir() is None:
        pytest.skip("Repository_Management checkout not found")
    errors = validate_action_default_roles(raise_on_error=True)
    assert errors == []


def test_validate_action_default_roles_skips_when_no_roles_dir() -> None:
    """When roles_dir is None (e.g. standalone/CI node), validation gracefully returns empty."""
    with patch("staff.roles.roles_dir", return_value=None):
        assert validate_action_default_roles(raise_on_error=True) == []


def test_validate_action_default_roles_fails_loudly_on_missing_role(caplog: pytest.LogCaptureFixture) -> None:
    """When a role does not resolve, fail loudly in tests and log warning at runtime."""
    with (
        patch("staff.roles.roles_dir", return_value=Path("/mock/roles")),
        patch("staff.roles.load_roles", return_value={}),
        patch("staff.action_executors.ACTION_DEFAULT_ROLES", ("nonexistent-role",)),
    ):
        with caplog.at_level(logging.WARNING):
            errors = validate_action_default_roles(raise_on_error=False)
            assert len(errors) == 1
            assert "nonexistent-role" in errors[0]
            assert any("nonexistent-role" in r.message for r in caplog.records)

        with pytest.raises(ValueError, match="nonexistent-role"):
            validate_action_default_roles(raise_on_error=True)
