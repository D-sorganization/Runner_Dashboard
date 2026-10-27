"""Merge-queue jump helpers."""

from __future__ import annotations

import pytest
from escalation.merge_jump import (
    EscalatePlan,
    interpret_enqueue_error,
    personal_jump_command,
    refusal_from_pr_payload,
    split_repo,
)


@pytest.mark.unit
def test_split_repo_bare_name() -> None:
    owner, repo = split_repo("Runner_Dashboard")
    assert owner == "D-sorganization"
    assert repo == "Runner_Dashboard"


@pytest.mark.unit
def test_refusal_hold_label() -> None:
    msg = refusal_from_pr_payload({"state": "open", "labels": [{"name": "hold"}]}, "Runner_Dashboard")
    assert msg is not None and "hold" in msg.lower()


@pytest.mark.unit
def test_refusal_blocked_checks() -> None:
    msg = refusal_from_pr_payload({"state": "open", "labels": [], "mergeable_state": "blocked"}, "X")
    assert msg is not None


@pytest.mark.unit
def test_permission_error_returns_personal_command() -> None:
    plan = EscalatePlan("D-sorganization", "Runner_Dashboard", 1, "PR_kwDO", "abc123")
    result = interpret_enqueue_error("HTTP 403: must have admin rights", "", plan)
    assert result.manual_command.startswith("gh-personal")
    assert "jump: true" in result.manual_command or "jump:true" in result.manual_command.replace(" ", "")


@pytest.mark.unit
def test_personal_jump_command_includes_ids() -> None:
    plan = EscalatePlan("D-sorganization", "Runner_Dashboard", 9, "PR_node", "deadbeef")
    cmd = personal_jump_command(plan)
    assert "PR_node" in cmd and "deadbeef" in cmd
