"""Planner stage orchestration (CR-4, #1285): dispatch, receive, approve, file.

Every GitHub read/write and the agent dispatch are injected (``PlanDeps``) so the
whole flow — prompt, invalid plan, re-prompt, valid plan, approval, filing — runs in
tests against fakes. Routes in ``routers/code_request_plans.py`` only translate
``PlanningError`` into HTTP statuses.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from code_requests.model import CodeRequest, CodeRequestState
from code_requests.plan import PlanDraft
from code_requests.plan_filing import Fetch, Write, file_plan
from code_requests.plan_store import PlanSessionStore
from code_requests.plan_validator import validate_plan
from code_requests.planner import (
    PLAN_COMMENT_MARKER,
    PlanningSession,
    PlanningStatus,
    build_planner_prompt,
    receive_plan,
)
from code_requests.profiles import AgentProfile, AgentProfileStore
from code_requests.store import CodeRequestStore

log = logging.getLogger("dashboard.code_requests.plan")

Dispatch = Callable[..., Awaitable[tuple[int, str]]]


class PlanningError(Exception):
    """A planning request that cannot proceed; ``status`` is the HTTP status to report."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class PlanDeps:
    """Everything the planner stage touches outside this module."""

    requests: CodeRequestStore
    profiles: AgentProfileStore
    sessions: PlanSessionStore
    org: str
    fetch: Fetch
    write: Write
    dispatch: Dispatch


async def _load(deps: PlanDeps, request_id: str) -> CodeRequest:
    request = await deps.requests.get(request_id)
    if request is None:
        raise PlanningError(404, f"Code Request {request_id!r} not found")
    return request


def _planner_profile(deps: PlanDeps, request: CodeRequest) -> AgentProfile:
    profile = deps.profiles.get(request.planner_profile_id) if request.planner_profile_id else None
    return profile or deps.profiles.get_default_or_fallback(role="planner")


def _session(deps: PlanDeps, request_id: str) -> PlanningSession:
    session = deps.sessions.get(request_id)
    if session is None:
        raise PlanningError(404, f"planning has not started for {request_id!r}")
    return session


async def fetch_repo_rules(fetch: Fetch, org: str, repository: str) -> str:
    """The target repository's AGENTS.md and CLAUDE.md, fetched at planning time."""
    parts: list[str] = []
    for name in ("AGENTS.md", "CLAUDE.md"):
        try:
            data = await fetch(f"/repos/{org}/{repository}/contents/{name}")
        except Exception as exc:  # noqa: BLE001 - a missing rules file is normal
            log.debug("no %s in %s: %s", name, repository, exc)
            continue
        if isinstance(data, dict) and data.get("encoding") == "base64":
            parts.append(f"### {name}\n\n" + base64.b64decode(str(data.get("content", ""))).decode("utf-8", "replace"))
    return "\n\n".join(parts)


async def _dispatch(deps: PlanDeps, request: CodeRequest, session: PlanningSession, principal: str) -> PlanningSession:
    profile = _planner_profile(deps, request)
    rules = await fetch_repo_rules(deps.fetch, deps.org, request.repository)
    prompt = build_planner_prompt(request, org=deps.org, repo_rules=rules, errors=session.errors or None)
    code, stderr = await deps.dispatch(
        request.repository,
        request.branch,
        profile.provider,
        prompt,
        model=profile.model,
        effort=profile.effort,
        principal=principal,
        budget=profile.budget,
        profile_id=profile.id,
        standards=list(profile.standards),
    )
    if code != 0:
        raise PlanningError(502, f"planner dispatch failed: {stderr.strip()[:300]}")
    updated = session.model_copy(update={"attempts": session.attempts + 1, "status": PlanningStatus.AWAITING_PLAN})
    return deps.sessions.save(updated)


async def start_planning(deps: PlanDeps, request_id: str, *, principal: str) -> PlanningSession:
    """Dispatch the planner for a Code Request in ``planning``."""
    request = await _load(deps, request_id)
    if request.state is not CodeRequestState.PLANNING:
        raise PlanningError(409, f"Code Request is '{request.state.value}', planning needs 'planning'")
    if request.issue_number is None:
        raise PlanningError(409, "Code Request has no GitHub issue to receive the plan")
    existing = deps.sessions.get(request.id)
    if existing is not None and existing.status is not PlanningStatus.FAILED:
        raise PlanningError(409, f"planning already {existing.status.value} for {request.id}")
    return await _dispatch(deps, request, PlanningSession(request_id=request.id), principal)


async def _file(deps: PlanDeps, request: CodeRequest, session: PlanningSession, actor: str) -> PlanningSession:
    assert session.draft is not None, "only a validated draft is filed"
    try:
        await file_plan(request, session.draft, session.filing, org=deps.org, fetch=deps.fetch, write=deps.write)
    finally:
        deps.sessions.save(session)  # keep partial progress so a retry never duplicates
    session.status = PlanningStatus.FILED
    deps.sessions.save(session)
    epic = session.filing.epic_url or f"{deps.org}/{request.repository}#{session.filing.epic_number}"
    planned = await deps.requests.transition(
        request.id, CodeRequestState.PLANNED, actor=actor, reason=f"plan filed as {epic}"
    )
    await deps.requests.save(planned.model_copy(update={"plan_epic": epic}))
    return session


async def submit_plan(deps: PlanDeps, request_id: str, output: str, *, actor: str) -> PlanningSession:
    """Validate planner output and act on it: draft, file, re-prompt or fail."""
    request = await _load(deps, request_id)
    session = _session(deps, request.id)
    requires_approval = _planner_profile(deps, request).approval_gates.get("plan_requires_approval", True)
    try:
        session, action = receive_plan(
            session, output, repository=request.repository, requires_approval=requires_approval
        )
    except ValueError as exc:
        raise PlanningError(409, str(exc)) from exc
    deps.sessions.save(session)
    if action == "reprompt":
        return await _dispatch(deps, request, session, actor)
    if action == "fail":
        reasons = "; ".join(session.errors)[:900]
        await deps.requests.transition(
            request.id, CodeRequestState.FAILED, actor=actor, reason=f"planner output rejected: {reasons}"
        )
    if action == "file":
        return await _file(deps, request, session, actor)
    return session


async def ingest_plan_comment(deps: PlanDeps, request_id: str, *, actor: str) -> PlanningSession:
    """Read the newest ``<!-- plan:v1 -->`` comment on the Code Request issue and submit it."""
    request = await _load(deps, request_id)
    session = _session(deps, request.id)
    comments = await deps.fetch(
        f"/repos/{deps.org}/{request.repository}/issues/{request.issue_number}/comments?per_page=100"
    )
    plans = [
        c
        for c in comments or []
        if isinstance(c, dict) and str(c.get("body", "")).lstrip().startswith(PLAN_COMMENT_MARKER)
    ]
    if not plans:
        raise PlanningError(404, "no plan comment found on the Code Request issue")
    latest = plans[-1]
    if latest.get("id") == session.last_comment_id:
        raise PlanningError(409, "the latest plan comment was already processed")
    deps.sessions.save(session.model_copy(update={"last_comment_id": latest.get("id")}))
    return await submit_plan(deps, request.id, str(latest["body"]), actor=actor)


async def edit_draft(deps: PlanDeps, request_id: str, plan: dict[str, Any]) -> PlanningSession:
    """Replace the draft with an operator's edited plan, re-validated in full."""
    request = await _load(deps, request_id)
    session = _session(deps, request.id)
    if session.status is not PlanningStatus.DRAFT:
        raise PlanningError(409, f"only a draft can be edited (status {session.status.value})")
    try:
        draft = PlanDraft.model_validate(plan)
    except ValueError as exc:
        raise PlanningError(422, str(exc)) from exc
    errors = validate_plan(draft, repository=request.repository)
    if errors:
        raise PlanningError(422, "; ".join(errors))
    return deps.sessions.save(session.model_copy(update={"draft": draft, "errors": []}))


async def approve_plan(deps: PlanDeps, request_id: str, *, actor: str) -> PlanningSession:
    """Operator approval: file the draft on GitHub and move the request to ``planned``."""
    request = await _load(deps, request_id)
    session = _session(deps, request.id)
    if session.status is not PlanningStatus.DRAFT:
        raise PlanningError(409, f"only a draft can be approved (status {session.status.value})")
    return await _file(deps, request, session, actor)
