"""Tests for staff action executor role resolution against loaded roster (WP-0.1, Issue #1474)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import anyio
import anyio.to_thread
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


def test_execute_review_pr_defaults_to_code_reviewer() -> None:
    """staff.review_pr with no reviewer dispatches a code-reviewer run (via the shared service, #1487, #1518)."""
    seen: list[Any] = []

    async def fake_dispatch(cmd: Any, caller: Any) -> dict[str, Any]:
        seen.append(cmd)
        return {"dry_run": False, "run": {"id": "run-1474", "role": cmd.role, "repo": cmd.repo, "status": "queued"}}

    async def main() -> Any:
        ctx = ActionContext(caller=None, thread_id="th-test")
        return await anyio.to_thread.run_sync(execute_review_pr, {"repo": "Runner_Dashboard", "pr": 42}, ctx)

    with patch("staff.dispatch_service.dispatch_staff_run", fake_dispatch):
        result = anyio.run(main)
    assert result.success is True and result.run_id == "run-1474"
    assert [c.role for c in seen] == ["code-reviewer"]
    assert seen[0].repo == "Runner_Dashboard" and seen[0].pr == 42


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
