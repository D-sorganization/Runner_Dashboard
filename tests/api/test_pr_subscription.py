"""Tests for PR lifecycle defaults, Auto-fix eligibility, and wake-up cap (#1845 / RD-0).

Acceptance criteria:
- In the dispatch envelope (backend/dispatch_contract.py) and the staff-run prompts,
  add an explicit pr_lifecycle: "arm_and_exit" default. The session opens the PR ready,
  arms auto-merge through automerge_guard, and ends.
- Auto-fix / PR subscription is allowed only on ready PRs, only by explicit operator
  choice, and is capped at N wake-ups (default 3).
- After the cap, hand off to RD-1 (fresh CI-fix dispatch) instead of continuing the long session.
- No session started by the dashboard still holds a PR subscription after its PR opens
  unless the operator opted in.
- A draft PR is never put on Auto-fix.
"""

from __future__ import annotations

from dispatch_contract import (
    CommandEnvelope,
    build_envelope,
)
from staff import scheduler, workspace
from staff.roles import RoleSpec

# ─── 1. Dispatch Envelope pr_lifecycle contract ───────────────────────────────


def test_build_envelope_defaults_pr_lifecycle_to_arm_and_exit() -> None:
    env = build_envelope(
        action="agents.dispatch.adhoc",
        source="dashboard",
        target="Repository_Management",
        requested_by="operator",
    )
    assert hasattr(env, "pr_lifecycle")
    assert env.pr_lifecycle == "arm_and_exit"
    assert env.payload.get("pr_lifecycle") == "arm_and_exit"


def test_build_envelope_preserves_custom_pr_lifecycle() -> None:
    env = build_envelope(
        action="agents.dispatch.adhoc",
        source="dashboard",
        target="Repository_Management",
        requested_by="operator",
        pr_lifecycle="subscribed",
    )
    assert env.pr_lifecycle == "subscribed"
    assert env.payload.get("pr_lifecycle") == "subscribed"


def test_command_envelope_from_dict_restores_pr_lifecycle() -> None:
    data = {
        "action": "agents.dispatch.adhoc",
        "source": "dashboard",
        "target": "Repository_Management",
        "requested_by": "operator",
        "pr_lifecycle": "arm_and_exit",
        "payload": {"pr_lifecycle": "arm_and_exit"},
    }
    env = CommandEnvelope.from_dict(data)
    assert env.pr_lifecycle == "arm_and_exit"
    dumped = env.to_dict()
    assert dumped["pr_lifecycle"] == "arm_and_exit"


# ─── 2. Staff Run Prompts pr_lifecycle contract ───────────────────────────────


def test_fleet_rules_prompts_specify_arm_and_exit_not_draft() -> None:
    rules = workspace.FLEET_RULES
    # Must NOT instruct to open a DRAFT pull request
    assert "DRAFT pull request" not in rules
    assert "draft pull request" not in rules.lower() or "not draft" in rules.lower()
    # Must instruct arm_and_exit and automerge_guard
    assert "arm_and_exit" in rules
    assert "automerge_guard" in rules
    assert "ready" in rules.lower()


def test_scheduled_prompt_specifies_ready_pr_with_automerge() -> None:
    prompt = scheduler.SCHEDULED_PROMPT
    assert "single draft pull request" not in prompt
    assert "ready pull request" in prompt or "arm_and_exit" in prompt
    assert "automerge_guard" in prompt


def test_compose_prompt_carries_arm_and_exit_lifecycle() -> None:
    role = RoleSpec(name="triage", title="Triage Officer")
    prompt = workspace.compose_prompt(
        role,
        repo="Repository_Management",
        target_ref="issue #1892",
        operator_prompt="Execute task",
        branch="staff/triage-1892",
    )
    assert "arm_and_exit" in prompt
    assert "automerge_guard" in prompt


# ─── 3. PR Subscription & Auto-fix Guard Policy ───────────────────────────────


def test_pr_subscription_guard_rejects_draft_pr() -> None:
    from pr_subscription import evaluate_pr_subscription

    decision = evaluate_pr_subscription(
        is_draft=True,
        operator_opt_in=True,
        wakeups_count=0,
        max_wakeups=3,
    )
    assert decision.allowed is False
    assert "draft" in decision.reason.lower()
    assert decision.handoff_to_rd1 is False


def test_pr_subscription_guard_requires_operator_opt_in() -> None:
    from pr_subscription import evaluate_pr_subscription

    decision = evaluate_pr_subscription(
        is_draft=False,
        operator_opt_in=False,
        wakeups_count=0,
        max_wakeups=3,
    )
    assert decision.allowed is False
    assert "opt-in" in decision.reason.lower() or "opt_in" in decision.reason.lower()
    assert decision.pr_lifecycle == "arm_and_exit"


def test_pr_subscription_guard_admits_ready_pr_with_operator_opt_in() -> None:
    from pr_subscription import evaluate_pr_subscription

    decision = evaluate_pr_subscription(
        is_draft=False,
        operator_opt_in=True,
        wakeups_count=1,
        max_wakeups=3,
    )
    assert decision.allowed is True
    assert decision.handoff_to_rd1 is False
    assert decision.pr_lifecycle == "subscribed"


def test_pr_subscription_guard_enforces_wakeup_cap_and_handoff_to_rd1() -> None:
    from pr_subscription import evaluate_pr_subscription

    # At cap (3 wake-ups): rejected with handoff to RD-1
    decision = evaluate_pr_subscription(
        is_draft=False,
        operator_opt_in=True,
        wakeups_count=3,
        max_wakeups=3,
    )
    assert decision.allowed is False
    assert decision.handoff_to_rd1 is True
    assert "RD-1" in decision.reason or "rd-1" in decision.reason.lower()
    assert "cap" in decision.reason.lower()
