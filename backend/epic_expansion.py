"""``expand_epic_children`` dispatch kind (USE-1, issue #1865).

Writing child issue bodies from an epic whose design is already decided is
well-specified work, so it is routed to the ``tier:cli`` provider (Sonnet) at
low effort. The epic itself stays ``tier:strong``.

Planning sessions used to repeat the same ~10 KB rules block in every child
body. Children rendered here **link** the shared rules (TDD/DbC/LoD/DRY, PR
lifecycle) instead of repeating them, so a body stays a few hundred bytes.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from dispatch_effort import EXPAND_EPIC_CHILDREN, Effort, resolve_effort
from dispatch_routing import ModelRoutingDecision, resolve_model_routing
from pydantic import BaseModel, Field, field_validator

log = logging.getLogger("dashboard.epic_expansion")

_RM_BLOB = "https://github.com/D-sorganization/Repository_Management/blob/main"

SHARED_RULE_LINKS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "Engineering rules (TDD, DbC, LoD, DRY)": f"{_RM_BLOB}/docs/agents/prompts/cli_tier_task.md",
        "PR lifecycle (ready PR, auto-merge, end session)": f"{_RM_BLOB}/fleet-rules/pr-lifecycle.md",
        "Agent tiers": f"{_RM_BLOB}/docs/agents/AGENT_TIER_ROUTING.md",
    }
)


class ChildIssueSpec(BaseModel):
    """One child issue decided by the epic: the only per-child content."""

    title: str = Field(..., min_length=1, max_length=200)
    objective: str = Field(..., min_length=1, max_length=2_000)
    acceptance: list[str] = Field(..., min_length=1, max_length=20)
    paths: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("title", "objective")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("acceptance")
    @classmethod
    def _criteria_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned:
            raise ValueError("at least one acceptance criterion is required")
        return cleaned


@dataclass(frozen=True, slots=True)
class EpicExpansionPlan:
    """Routing for one ``expand_epic_children`` dispatch."""

    dispatch_kind: str
    repository: str
    epic_number: int
    tier: str
    model: str
    effort: Effort
    reason: str


def _rules_block() -> str:
    return "\n".join(f"- [{label}]({url})" for label, url in SHARED_RULE_LINKS.items())


def render_child_issue_body(*, epic_repository: str, epic_number: int, child: ChildIssueSpec) -> str:
    """Render a child issue body that links the shared rules.

    Precondition: ``epic_number`` > 0 and ``child`` is a validated spec.
    Postcondition: the body references the epic, lists every acceptance
    criterion as a checkbox, and links (never inlines) each shared rule.
    """
    if epic_number <= 0:
        raise ValueError("epic_number must be positive")
    lines = [
        f"Part of {epic_repository}#{epic_number}.",
        "",
        "## Objective",
        child.objective,
        "",
        "## Acceptance",
        *(f"- [ ] {item}" for item in child.acceptance),
    ]
    if child.paths:
        lines += ["", "## Paths", *(f"- `{path}`" for path in child.paths)]
    lines += ["", "## Rules", "Follow the linked fleet rules; they are not repeated here.", _rules_block()]
    return "\n".join(lines) + "\n"


def render_expansion_prompt(*, repository: str, epic_number: int, operator_note: str) -> str:
    """Prompt for the tier:cli session that files the epic's children."""
    if epic_number <= 0:
        raise ValueError("epic_number must be positive")
    prompt = (
        f"Expand the decided epic {repository}#{epic_number} into its child issues.\n"
        "Do not edit the epic or reopen its design decisions; if a child needs a design "
        "decision, stop and report it on the epic.\n"
        "For each child, file one issue labelled `tier:cli` whose body has only: "
        "`Part of <epic>`, an Objective, Acceptance checkboxes, optional Paths, and a Rules "
        "section that links these documents instead of repeating them:\n"
        f"{_rules_block()}\n"
    )
    if operator_note.strip():
        prompt += f"\nOperator note: {operator_note.strip()}\n"
    return prompt


def expansion_routing(requested_model: str = "") -> ModelRoutingDecision:
    """tier:cli routing for epic expansion; an explicit model is honoured."""
    routing = resolve_model_routing(labels=("tier:cli",), prompt="", requested_model=requested_model)
    return ModelRoutingDecision(
        tier="cli",
        model=routing.model,
        reason="expand_epic_children: well-specified issue-body expansion routed to tier:cli",
    )


def plan_epic_expansion(*, repository: str, epic_number: int, requested_model: str = "") -> EpicExpansionPlan:
    """Route an ``expand_epic_children`` dispatch to the tier:cli provider.

    Precondition: ``epic_number`` > 0.
    Postcondition: ``tier == "cli"``; ``effort`` comes from the dispatch-kind
    mapping; an explicit ``requested_model`` is honoured.
    """
    if epic_number <= 0:
        raise ValueError("epic_number must be positive")
    routing = expansion_routing(requested_model)
    plan = EpicExpansionPlan(
        dispatch_kind=EXPAND_EPIC_CHILDREN,
        repository=repository,
        epic_number=epic_number,
        tier=routing.tier,
        model=routing.model,
        effort=resolve_effort(EXPAND_EPIC_CHILDREN),
        reason=routing.reason,
    )
    log.info("epic expansion planned repository=%s epic=%d model=%s", repository, epic_number, plan.model)
    return plan
