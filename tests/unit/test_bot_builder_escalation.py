"""Cumulative escalation diff contract tests (RD #3103)."""

from __future__ import annotations

from staff.bot_builder import BuilderRoleDefinition, escalation_diff, parse_builder_role


def _role(
    persona: str,
    instructions: str,
    capabilities: list[str],
    name: str = "pilot-bot",
) -> BuilderRoleDefinition:
    return parse_builder_role(
        {
            "schema_version": 1,
            "name": name,
            "persona": persona,
            "instructions": instructions,
            "skills": [],
            "model": None,
            "providers": ["sub-cli-alpha"],
            "schedule": None,
            "triggers": [],
            "host": "grokbot-first",
            "enabled": False,
            "capabilities": capabilities,
            "private_context_ref": None,
            "household_data_opt_in": False,
        },
    )


def test_persona_only_edit_requires_confirmation_without_capability_change() -> None:
    original = _role("Alpha persona.", "Same instructions.", ["rd.staff_api"])
    previous = _role("Alpha persona.", "Same instructions.", ["rd.staff_api"])
    candidate = _role("Beta persona.", "Same instructions.", ["rd.staff_api"])
    diff = escalation_diff(original, previous, candidate)
    assert diff.newly_able_vs_previous == ()
    assert diff.newly_able_vs_original == ()
    assert "persona" in diff.requires_confirmation
    assert "instructions" not in diff.requires_confirmation


def test_instructions_edit_requires_confirmation() -> None:
    original = _role("Same.", "Original instructions.", ["rd.staff_api"])
    previous = original
    candidate = _role("Same.", "Updated instructions.", ["rd.staff_api"])
    diff = escalation_diff(original, previous, candidate)
    assert "instructions" in diff.requires_confirmation


def test_cumulative_widening_flags_against_original_across_small_steps() -> None:
    original = _role("Same.", "Same.", ["rd.staff_api"])
    step_a = _role("Same.", "Same.", ["rd.staff_api", "rd.cli_sessions"])
    step_b = _role("Same.", "Same.", ["rd.staff_api", "rd.cli_sessions", "grok.chat_cards"])

    diff_step_a = escalation_diff(original, original, step_a)
    assert diff_step_a.newly_able_vs_previous == ("rd.cli_sessions",)
    assert diff_step_a.newly_able_vs_original == ("rd.cli_sessions",)

    diff_step_b = escalation_diff(original, step_a, step_b)
    assert diff_step_b.newly_able_vs_previous == ("grok.chat_cards",)
    assert set(diff_step_b.newly_able_vs_original) == {"rd.cli_sessions", "grok.chat_cards"}


def test_declared_triggers_only_in_schema_not_expanded() -> None:
    """Loop checks (slice 13) must use declared trigger edges only — schema carries them."""
    role = parse_builder_role(
        {
            "schema_version": 1,
            "name": "pilot-bot",
            "persona": "p",
            "instructions": "i",
            "skills": [],
            "model": None,
            "providers": ["sub-cli-alpha"],
            "schedule": "0 9 * * *",
            "triggers": [{"target_role": "night-watch", "event": "schedule_tick"}],
            "host": "grokbot-first",
            "enabled": False,
            "capabilities": ["rd.staff_api"],
            "private_context_ref": None,
            "household_data_opt_in": False,
        },
    )
    assert len(role.triggers) == 1
    assert role.triggers[0].target_role == "night-watch"
