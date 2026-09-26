"""Semantic validation of planner output (CR-4, #1285).

``validate_plan_output`` is the single gate between planner text and anything the
dashboard stores or files. It returns human-readable errors, each naming the child
it concerns, so they can be sent back to the planner verbatim on a re-prompt.
"""

from __future__ import annotations

from code_requests.handoff_rules import handoff_findings
from code_requests.plan import (
    COMPLEXITIES,
    TASK_CLASS_LABELS,
    TIER_LABELS,
    DependencyCycleError,
    PlanDraft,
    PlanFormatError,
    compute_waves,
    extract_plan_json,
    parse_plan_output,
)
from code_requests.plan_render import TurnoverIdentity, render_turnover
from pydantic import ValidationError

# Provisional identity used to validate turnover documents before issues exist; the
# real numbers and baseline SHA are filled in at filing time. No angle brackets: the
# handoff rules treat them as unedited placeholders.
_DRAFT_SHA = "0000000"


def _describe(exc: ValidationError, plan_data: object) -> list[str]:
    children = plan_data.get("children") if isinstance(plan_data, dict) else None
    messages: list[str] = []
    for err in exc.errors():
        loc = list(err["loc"])
        where = ".".join(str(p) for p in loc)
        if len(loc) >= 2 and loc[0] == "children" and isinstance(loc[1], int) and isinstance(children, list):
            item = children[loc[1]] if loc[1] < len(children) else None
            key = item.get("key") if isinstance(item, dict) else None
            field = ".".join(str(p) for p in loc[2:]) or "child"
            where = f"child '{key or loc[1]}' field '{field}'"
        messages.append(f"{where}: {err['msg']}")
    return messages


def validate_plan(plan: PlanDraft, *, repository: str) -> list[str]:
    """Return every semantic problem with a structurally valid plan (empty when valid)."""
    errors: list[str] = []
    keys = [c.key for c in plan.children]
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            errors.append(f"duplicate child key '{key}'")
        seen.add(key)

    for c in plan.children:
        if c.tier not in TIER_LABELS:
            errors.append(f"child '{c.key}': tier '{c.tier}' must be one of {sorted(TIER_LABELS)}")
        if c.complexity not in COMPLEXITIES:
            errors.append(f"child '{c.key}': complexity '{c.complexity}' must be one of {sorted(COMPLEXITIES)}")
        if c.task_class not in TASK_CLASS_LABELS:
            errors.append(f"child '{c.key}': task_class '{c.task_class}' must be one of {sorted(TASK_CLASS_LABELS)}")
        for dep in c.local_dependencies():
            if dep not in keys:
                errors.append(f"child '{c.key}': dependency '{dep}' is neither a sibling key nor an issue ref")

    if not errors:
        try:
            compute_waves(plan.children)
        except DependencyCycleError as exc:
            errors.append(str(exc))

    identity = TurnoverIdentity(
        repository=repository, base_branch="main", baseline_sha=_DRAFT_SHA, governing_issue="assigned at filing"
    )
    for c in plan.children:
        for finding in handoff_findings(render_turnover(c, identity)):
            errors.append(f"child '{c.key}' turnover: {finding}")
    return errors


def validate_plan_output(text: str, *, repository: str) -> tuple[PlanDraft | None, list[str]]:
    """Parse and validate planner text. Returns (plan, []) when valid, else (None | plan, errors)."""
    try:
        plan = parse_plan_output(text)
    except PlanFormatError as exc:
        return None, [str(exc)]
    except ValidationError as exc:
        return None, _describe(exc, extract_plan_json(text))
    errors = validate_plan(plan, repository=repository)
    return (plan if not errors else None), errors
