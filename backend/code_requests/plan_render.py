"""Markdown rendering for planned issues and their turnover documents (CR-4, #1285).

The dashboard, not the planner, writes each turnover document: Identity comes from
facts the dashboard knows (repository, base branch, baseline SHA, governing issue);
the planner contributes the objective, decisions, constraints and next steps. The
result must pass the fleet handoff rules (``handoff_rules``).
"""

from __future__ import annotations

from dataclasses import dataclass

from code_requests.plan import PlanChild, PlanDraft, compute_waves

TURNOVER_MARKER = "<!-- turnover:v1 -->"
PLAN_EPIC_MARKER = "<!-- plan-epic:v1 -->"


@dataclass(frozen=True)
class TurnoverIdentity:
    """Facts the dashboard fills into a turnover document's Identity section."""

    repository: str
    base_branch: str
    baseline_sha: str
    governing_issue: str


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- None."


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{i}. {item}" for i, item in enumerate(items, start=1))


def render_turnover(child: PlanChild, identity: TurnoverIdentity) -> str:
    """Render the pre-filled turnover document for one child issue."""
    commands = "\n".join(f"- `{v.command}` → {v.expect}" for v in child.validation_commands)
    return f"""{TURNOVER_MARKER}
# Implementation Handoff — {child.title}

## Identity

- Repository: {identity.repository}
- Working directory: not started (executor creates its own worktree)
- Branch: not created (base branch {identity.base_branch})
- Baseline commit: {identity.baseline_sha}
- Implementation commit: SELF
- Pull request: not created
- Governing issue/epic: {identity.governing_issue}

## Objective and status

{child.objective}

Status: planned, not started.

## Files and decisions

Allowed paths:
{_bullets(child.file_scope.allowed)}

Must not touch:
{_bullets(child.file_scope.forbidden)}

Key decisions and constraints:
{_bullets(child.key_decisions)}

## Validation

{commands}

## Blockers and risks

Depends on: {", ".join(child.dependencies) if child.dependencies else "nothing"}.

## Next steps

{_numbered(child.next_steps)}

## Change log

- `SELF` — Turnover document authored by the CR-4 planner.
"""


def render_child_body(child: PlanChild, *, epic_ref: str, dependency_refs: list[str]) -> str:
    """Render a child issue body with every required section."""
    commands = "\n".join(f"- `{v.command}` — expect: {v.expect}" for v in child.validation_commands)
    return f"""Part of {epic_ref}.

## Objective

{child.objective}

## Execution instructions

{_numbered(child.execution_instructions)}

## File scope

May touch:
{_bullets(child.file_scope.allowed)}

Must not touch:
{_bullets(child.file_scope.forbidden)}

## Acceptance criteria

{_bullets(child.acceptance_criteria)}

## Validation commands

{commands}

## Dependencies

{_bullets(dependency_refs)}

## Out of scope

{_bullets(child.out_of_scope)}

## Tier hint

`{child.tier}` · `complexity:{child.complexity}` · task class `{child.task_class}`.
The turnover document is the first comment.
"""


def render_epic_body(plan: PlanDraft, *, code_request_ref: str, extra_duplicates: list[str]) -> str:
    """Render the plan epic: summary, dependency waves, acceptance criteria, duplicates."""
    titles = {c.key: c.title for c in plan.children}
    waves = "\n".join(
        f"{i}. " + "; ".join(titles[k] for k in wave) for i, wave in enumerate(compute_waves(plan.children), start=1)
    )
    cited = [f"#{d.number} {d.title} — {d.reason}".strip() for d in plan.duplicates]
    return f"""{PLAN_EPIC_MARKER}
Plan for {code_request_ref}.

## Summary

{plan.epic.summary}

## Dependency waves

{waves}

## Acceptance criteria

{_bullets(plan.epic.acceptance_criteria)}

## Possible duplicates checked

{_bullets(cited + extra_duplicates)}
"""
