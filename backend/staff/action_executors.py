"""Standard action executors and verifiers for Staff Console (SC-B6, Issue #1313)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from identity import format_caller
from staff.audit import record_audit

log = logging.getLogger("dashboard.staff.action_executors")

DEFAULT_REVIEWER_ROLE = "code-reviewer"
FALLBACK_REVIEWER_ROLE = "fleet-critic"
CODE_REQUEST_OWNER_ROLE = "barb"
BOARD_PROPOSAL_ROLE = "board-secretary"

ACTION_DEFAULT_ROLES: tuple[str, ...] = (
    DEFAULT_REVIEWER_ROLE,
    CODE_REQUEST_OWNER_ROLE,
    BOARD_PROPOSAL_ROLE,
)


def validate_action_default_roles(raise_on_error: bool = False) -> list[str]:
    """Validate action default roles against the loaded staff roster.

    When a name does not resolve, logs a warning at runtime. If raise_on_error
    is True, raises ValueError so test suites can fail loudly.
    """
    from staff.roles import load_roles, roles_dir

    r_dir = roles_dir()
    if r_dir is None:
        log.debug("No staff roles directory located; skipping action default roles validation")
        return []

    try:
        roster = load_roles(r_dir)
    except Exception as exc:
        msg = f"Failed to load staff roles for validation: {exc}"
        log.warning(msg)
        if raise_on_error:
            raise ValueError(msg) from exc
        return [msg]

    errors: list[str] = []
    for role_name in ACTION_DEFAULT_ROLES:
        spec = roster.get(role_name)
        if not spec:
            if role_name == DEFAULT_REVIEWER_ROLE and FALLBACK_REVIEWER_ROLE in roster:
                log.warning(
                    "Default reviewer '%s' not loaded; falling back to %s",
                    role_name,
                    FALLBACK_REVIEWER_ROLE,
                )
                continue
            errors.append(f"Role '{role_name}' does not exist in loaded roster")
        elif not spec.dispatchable:
            errors.append(f"Role '{role_name}' is not dispatchable (retired={spec.retired}, surface={spec.surface})")

    if errors:
        for err in errors:
            log.warning("Action executor default role validation warning: %s", err)
        if raise_on_error:
            raise ValueError("; ".join(errors))
    return errors


# Validate at import time without crashing the server
try:
    validate_action_default_roles(raise_on_error=False)
except Exception:  # noqa: BLE001
    log.exception("Unexpected error during action default roles initial validation")

if TYPE_CHECKING:
    from staff.actions import ActionContext, ActionRegistry, ActionResult


def _optional_int(value: Any) -> int | None:
    raw = str(value or "")
    return int(raw) if raw.isdigit() else None


def execute_staff_dispatch(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    """Dispatch through the same policy as ``POST /api/staff/{role}/run`` (#1487).

    Pre: runs in an anyio worker thread (the proposal routes use ``to_thread``).
    Post: forwarding, rate limit, dry-run and the dispatch audit all apply; every
    refusal is a failed ``ActionResult`` with a classified ``failure_class``.
    """
    from fastapi import HTTPException
    from identity import Principal
    from staff.actions import ActionResult
    from staff.dispatch_service import (
        DispatchCommand,
        dispatch_staff_run,
        failure_class_for,
        refusal_message,
    )
    from staff.fleet import caller_identity
    from staff.loop_bridge import BridgeUnavailableError, run_on_loop

    caller = ctx.caller or Principal(id="staff_action", type="bot", name="staff_action")
    try:
        cmd = DispatchCommand(
            role=str(params.get("role") or ""),
            requested_by=caller_identity(caller),
            provider=params.get("provider"),
            model=params.get("model"),
            repo=str(params.get("repo") or ""),
            issue=_optional_int(params.get("issue")),
            pr=_optional_int(params.get("pr")),
            prompt=str(params.get("prompt") or ""),
            machine=str(params.get("machine") or "local"),
            dry_run=ctx.dry_run,
            work_item_id=str(params.get("work_item_id") or ""),
            surface="thread",
            thread_id=ctx.thread_id,
        )
    except ValueError as exc:
        return ActionResult(success=False, error=str(exc), failure_class="invalid_params")
    try:
        out = run_on_loop(dispatch_staff_run, cmd, caller)
    except BridgeUnavailableError as exc:
        return ActionResult(success=False, error=f"staff.dispatch {exc}", failure_class="bridge_unavailable")
    except HTTPException as exc:
        return ActionResult(success=False, error=refusal_message(exc), failure_class=failure_class_for(exc))
    run = out.get("run") or {}
    return ActionResult(
        success=True,
        result={
            "run_id": run.get("id"),
            "role": run.get("role", cmd.role),
            "repo": run.get("repo", cmd.repo),
            "status": run.get("status"),
            "machine": out.get("machine"),
            "forwarded_to": out.get("forwarded_to"),
            "dry_run": bool(out.get("dry_run")),
            "plan": out.get("plan"),
        },
        run_id=run.get("id"),
    )


def verify_staff_dispatch(res: ActionResult, params: dict[str, Any], ctx: ActionContext) -> tuple[bool, str]:
    result = res.result if isinstance(res.result, dict) else {}
    if result.get("dry_run") and not res.run_id:
        return True, "Dry run: plan validated, nothing dispatched"
    if not res.run_id:
        return False, "No run_id produced"
    if result.get("forwarded_to"):
        return True, f"Run {res.run_id} accepted by {result['forwarded_to']}"
    from staff.runner import get_runner

    run = get_runner().store.get_run(res.run_id)
    if not run:
        return False, f"Run {res.run_id} not found in store"
    # Report the post-run verification of its output (#1516); a failed verdict fails the check.
    if run.verification == "failed":
        return False, f"Run {res.run_id} ({run.status}) failed output verification: {run.verification_detail}"
    output = f"{run.verification}: {run.verification_detail}" if run.verification else "output not yet verified"
    return True, f"Run {res.run_id} verified in state {run.status}; {output}"


def execute_review_pr(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    from staff import review, roles
    from staff.actions import ActionResult
    from staff.runner import get_runner

    repo = str(params.get("repo") or "").strip()
    if not repo or not params.get("pr"):
        return ActionResult(success=False, error="Missing 'repo' or 'pr'", failure_class="invalid_params")
    pr = _optional_int(params.get("pr"))
    if pr is None:
        return ActionResult(
            success=False, error=f"'pr' must be a PR number, got {params.get('pr')!r}", failure_class="invalid_params"
        )
    r_dir = roles.roles_dir()
    roster = roles.load_roles(r_dir) if r_dir else None
    params_copy = review.prepare_review_params(
        {**params, "pr": pr},
        default_role=DEFAULT_REVIEWER_ROLE,
        roster=roster,
        store=get_runner().store,
        gh_probe=review.GhCliCommitProbe(),
    )
    return execute_staff_dispatch(params_copy, ctx)


def execute_staff_hold(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    from staff.actions import ActionResult

    text = str(params.get("text") or "").strip()
    if not text:
        return ActionResult(success=False, error="Missing 'text' for hold", failure_class="invalid_params")
    raw_applies = params.get("applies_to") or ["*"]
    applies_to = [str(a) for a in raw_applies] if isinstance(raw_applies, list) else [str(raw_applies)]
    from staff.holds import Hold, HoldsList, _hold_id

    hl = HoldsList()
    holds = hl.load()
    hid = str(params.get("hold_id") or _hold_id(text))
    existing = next((h for h in holds if h.id == hid or h.text.lower() == text.lower()), None)
    if existing:
        existing.active = True
        existing.applies_to = list(set(existing.applies_to + applies_to))
    else:
        holds.append(
            Hold(
                id=hid,
                text=text,
                applies_to=applies_to,
                lifted_when=str(params.get("lifted_when") or ""),
                active=True,
            )
        )
    hl.replace([h.to_dict() for h in holds])
    record_audit(
        action="hold_set",
        target=f"hold:{hid}",
        principal=format_caller(ctx.caller) if ctx.caller else "staff_action",
        surface="thread",
        thread_id=ctx.thread_id,
        outcome="success",
        detail={"text": text, "applies_to": applies_to},
        fail_closed=True,
        store=ctx.audit_store,
    )
    return ActionResult(success=True, result={"hold_id": hid, "text": text, "active": True})


def verify_staff_hold(res: ActionResult, params: dict[str, Any], ctx: ActionContext) -> tuple[bool, str]:
    from staff.holds import HoldsList

    text = str(params.get("text") or "").strip().lower()
    holds = HoldsList().load()
    if any(h.active and (h.text.lower() == text or (res.result and h.id == res.result.get("hold_id"))) for h in holds):
        return True, "Hold verified active in ledger"
    return False, "Hold not found or inactive"


def execute_staff_unhold(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    from staff.actions import ActionResult

    hid = str(params.get("hold_id") or "").strip()
    text = str(params.get("text") or "").strip().lower()
    if not hid and not text:
        return ActionResult(success=False, error="Must specify 'hold_id' or 'text'", failure_class="invalid_params")
    from staff.holds import HoldsList

    hl = HoldsList()
    holds = hl.load()
    matched = False
    for h in holds:
        if (hid and h.id == hid) or (text and h.text.lower() == text):
            h.active = False
            matched = True
    if not matched:
        return ActionResult(success=False, error="Hold not found", failure_class="not_found")
    hl.replace([h.to_dict() for h in holds])
    record_audit(
        action="hold_lift",
        target=f"hold:{hid or text}",
        principal=format_caller(ctx.caller) if ctx.caller else "staff_action",
        surface="thread",
        thread_id=ctx.thread_id,
        outcome="success",
        detail={"hold_id": hid, "text": text},
        fail_closed=True,
        store=ctx.audit_store,
    )
    return ActionResult(success=True, result={"hold_id": hid, "lifted": True})


def verify_staff_unhold(res: ActionResult, params: dict[str, Any], ctx: ActionContext) -> tuple[bool, str]:
    from staff.holds import HoldsList

    hid = str(params.get("hold_id") or "").strip()
    text = str(params.get("text") or "").strip().lower()
    for h in HoldsList().load():
        if (hid and h.id == hid) or (text and h.text.lower() == text):
            if h.active:
                return False, "Hold still active"
    return True, "Hold verified lifted"


def execute_code_request_create(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    from staff.actions import ActionResult

    title = str(params.get("title") or "").strip()
    repo = str(params.get("repo") or "").strip()
    if not title or not repo:
        return ActionResult(success=False, error="Missing 'title' or 'repo'", failure_class="invalid_params")
    from staff.work_items import get_work_item_store

    wi_store = get_work_item_store()
    role = str(params.get("role") or CODE_REQUEST_OWNER_ROLE)
    wi = wi_store.create_work_item(
        title=f"[Code Request] {title}",
        owner_role=role,
        thread_id=ctx.thread_id,
        requested_by=format_caller(ctx.caller) if ctx.caller else "staff_action",
    )
    return ActionResult(success=True, result={"work_item_id": wi.id, "title": title, "repo": repo})


def execute_board_propose(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    from staff.actions import ActionResult

    title = str(params.get("title") or "").strip()
    prop_body = str(params.get("proposal") or "").strip()
    if not title or not prop_body:
        return ActionResult(success=False, error="Missing 'title' or 'proposal'", failure_class="invalid_params")
    from staff.work_items import get_work_item_store

    wi_store = get_work_item_store()
    role = str(params.get("role") or BOARD_PROPOSAL_ROLE)
    wi = wi_store.create_work_item(
        title=f"[Board Proposal] {title}",
        owner_role=role,
        thread_id=ctx.thread_id,
        requested_by=format_caller(ctx.caller) if ctx.caller else "staff_action",
    )
    return ActionResult(success=True, result={"proposal_id": wi.id, "title": title})


def execute_notify_user(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    from staff.actions import ActionResult

    text = str(params.get("text") or params.get("message") or "").strip()
    if not text:
        return ActionResult(success=False, error="Missing 'text' or 'message'", failure_class="invalid_params")
    record_audit(
        action="notify_user",
        target="user",
        principal=format_caller(ctx.caller) if ctx.caller else "staff_action",
        surface="thread",
        thread_id=ctx.thread_id,
        outcome="success",
        detail={"text": text},
        store=ctx.audit_store,
    )
    return ActionResult(success=True, result={"notified": True, "text": text})


def execute_claim_issue(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    from staff.actions import ActionResult

    repo = str(params.get("repo") or "").strip()
    issue = int(params.get("issue") or 0)
    if not repo or not issue:
        return ActionResult(success=False, error="Missing 'repo' or 'issue'", failure_class="invalid_params")
    record_audit(
        action="claim_issue",
        target=f"{repo}#{issue}",
        principal=format_caller(ctx.caller) if ctx.caller else "staff_action",
        surface="thread",
        thread_id=ctx.thread_id,
        outcome="success",
        detail={"repo": repo, "issue": issue},
        store=ctx.audit_store,
    )
    return ActionResult(success=True, result={"claimed": True, "repo": repo, "issue": issue})


def execute_open_pr(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    from staff.actions import ActionResult

    repo = str(params.get("repo") or "").strip()
    branch = str(params.get("branch") or "").strip()
    title = str(params.get("title") or f"PR from {branch}").strip()
    if not repo or not branch:
        return ActionResult(success=False, error="Missing 'repo' or 'branch'", failure_class="invalid_params")
    record_audit(
        action="open_pr",
        target=f"{repo}:{branch}",
        principal=format_caller(ctx.caller) if ctx.caller else "staff_action",
        surface="thread",
        thread_id=ctx.thread_id,
        outcome="success",
        detail={"repo": repo, "branch": branch, "title": title},
        store=ctx.audit_store,
    )
    return ActionResult(success=True, result={"opened": True, "repo": repo, "branch": branch, "title": title})


def register_standard_actions(registry: ActionRegistry) -> None:
    """Register the standard staff actions (the non-maintenance catalogue) into ``registry``."""
    from staff.actions import ActionDefinition, ActionRiskClass

    standard_defs = (
        ActionDefinition(
            name="staff.dispatch",
            description="Dispatch an AI staff role to work on an issue, PR, or prompt.",
            params_schema={
                "role": "string",
                "repo": "string?",
                "prompt": "string?",
                "issue": "int?",
                "pr": "int?",
            },
            required_scope="staff.dispatch",
            risk_class=ActionRiskClass.MEDIUM,
            executor=execute_staff_dispatch,
            verifier=verify_staff_dispatch,
        ),
        ActionDefinition(
            name="staff.review_pr",
            description="Request a PR review from a specialist staff role.",
            params_schema={
                "repo": "string",
                "pr": "int",
                "reviewer": "string?",
                "focus": "string?",
            },
            required_scope="staff.dispatch",
            risk_class=ActionRiskClass.LOW,
            executor=execute_review_pr,
            verifier=verify_staff_dispatch,
        ),
        ActionDefinition(
            name="staff.hold",
            description="Set an operational hold locking a role or policy.",
            params_schema={
                "text": "string",
                "applies_to": "list[string]?",
                "lifted_when": "string?",
            },
            required_scope="staff.holds.write",
            risk_class=ActionRiskClass.HIGH,
            executor=execute_staff_hold,
            verifier=verify_staff_hold,
        ),
        ActionDefinition(
            name="staff.unhold",
            description="Lift an operational hold.",
            params_schema={"hold_id": "string?", "text": "string?"},
            required_scope="staff.holds.write",
            risk_class=ActionRiskClass.HIGH,
            executor=execute_staff_unhold,
            verifier=verify_staff_unhold,
        ),
        ActionDefinition(
            name="code_request.create",
            description="Create a tracked Code Request work item.",
            params_schema={
                "title": "string",
                "repo": "string",
                "description": "string?",
                "priority": "string?",
            },
            required_scope="code_requests.write",
            risk_class=ActionRiskClass.MEDIUM,
            executor=execute_code_request_create,
        ),
        ActionDefinition(
            name="board.propose",
            description="Submit a proposal to the Board of Directors.",
            params_schema={"title": "string", "proposal": "string", "target": "string?"},
            required_scope="board.proposals.write",
            risk_class=ActionRiskClass.MEDIUM,
            executor=execute_board_propose,
        ),
        ActionDefinition(
            name="submit_proposal",
            description="Submit a proposal to the Board of Directors.",
            params_schema={"title": "string", "proposal": "string", "target": "string?"},
            required_scope="board.proposals.write",
            risk_class=ActionRiskClass.MEDIUM,
            executor=execute_board_propose,
        ),
        ActionDefinition(
            name="notify_user",
            description="Send an unsolicited notification or ping to the owner.",
            params_schema={"text": "string"},
            required_scope="staff.chat",
            risk_class=ActionRiskClass.LOW,
            executor=execute_notify_user,
        ),
        ActionDefinition(
            name="claim_issue",
            description="Claim an issue with an agent coordination lease.",
            params_schema={"repo": "string", "issue": "int"},
            required_scope="staff.dispatch",
            risk_class=ActionRiskClass.LOW,
            executor=execute_claim_issue,
        ),
        ActionDefinition(
            name="open_pr",
            description="Open a pull request from a branch.",
            params_schema={"repo": "string", "branch": "string", "title": "string?"},
            required_scope="staff.dispatch",
            risk_class=ActionRiskClass.MEDIUM,
            executor=execute_open_pr,
        ),
    )
    for ad in standard_defs:
        registry.register(ad)
