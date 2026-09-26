"""One way to request work (SC-G5-1, #1497).

``submit_request`` validates a :class:`WorkRequest` for its kind, records it as a Work
Item plus an ActionProposal on the requester's own *Requests* thread, and executes it
through the action registry, so quotas, forwarding and audit happen once in the
registered action (for ``staff.dispatch``: the shared dispatch service, #1487).

Approval follows the registry risk (#1485): a requester who may approve the action runs
it at once; anyone else leaves an ``approval_required`` proposal for an approver.
Each request kind is a thin adapter from the request body onto one registered action.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

from code_requests.model import STANDARDS_INJECTION
from identity import Principal, format_caller, principal_has_scope
from pydantic import BaseModel, ConfigDict, Field, field_validator
from staff.actions import (
    ACTION_REGISTRY,
    ActionContext,
    ActionError,
    ActionResult,
    check_approval_policy,
    execute_proposal,
    is_owner,
    registered_risk,
)

if TYPE_CHECKING:
    from staff.conversation_models import ThreadRecord
    from staff.conversations import ConversationStore
    from staff.work_items import WorkItemStore

log = logging.getLogger("dashboard.staff.work_requests")

REQUESTS_THREAD_PREFIX = "th_req_"

# Most issues or PRs one bulk request may target; the legacy bulk dispatch cap.
MAX_BULK_TARGETS = 100

# How a failed action surfaces over HTTP; anything unlisted is an upstream failure.
_STATUS_BY_FAILURE_CLASS = {
    "invalid_params": 422,
    "permission_denied": 403,
    "rate_limited": 429,
    "peer_unreachable": 503,
    "bridge_unavailable": 503,
}


class RequestTarget(BaseModel):
    """What the work is about. Each kind accepts only the fields it can act on."""

    model_config = ConfigDict(extra="forbid")

    repo: str = ""
    issue: int | None = Field(default=None, ge=1)
    pr: int | None = Field(default=None, ge=1)
    run_id: int | None = Field(default=None, ge=1)
    ref: str = ""
    issues: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list, max_length=MAX_BULK_TARGETS)
    prs: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list, max_length=MAX_BULK_TARGETS)

    @field_validator("issues", "prs")
    @classmethod
    def _unique(cls, numbers: list[int]) -> list[int]:
        if len(set(numbers)) != len(numbers):
            raise ValueError("lists a target more than once")
        return numbers


class WorkRequest(BaseModel):
    """The body of ``POST /api/v1/staff/requests``."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(description="A request kind; see REQUEST_KINDS")
    role: str | None = None
    provider: str | None = None
    model: str | None = None
    machine: str = "local"
    target: RequestTarget = Field(default_factory=RequestTarget)
    prompt: str = Field(default="", max_length=20000)
    dry_run: bool = False
    profile_id: str | None = None
    force: bool | None = None
    approved_by: str | None = None
    # code_request.dispatch settings; unset ones take the agent profile's defaults (#1501).
    effort: str | None = None
    standards: list[str] | None = None
    budget: dict[str, Any] | None = None


class RequestRejectedError(ValueError):
    """The request is invalid for its kind; nothing was recorded."""


@dataclass(frozen=True)
class RequestKind:
    """A request kind: the registered action it runs and how its params are built."""

    action: str
    build_params: Callable[[WorkRequest], dict[str, Any]]


@dataclass(frozen=True)
class RequestOutcome:
    """Pre: ``state`` is planned | executed | approval_required | failed.
    ``http_status`` is what the route returns (``failed`` raises it with ``body``)."""

    state: str
    http_status: int
    body: dict[str, Any]


def _staff_dispatch_params(req: WorkRequest) -> dict[str, Any]:
    t = req.target
    if not (req.role or "").strip():
        raise RequestRejectedError("kind 'staff.dispatch' needs a role")
    if t.issues or t.prs:
        raise RequestRejectedError("kind 'staff.dispatch' takes one issue or pr, not bulk targets")
    if t.run_id is not None or t.ref or req.profile_id:
        raise RequestRejectedError("kind 'staff.dispatch' does not accept run_id, ref or profile_id")
    if t.issue and t.pr:
        raise RequestRejectedError("choose an issue or a pr, not both")
    if not (req.prompt.strip() or t.issue or t.pr):
        raise RequestRejectedError("kind 'staff.dispatch' needs a prompt, an issue or a pr")
    params = {
        "role": req.role,
        "provider": req.provider,
        "model": req.model,
        "machine": req.machine,
        "repo": t.repo,
        "issue": t.issue,
        "pr": t.pr,
        "prompt": req.prompt,
    }
    return {k: v for k, v in params.items() if v not in (None, "")}


def _ci_remediate_params(req: WorkRequest) -> dict[str, Any]:
    t = req.target
    if t.issue is not None or t.pr is not None or t.issues or t.prs:
        raise RequestRejectedError("kind 'ci.remediate' does not accept issue or pr targets")
    if t.ref or req.profile_id or req.role:
        raise RequestRejectedError("kind 'ci.remediate' does not accept ref, profile_id or role")
    if not (t.repo or "").strip():
        raise RequestRejectedError("kind 'ci.remediate' needs a repo")
    if t.run_id is None and not req.prompt.strip():
        raise RequestRejectedError("kind 'ci.remediate' needs a run_id or a prompt")
    params = {
        "repo": t.repo,
        "run_id": t.run_id,
        "provider": req.provider,
        "prompt": req.prompt,
        "machine": req.machine,
    }
    return {k: v for k, v in params.items() if v not in (None, "")}


def _issue_act_params(req: WorkRequest) -> dict[str, Any]:
    t = req.target
    if t.pr is not None or t.prs:
        raise RequestRejectedError("kind 'issue.act' does not accept pr targets")
    if t.run_id is not None or t.ref or req.profile_id:
        raise RequestRejectedError("kind 'issue.act' does not accept run_id, ref or profile_id")
    if not (t.repo or "").strip():
        raise RequestRejectedError("kind 'issue.act' needs a repo")
    if t.issue is None and not t.issues:
        raise RequestRejectedError("kind 'issue.act' needs an issue target")
    params = {
        "repo": t.repo,
        "issue": t.issue or (t.issues[0] if t.issues else None),
        "issues": t.issues,
        "provider": req.provider,
        "model": req.model,
        "prompt": req.prompt,
        "role": req.role,
        "machine": req.machine,
        "force": req.force,
        "approved_by": req.approved_by,
    }
    return {k: v for k, v in params.items() if v not in (None, "", [])}


def _pr_act_params(req: WorkRequest) -> dict[str, Any]:
    t = req.target
    if t.issue is not None or t.issues:
        raise RequestRejectedError("kind 'pr.act' does not accept issue targets")
    if t.run_id is not None or t.ref or req.profile_id:
        raise RequestRejectedError("kind 'pr.act' does not accept run_id, ref or profile_id")
    if not (t.repo or "").strip():
        raise RequestRejectedError("kind 'pr.act' needs a repo")
    if t.pr is None and not t.prs:
        raise RequestRejectedError("kind 'pr.act' needs a pr target")
    params = {
        "repo": t.repo,
        "pr": t.pr or (t.prs[0] if t.prs else None),
        "prs": t.prs,
        "provider": req.provider,
        "model": req.model,
        "prompt": req.prompt,
        "role": req.role,
        "machine": req.machine,
        "force": req.force,
        "approved_by": req.approved_by,
    }
    return {k: v for k, v in params.items() if v not in (None, "", [])}


def _code_request_dispatch_params(req: WorkRequest) -> dict[str, Any]:
    t = req.target
    if t.issue is not None or t.pr is not None or t.issues or t.prs or t.run_id is not None:
        raise RequestRejectedError("kind 'code_request.dispatch' does not accept issue, pr, or run_id")
    if not (t.repo or "").strip():
        raise RequestRejectedError("kind 'code_request.dispatch' needs a repo")
    if not req.prompt.strip() and not (t.ref or "").strip():
        raise RequestRejectedError("kind 'code_request.dispatch' needs a prompt or a ref")
    unknown = sorted(set(req.standards or []) - set(STANDARDS_INJECTION))
    if unknown:
        raise RequestRejectedError(f"kind 'code_request.dispatch' has unknown standards: {', '.join(unknown)}")
    params = {
        "repo": t.repo,
        "ref": t.ref,
        "provider": req.provider,
        "model": req.model,
        "profile_id": req.profile_id,
        "prompt": req.prompt,
        "effort": req.effort,
        "standards": req.standards,
        "budget": req.budget,
    }
    # An explicit empty standards list means "none", so it is kept (#1501).
    return {k: v for k, v in params.items() if v not in (None, "") or k == "standards" and v is not None}


def _assessment_run_params(req: WorkRequest) -> dict[str, Any]:
    t = req.target
    if t.issue is not None or t.pr is not None or t.issues or t.prs or t.run_id is not None or t.ref:
        raise RequestRejectedError("kind 'assessment.run' only accepts repo")
    if req.profile_id or req.role:
        raise RequestRejectedError("kind 'assessment.run' does not accept profile_id or role")
    if not (t.repo or "").strip():
        raise RequestRejectedError("kind 'assessment.run' needs a repo")
    params = {
        "repo": t.repo,
        "provider": req.provider,
        "prompt": req.prompt,
    }
    return {k: v for k, v in params.items() if v not in (None, "")}


REQUEST_KINDS: dict[str, RequestKind] = {
    "staff.dispatch": RequestKind(action="staff.dispatch", build_params=_staff_dispatch_params),
    "ci.remediate": RequestKind(action="ci.remediate", build_params=_ci_remediate_params),
    "issue.act": RequestKind(action="issue.act", build_params=_issue_act_params),
    "pr.act": RequestKind(action="pr.act", build_params=_pr_act_params),
    "code_request.dispatch": RequestKind(action="code_request.dispatch", build_params=_code_request_dispatch_params),
    "assessment.run": RequestKind(action="assessment.run", build_params=_assessment_run_params),
}


def validate_request(req: WorkRequest, caller: Principal) -> tuple[RequestKind, dict[str, Any]]:
    """Resolve the kind and build its params, or raise before anything is recorded.

    Raises :class:`RequestRejectedError` (422) or :class:`PermissionError` (403).
    """
    kind = REQUEST_KINDS.get(req.kind)
    if kind is None:
        raise RequestRejectedError(f"unknown kind '{req.kind}' (supported: {', '.join(sorted(REQUEST_KINDS))})")
    action = ACTION_REGISTRY.get(kind.action)
    assert action is not None, f"request kind {req.kind} maps to unregistered action {kind.action}"  # noqa: S101
    if not is_owner(caller) and not principal_has_scope(caller, action.required_scope):
        raise PermissionError(f"requesting '{req.kind}' needs the '{action.required_scope}' scope")
    return kind, kind.build_params(req)


def requests_thread(store: ConversationStore, caller: Principal) -> ThreadRecord:
    """The caller's own *Requests* thread (direct, with ``barb``), created on first use."""
    tid = REQUESTS_THREAD_PREFIX + hashlib.sha256(caller.id.encode()).hexdigest()[:12]
    existing = store.get_thread(tid)
    if existing is not None:
        return existing
    try:
        return store.create_thread(
            title=f"Requests: {caller.name or caller.id}",
            kind="direct",
            participants=["barb", caller.id],
            created_by=format_caller(caller),
            thread_id=tid,
            meta={"requests_for": caller.id},
        )
    except sqlite3.IntegrityError:  # a concurrent first request created it
        created = store.get_thread(tid)
        assert created is not None  # noqa: S101
        return created


def _failure_outcome(base: dict[str, Any], res: ActionResult) -> RequestOutcome:
    status = _STATUS_BY_FAILURE_CLASS.get(res.failure_class or "", 502)
    body = {**base, "state": "failed", "error": res.error, "failure_class": res.failure_class}
    return RequestOutcome("failed", status, body)


def _plan(req: WorkRequest, kind: RequestKind, params: dict[str, Any], caller: Principal) -> RequestOutcome:
    action = ACTION_REGISTRY.get(kind.action)
    assert action is not None  # noqa: S101
    base = {"kind": req.kind, "action": kind.action, "risk": action.risk_class, "params": params}
    res = action.executor(params, ActionContext(caller=caller, dry_run=True))
    if not res.success:
        return _failure_outcome(base, res)
    return RequestOutcome("planned", 200, {**base, "state": "planned", "plan": res.result})


def submit_request(
    req: WorkRequest,
    caller: Principal,
    store: ConversationStore,
    work_items: WorkItemStore,
) -> RequestOutcome:
    """Validate, record and execute (or preview) one work request.

    Pre: runs in an anyio worker thread (executors reach the loop via ``run_on_loop``).
    Post: a rejected request records nothing; a dry run records nothing; otherwise a
    Work Item and a proposal exist on the caller's Requests thread, the work item links
    the run on success and is ``blocked`` on failure, and every state change is audited
    by the stores and the action.
    """
    kind, params = validate_request(req, caller)
    if req.dry_run:
        return _plan(req, kind, params, caller)

    principal = format_caller(caller)
    thread = requests_thread(store, caller)
    msg = store.add_message(
        thread_id=thread.id,
        author_kind="user",
        author=principal,
        body_md=f"**Request** `{req.kind}`\n\n{req.prompt}".rstrip(),
        meta={"request": req.model_dump()},
    )
    targets_label = ""
    if req.target.issues:
        targets_label = f" #{', #'.join(str(n) for n in req.target.issues)}"
    elif req.target.issue:
        targets_label = f" #{req.target.issue}"
    elif req.target.prs:
        targets_label = f" PR #{', PR #'.join(str(n) for n in req.target.prs)}"
    elif req.target.pr:
        targets_label = f" PR #{req.target.pr}"
    elif req.target.run_id:
        targets_label = f" run {req.target.run_id}"

    title = f"[{req.kind}] {req.target.repo or req.role or ''}{targets_label} {req.prompt[:60]}".strip()
    wi = work_items.create_work_item(
        title=title[:200], requested_by=principal, thread_id=thread.id, owner_role=str(params.get("role") or "")
    )
    params = {**params, "work_item_id": wi.id}
    prop = store.create_proposal(
        message_id=msg.id,
        thread_id=thread.id,
        action=kind.action,
        params=params,
        risk=registered_risk(kind.action),
        principal=principal,
    )
    base = {
        "kind": req.kind,
        "action": kind.action,
        "risk": prop.risk,
        "thread_id": thread.id,
        "message_id": msg.id,
        "work_item_id": wi.id,
        "proposal_id": prop.id,
    }
    try:
        check_approval_policy(ACTION_REGISTRY.get(kind.action), prop, caller)
    except PermissionError as exc:
        log.info("staff request %s for %s awaits approval: %s", prop.id, principal, exc)
        return RequestOutcome("approval_required", 202, {**base, "state": "approval_required", "approval": str(exc)})

    try:
        res = execute_proposal(prop.id, approver=caller, store=store, approve=True)
    except (ActionError, PermissionError) as exc:
        res = ActionResult(success=False, error=str(exc), failure_class="permission_denied")
    if not res.success:
        work_items.transition_state(wi.id, "blocked", actor=principal, reason=res.error or "request failed")
        return _failure_outcome(base, res)
    if res.run_id:
        work_items.add_link(wi.id, "runs", res.run_id)
    return RequestOutcome("executed", 201, {**base, "state": "executed", "run_id": res.run_id, "result": res.result})
