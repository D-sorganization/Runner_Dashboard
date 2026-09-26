"""Tests for unified action vocabulary between chat replies and action registry (SC-B1-G3, #1486)."""

from __future__ import annotations

import pytest
from staff.actions import ACTION_REGISTRY
from staff.reply_contract import (
    ALL_KNOWN_ACTIONS,
    generate_chat_contract_text,
    get_chat_contract_text,
    get_known_actions,
    parse_reply,
)
from staff.roles import RoleSpec, parse_role


@pytest.fixture
def barb_role() -> RoleSpec:
    return parse_role(
        {
            "name": "barb",
            "title": "Staff Router",
            "instructions": "Route requests across fleet.",
            "providers": ["claude"],
            "permissions": {
                "can_dispatch": True,
            },
            "persona": {
                "voice": "Direct and helpful.",
                "greeting": "Barb standing by.",
                "answers": ["fleet triage"],
                "defers_to": ["cartographer", "maintenance"],
            },
            "chat": {
                "contract": "staff/prompts/_chat_contract.md",
                "providers": ["claude"],
                "tools": ["read_repo"],
            },
        }
    )


@pytest.fixture
def maintenance_role() -> RoleSpec:
    return parse_role(
        {
            "name": "maintenance",
            "title": "Fleet Maintenance",
            "instructions": "Maintain fleet runners.",
            "providers": ["claude"],
            "permissions": {
                "fleet_actions": ["runner.start", "runner.stop", "run.cancel"],
            },
            "persona": {
                "voice": "Precise.",
                "greeting": "Maintenance here.",
                "answers": ["runner health"],
                "defers_to": ["barb"],
            },
            "chat": {
                "contract": "staff/prompts/_chat_contract.md",
                "providers": ["claude"],
                "tools": ["read_run"],
            },
        }
    )


@pytest.mark.unit
def test_every_reply_contract_name_has_registered_executor() -> None:
    """Pinning test: every action recognized by the reply contract must have a registered executor."""
    known_actions = get_known_actions()
    assert len(known_actions) > 0, "Known actions set must not be empty"

    for action_name in sorted(known_actions):
        action_def = ACTION_REGISTRY.get(action_name)
        assert action_def is not None, f"Action '{action_name}' is in reply contract but missing from ACTION_REGISTRY"
        assert callable(action_def.executor), f"Action '{action_name}' must have a callable executor"


@pytest.mark.unit
def test_reply_contract_vocabulary_matches_action_registry() -> None:
    """The reply contract vocabulary must match the action registry exactly (single vocabulary)."""
    registry_action_names = {a.name for a in ACTION_REGISTRY.list_actions()}
    assert get_known_actions() == registry_action_names
    assert ALL_KNOWN_ACTIONS == registry_action_names


@pytest.mark.unit
def test_chat_reply_proposing_staff_dispatch_becomes_proposal(barb_role: RoleSpec) -> None:
    """Acceptance criteria: A chat reply proposing staff.dispatch becomes a proposal."""
    raw = (
        "I have analyzed the request and recommend dispatching the planner.\n\n"
        "```staff-actions\n"
        "[\n"
        "  {\n"
        '    "action": "staff.dispatch",\n'
        '    "params": {"role": "planner", "repo": "Runner_Dashboard", "prompt": "Plan epic"},\n'
        '    "reason": "Dispatch planner for new issue breakdown."\n'
        "  }\n"
        "]\n"
        "```\n"
    )
    parsed = parse_reply(raw, role=barb_role)
    assert "I have analyzed the request" in parsed.reply
    assert "```staff-actions" not in parsed.reply
    assert len(parsed.actions) == 1
    proposal = parsed.actions[0]
    assert proposal.action == "staff.dispatch"
    assert proposal.params == {"role": "planner", "repo": "Runner_Dashboard", "prompt": "Plan epic"}
    assert proposal.reason == "Dispatch planner for new issue breakdown."
    assert parsed.warnings == []


@pytest.mark.unit
def test_chat_reply_proposing_maintenance_action_becomes_proposal(maintenance_role: RoleSpec) -> None:
    """A maintenance role proposing maintenance.runner_start becomes a proposal."""
    raw = (
        "Runner host-3 is offline and should be restarted.\n\n"
        "```staff-actions\n"
        "[\n"
        "  {\n"
        '    "action": "maintenance.runner_start",\n'
        '    "params": {"runner_name": "host-3"},\n'
        '    "reason": "Restart stalled runner."\n'
        "  }\n"
        "]\n"
        "```\n"
    )
    parsed = parse_reply(raw, role=maintenance_role)
    assert len(parsed.actions) == 1
    assert parsed.actions[0].action == "maintenance.runner_start"
    assert parsed.actions[0].params == {"runner_name": "host-3"}
    assert parsed.warnings == []


@pytest.mark.unit
def test_unregistered_name_dropped_with_warning(barb_role: RoleSpec) -> None:
    """Acceptance criteria: An unregistered name is dropped with a warning, preserving prose."""
    raw = (
        "Here is my assessment of the fleet status.\n\n"
        "```staff-actions\n"
        "[\n"
        '  {"action": "destroy_all_servers", "params": {}, "reason": "Arbitrary unregistered action"},\n'
        '  {"action": "format_hard_drive", "params": {"repo": "Tools"}, "reason": "Arbitrary unregistered action 2"},\n'
        '  {"action": "staff.dispatch", "params": {"role": "cartographer"}, "reason": "Valid action"}\n'
        "]\n"
        "```\n"
    )
    parsed = parse_reply(raw, role=barb_role)
    assert parsed.reply == "Here is my assessment of the fleet status."
    assert len(parsed.actions) == 1
    assert parsed.actions[0].action == "staff.dispatch"

    # Both unregistered actions must produce warnings
    warnings_text = " ".join(parsed.warnings)
    assert "destroy_all_servers" in warnings_text
    assert "format_hard_drive" in warnings_text
    assert "unknown and was dropped" in warnings_text.lower()


@pytest.mark.unit
def test_chat_contract_prompt_text_generated_from_registry() -> None:
    """Acceptance criteria: The reply-contract prompt text is generated from the registry (DRY)."""
    text = generate_chat_contract_text()
    assert "Chat Reply Contract" in text
    assert "staff-actions" in text
    assert "handoff:" in text
    assert "question:" in text

    # All registered action names must appear in the generated prompt table
    for action in ACTION_REGISTRY.list_actions():
        assert f"`{action.name}`" in text, f"Registered action '{action.name}' must be listed in contract prompt"

    # Contract text returned by get_chat_contract_text matches or includes generated text
    default_text = get_chat_contract_text()
    assert "`staff.dispatch`" in default_text
    assert "`maintenance.runner_start`" in default_text
