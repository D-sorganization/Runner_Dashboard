"""Planner stage state machine for Code Requests (CR-4, #1285).

A strong-tier planner agent is dispatched through the CR-3 envelope. Dispatch is
fire-and-forget, so the plan comes back later — posted to
``POST /api/code-requests/{id}/plan`` or as an issue comment starting with
``PLAN_COMMENT_MARKER`` — and ``receive_plan`` decides what happens next:

- valid, approval required  -> ``draft`` (nothing filed until an operator approves)
- valid, no approval needed -> ``file``
- invalid, retries left     -> ``reprompt`` (the validator errors go back to the planner)
- invalid, retries spent    -> ``fail`` (the Code Request moves to ``failed``)

``receive_plan`` is pure; the router performs the dispatch, filing and transitions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from code_requests.model import CodeRequest
from code_requests.plan import COMPLEXITIES, TASK_CLASS_LABELS, TIER_LABELS, PlanDraft
from code_requests.plan_validator import validate_plan_output
from pydantic import BaseModel, Field

MAX_REPROMPTS = 2
PLAN_COMMENT_MARKER = "<!-- plan:v1 -->"
REPO_RULES_CHAR_LIMIT = 12_000

NextAction = Literal["draft", "file", "reprompt", "fail"]


class PlanningStatus(StrEnum):
    AWAITING_PLAN = "awaiting_plan"
    DRAFT = "draft"
    FILED = "filed"
    FAILED = "failed"


class FilingProgress(BaseModel):
    """What has already been created on GitHub, so a retried approval never duplicates."""

    epic_number: int | None = None
    epic_url: str | None = None
    children: dict[str, int] = Field(default_factory=dict)
    child_ids: dict[str, int] = Field(default_factory=dict)
    turnovers_posted: list[str] = Field(default_factory=list)
    linked: list[str] = Field(default_factory=list)


class PlanningSession(BaseModel):
    """Durable planning state for one Code Request."""

    request_id: str
    status: PlanningStatus = PlanningStatus.AWAITING_PLAN
    attempts: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)
    draft: PlanDraft | None = None
    last_comment_id: int | None = None
    filing: FilingProgress = Field(default_factory=FilingProgress)
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


def _output_contract() -> str:
    example = {
        "epic": {"title": "...", "summary": "...", "acceptance_criteria": ["..."]},
        "children": [
            {
                "key": "short-id",
                "title": "...",
                "objective": "...",
                "execution_instructions": ["ordered, concrete step naming file paths"],
                "file_scope": {"allowed": ["path/"], "forbidden": ["path/"]},
                "acceptance_criteria": ["..."],
                "validation_commands": [{"command": "exact command", "expect": "expected outcome"}],
                "dependencies": ["another-key", "#123"],
                "out_of_scope": ["..."],
                "tier": "tier:cli",
                "complexity": "small",
                "task_class": "feature",
                "key_decisions": ["decision or constraint the executor must keep"],
                "next_steps": ["first executable step"],
            }
        ],
        "duplicates": [{"number": 123, "title": "...", "reason": "why it is or is not a duplicate"}],
    }
    return (
        "Return exactly one JSON object in a ```json fenced block, shaped like:\n"
        f"```json\n{json.dumps(example, indent=2)}\n```\n"
        f"- tier: one of {sorted(TIER_LABELS)}; complexity: one of {sorted(COMPLEXITIES)}; "
        f"task_class: one of {sorted(TASK_CLASS_LABELS)}.\n"
        "- dependencies: sibling child keys, or existing issue refs like #123. No cycles.\n"
        "- Never write angle-bracket placeholders or credentials; the dashboard turns key_decisions "
        "and next_steps into each child's turnover document and rejects placeholders.\n"
        "- Before planning, search the target repository's open issues for duplicates "
        "(`gh search issues --repo OWNER/REPO --state open <keywords>`) and cite every candidate in `duplicates`."
    )


def build_planner_prompt(request: CodeRequest, *, org: str, repo_rules: str, errors: list[str] | None = None) -> str:
    """Compose the planner prompt; ``errors`` makes it a re-prompt."""
    assert request.issue_number is not None, "planning needs the Code Request's GitHub issue"
    rules = repo_rules[:REPO_RULES_CHAR_LIMIT] or "(the repository has no AGENTS.md or CLAUDE.md)"
    parts = [
        f"You are the planner for Code Request {request.id} in {org}/{request.repository}.",
        "Turn the request below into one plan epic and execution-ready child issues that cheaper "
        "agents can implement without reconstructing context. Do not implement anything.",
        f"## Request\n\nTitle: {request.title}\n\n{request.prompt}",
        f"## Target repository rules\n\n{rules}",
        f"## Output contract\n\n{_output_contract()}",
        "## How to submit\n\n"
        f"Post one comment on {org}/{request.repository}#{request.issue_number} whose first line is "
        f"`{PLAN_COMMENT_MARKER}`, followed by the fenced JSON plan. Do not open issues yourself.",
    ]
    if errors:
        listed = "\n".join(f"- {e}" for e in errors)
        parts.append(
            f"## Your previous plan was rejected\n\nFix every problem below and resubmit the whole plan:\n{listed}"
        )
    return "\n\n".join(parts)


def receive_plan(
    session: PlanningSession, text: str, *, repository: str, requires_approval: bool
) -> tuple[PlanningSession, NextAction]:
    """Validate a submitted plan and decide the next step (pure)."""
    if session.status is not PlanningStatus.AWAITING_PLAN:
        raise ValueError(f"no plan is awaited for {session.request_id} (status {session.status.value})")
    plan, errors = validate_plan_output(text, repository=repository)
    updated = session.model_copy(deep=True)
    updated.updated_at = datetime.now(UTC).isoformat()
    updated.errors = errors
    if plan is not None:
        updated.draft = plan
        updated.status = PlanningStatus.DRAFT
        return updated, "draft" if requires_approval else "file"
    if updated.attempts <= MAX_REPROMPTS:
        return updated, "reprompt"
    updated.status = PlanningStatus.FAILED
    return updated, "fail"
