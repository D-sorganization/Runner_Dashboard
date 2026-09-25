"""Code Request data model and serialization (CR-2, issue #1282).

Defines Pydantic models for Code Requests, lifecycle states, board routes,
requesters, and audit events, along with lossless front-matter serialization
and deserialization for durable GitHub issue records.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import yaml
from pydantic import BaseModel, Field

# Regex matching fenced YAML front-matter block at the top of an issue body
FRONT_MATTER_PATTERN = re.compile(r"^```yaml\s*\n(.*?)\n```(?:\r?\n|$)(.*)", re.DOTALL)

STANDARDS_INJECTION: dict[str, str] = {
    "tdd": (
        "Use Test-Driven Development: write failing tests first (RED), then minimal code to pass (GREEN),"
        " then refactor. Tests must pass before any PR."
    ),
    "dbc": (
        "Apply Design by Contract: validate inputs at boundaries, assert internal invariants,"
        " document pre/postconditions in docstrings."
    ),
    "dry": (
        "Apply DRY: extract shared logic into modules, eliminate duplication."
        " Three similar code blocks should become one shared function."
    ),
    "lod": (
        "Apply Law of Demeter: components talk to immediate neighbors only."
        " UI receives view models, not raw nested payloads."
    ),
    "security": (
        "Apply security-first: validate all inputs, avoid injection vulnerabilities,"
        " use parameterized queries, never log secrets."
    ),
    "docs": (
        "Document public APIs, non-obvious decisions, and architecture choices."
        " Prefer short clear docstrings over multi-paragraph ones."
    ),
}


class CodeRequestState(StrEnum):
    """Lifecycle states of a Code Request."""

    DRAFT = "draft"
    TRIAGE = "triage"
    BOARD_REVIEW = "board_review"
    PLANNING = "planning"
    PLANNED = "planned"
    EXECUTING = "executing"
    DONE = "done"
    FAILED = "failed"
    DEFERRED = "deferred"
    DECLINED = "declined"
    CANCELLED = "cancelled"


class BoardRoute(StrEnum):
    """Routing policy for Architecture Board review."""

    AUTO = "auto"
    FORCE_BOARD = "force_board"
    SKIP_BOARD = "skip_board"


class RequesterKind(StrEnum):
    """Kind of requester initiating the Code Request."""

    HUMAN = "human"
    AGENT = "agent"


class Requester(BaseModel):
    """Principal initiating the Code Request."""

    id: str
    kind: RequesterKind = RequesterKind.HUMAN


class CodeRequestAuditEvent(BaseModel):
    """Audit entry recorded on state transition."""

    actor: str
    from_state: CodeRequestState
    to_state: CodeRequestState
    reason: str
    timestamp: str
    override: bool = False


class CodeRequest(BaseModel):
    """Typed record of a Code Request."""

    id: str
    repository: str
    title: str = ""
    issue_number: int | None = None
    issue_url: str | None = None
    state: CodeRequestState = CodeRequestState.DRAFT
    prompt: str = ""
    requester: Requester
    planner_profile_id: str | None = None
    executor_profile_id: str | None = None
    board_route: BoardRoute = BoardRoute.AUTO
    board_proposal: str | None = None
    plan_epic: str | None = None
    branch: str = "main"
    standards: list[str] = Field(default_factory=list)
    audit_trail: list[CodeRequestAuditEvent] = Field(default_factory=list)
    created_at: str
    updated_at: str


def serialize_front_matter(request: CodeRequest) -> str:
    """Serialize the CodeRequest metadata into a fenced YAML front-matter block."""
    payload: dict[str, Any] = {
        "id": request.id,
        "repository": request.repository,
        "state": request.state.value,
        "board_route": request.board_route.value,
        "board_proposal": request.board_proposal,
        "plan_epic": request.plan_epic,
        "planner_profile_id": request.planner_profile_id,
        "executor_profile_id": request.executor_profile_id,
        "requester": {
            "id": request.requester.id,
            "kind": request.requester.kind.value,
        },
        "standards": list(request.standards),
        "branch": request.branch,
        "created_at": request.created_at,
        "updated_at": request.updated_at,
    }
    yaml_text = yaml.safe_dump(payload, sort_keys=False, default_flow_style=False).strip()
    return f"```yaml\n{yaml_text}\n```"


def serialize_issue_body(request: CodeRequest) -> str:
    """Serialize the full issue body including front-matter block and prompt."""
    fm = serialize_front_matter(request)
    prompt_section = f"## Prompt\n\n{request.prompt.strip()}" if request.prompt.strip() else ""
    if prompt_section:
        return f"{fm}\n\n{prompt_section}\n"
    return f"{fm}\n"


def parse_issue_body(body: str, *, default_id: str = "", default_repo: str = "") -> dict[str, Any]:
    """Parse front-matter and prompt from an issue body.

    Returns a dict with parsed front-matter attributes and extracted prompt.
    """
    match = FRONT_MATTER_PATTERN.match(body.strip())
    if not match:
        # No front matter: parse entire body or prompt heading
        prompt = body.strip()
        if "## Prompt" in prompt:
            prompt = prompt.split("## Prompt", 1)[1].strip()
        return {
            "id": default_id,
            "repository": default_repo,
            "prompt": prompt,
            "state": CodeRequestState.DRAFT.value,
        }

    raw_yaml, remainder = match.group(1), match.group(2)
    try:
        data = yaml.safe_load(raw_yaml)
        if not isinstance(data, dict):
            data = {}
    except Exception:  # noqa: BLE001
        data = {}

    prompt = remainder.strip()
    if "## Prompt" in prompt:
        prompt = prompt.split("## Prompt", 1)[1].strip()

    data.setdefault("id", default_id)
    data.setdefault("repository", default_repo)
    if prompt:
        data["prompt"] = prompt

    return data


def code_request_from_issue(
    issue: dict[str, Any],
    default_repo: str = "",
    audit_trail: list[CodeRequestAuditEvent] | None = None,
) -> CodeRequest:
    """Construct a CodeRequest instance from a GitHub issue dict and comments."""
    body = str(issue.get("body") or "")
    issue_number = issue.get("number")
    repo = default_repo
    if not repo:
        # Try extracting from repository_url: https://api.github.com/repos/:org/:repo
        repo_url = str(issue.get("repository_url") or "")
        if repo_url:
            repo = repo_url.rstrip("/").split("/")[-1]

    default_id = f"cr-{repo}-{issue_number}" if repo and issue_number else f"cr-{issue_number or 'unknown'}"
    parsed = parse_issue_body(body, default_id=default_id, default_repo=repo)

    # State from label or front matter
    state_str = parsed.get("state", CodeRequestState.DRAFT.value)
    labels = issue.get("labels", [])
    for lbl in labels:
        name = lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)
        if name.startswith("code-request:"):
            candidate = name.split(":", 1)[1]
            try:
                CodeRequestState(candidate)
                state_str = candidate
                break
            except ValueError:
                pass

    try:
        state = CodeRequestState(state_str)
    except ValueError:
        state = CodeRequestState.DRAFT

    # Requester
    req_data = parsed.get("requester")
    if isinstance(req_data, dict) and "id" in req_data:
        kind_str = req_data.get("kind", RequesterKind.HUMAN.value)
        try:
            req_kind = RequesterKind(kind_str)
        except ValueError:
            req_kind = RequesterKind.HUMAN
        requester = Requester(id=str(req_data["id"]), kind=req_kind)
    else:
        user_data = issue.get("user", {})
        user_login = user_data.get("login", "unknown") if isinstance(user_data, dict) else "unknown"
        user_type = user_data.get("type", "User") if isinstance(user_data, dict) else "User"
        requester = Requester(
            id=user_login,
            kind=RequesterKind.AGENT if user_type.lower() == "bot" else RequesterKind.HUMAN,
        )

    # Route
    route_str = parsed.get("board_route", BoardRoute.AUTO.value)
    try:
        board_route = BoardRoute(route_str)
    except ValueError:
        board_route = BoardRoute.AUTO

    created_at = parsed.get("created_at") or issue.get("created_at") or datetime.now(UTC).isoformat()
    updated_at = parsed.get("updated_at") or issue.get("updated_at") or created_at

    return CodeRequest(
        id=str(parsed.get("id") or default_id),
        repository=repo or default_repo or "Runner_Dashboard",
        title=str(issue.get("title") or f"Code Request: {parsed.get('prompt', '')[:40]}"),
        issue_number=issue_number,
        issue_url=issue.get("html_url"),
        state=state,
        prompt=str(parsed.get("prompt") or ""),
        requester=requester,
        planner_profile_id=parsed.get("planner_profile_id"),
        executor_profile_id=parsed.get("executor_profile_id"),
        board_route=board_route,
        board_proposal=parsed.get("board_proposal"),
        plan_epic=parsed.get("plan_epic"),
        branch=str(parsed.get("branch") or "main"),
        standards=list(parsed.get("standards") or []),
        audit_trail=list(audit_trail or []),
        created_at=created_at,
        updated_at=updated_at,
    )
