"""Built-in fleet rules carried by every staff prompt (issue #1217)."""

from __future__ import annotations

from staff import workspace
from staff.roles import RoleSpec


def _role() -> RoleSpec:
    return RoleSpec(name="night-watch", title="Night Watch", instructions="Overnight triage.")


def test_fleet_rules_carry_all_agent_guardrails() -> None:
    rules = workspace.FLEET_RULES
    assert "never merge" in rules
    assert "claim:local" in rules
    assert "live lease" in rules
    assert "bulk remediation issues" in rules


def test_compose_prompt_always_ends_with_fleet_rules() -> None:
    prompt = workspace.compose_prompt(
        _role(), repo="Tools", target_ref="issue #1", operator_prompt="", branch="staff/night-watch-1"
    )
    assert prompt.endswith(workspace.FLEET_RULES)
    assert "claim:local" in prompt
