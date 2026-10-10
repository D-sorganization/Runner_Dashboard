"""expand_epic_children dispatch kind (USE-1, issue #1865)."""

from __future__ import annotations

import pytest
from dispatch_effort import EXPAND_EPIC_CHILDREN
from epic_expansion import (
    SHARED_RULE_LINKS,
    ChildIssueSpec,
    plan_epic_expansion,
    render_child_issue_body,
    render_expansion_prompt,
)
from pydantic import ValidationError


def _child(**overrides: object) -> ChildIssueSpec:
    data: dict[str, object] = {
        "title": "Add effort to dispatch routing",
        "objective": "Every dispatch kind resolves a reasoning effort.",
        "acceptance": ["Unknown kinds fall back to medium.", "Envelope validates effort."],
        "paths": ["backend/dispatch_effort.py"],
    }
    data.update(overrides)
    return ChildIssueSpec.model_validate(data)


def test_child_body_links_shared_rules_instead_of_repeating_them() -> None:
    body = render_child_issue_body(
        epic_repository="D-sorganization/Repository_Management", epic_number=1889, child=_child()
    )
    for url in SHARED_RULE_LINKS.values():
        assert url in body
    assert "Part of D-sorganization/Repository_Management#1889." in body
    assert "- [ ] Unknown kinds fall back to medium." in body
    assert "`backend/dispatch_effort.py`" in body
    # The rules are linked, never inlined: no rule body text is repeated.
    assert "RED (failing test)" not in body
    assert len(body) < 2_000


def test_child_spec_requires_acceptance_criteria() -> None:
    with pytest.raises(ValidationError):
        _child(acceptance=[])
    with pytest.raises(ValidationError):
        _child(title="  ")


def test_expansion_plan_routes_to_cli_tier_sonnet_with_low_effort() -> None:
    plan = plan_epic_expansion(repository="D-sorganization/Runner_Dashboard", epic_number=1889)
    assert plan.dispatch_kind == EXPAND_EPIC_CHILDREN
    assert plan.tier == "cli"
    assert "sonnet" in plan.model
    assert plan.effort == "low"


def test_expansion_plan_honours_explicit_model() -> None:
    plan = plan_epic_expansion(repository="D-sorganization/Runner_Dashboard", epic_number=1889, requested_model="x")
    assert plan.model == "x"
    assert plan.tier == "cli"


def test_expansion_prompt_keeps_the_epic_strong_and_links_the_template() -> None:
    prompt = render_expansion_prompt(repository="D-sorganization/Runner_Dashboard", epic_number=1889, operator_note="")
    assert "D-sorganization/Runner_Dashboard#1889" in prompt
    assert "Do not edit the epic" in prompt
    for url in SHARED_RULE_LINKS.values():
        assert url in prompt
    assert "tier:cli" in prompt


def test_expansion_prompt_appends_operator_note() -> None:
    prompt = render_expansion_prompt(repository="o/r", epic_number=1, operator_note="Split by router.")
    assert prompt.rstrip().endswith("Split by router.")


@pytest.mark.parametrize("number", [0, -3])
def test_plan_rejects_invalid_epic_number(number: int) -> None:
    with pytest.raises(ValueError, match="epic_number"):
        plan_epic_expansion(repository="o/r", epic_number=number)
