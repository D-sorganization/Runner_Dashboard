"""Standard action executors and verifiers for Staff Console (SC-B6, Issue #1313)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from identity import format_caller
from staff.audit import record_audit
from staff.code_request_actions import execute_code_request_create, execute_code_request_update
from staff.hold_actions import execute_staff_hold, execute_staff_unhold, verify_staff_hold, verify_staff_unhold

log = logging.getLogger("dashboard.staff.action_executors")

DEFAULT_REVIEWER_ROLE = "code-reviewer"
FALLBACK_REVIEWER_ROLE = "fleet-critic"
# Phase 2 of Repository_Management#1766 (#1665): product-owner owns Code Requests; barb
# holds them until a node's Repository_Management checkout carries the role.
CODE_REQUEST_OWNER_ROLE = "product-owner"
FALLBACK_CODE_REQUEST_OWNER_ROLE = "barb"
BOARD_PROPOSAL_ROLE = "board-secretary"

ACTION_DEFAULT_ROLES: tuple[str, ...] = (
    DEFAULT_REVIEWER_ROLE,
    CODE_REQUEST_OWNER_ROLE,
    BOARD_PROPOSAL_ROLE,
)


def _board_coordinator_extra() -> tuple[str, ...]:
    """The Board group's coordinator when it is not already validated as a default role (#1601).

    ``BOARD_PROPOSAL_ROLE`` is the coordinator by construction and is checked as a
    default role, so only a coordinator that has drifted from it is added.
    """
    try:
        from staff.groups import get_board_group  # noqa: PLC0415 - groups imports this module

        coordinator = get_board_group().coordinator
    except Exception as exc:  # noqa: BLE001 - validation must never break startup
        log.debug("Board group coordinator could not be loaded for validation: %s", exc)
        return ()
    already = (*ACTION_DEFAULT_ROLES, BOARD_PROPOSAL_ROLE)
    return () if not coordinator or coordinator in already else (coordinator,)


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
    for role_name in (*ACTION_DEFAULT_ROLES, *_board_coordinator_extra()):
        spec = roster.get(role_name)
        if not spec:
            if role_name == DEFAULT_REVIEWER_ROLE and FALLBACK_REVIEWER_ROLE in roster:
                log.warning(
                    "Default reviewer '%s' not loaded; falling back to %s",
                    role_name,
                    FALLBACK_REVIEWER_ROLE,
                )
                continue
            if role_name == CODE_REQUEST_OWNER_ROLE and FALLBACK_CODE_REQUEST_OWNER_ROLE in roster:
                log.warning(
                    "Code Request owner '%s' not loaded; falling back to %s",
                    role_name,
                    FALLBACK_CODE_REQUEST_OWNER_ROLE,
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


def code_request_owner_role(roster: Mapping[str, Any] | None = None) -> str:
    """The role that owns new Code Requests: product-owner once loaded, else barb.

    Post: always returns a role name; a roster that cannot be loaded yields the fallback.
    """
    if roster is None:
        from staff.roles import load_roles, roles_dir

        r_dir = roles_dir()
        try:
            roster = load_roles(r_dir) if r_dir is not None else {}
        except Exception:  # noqa: BLE001 - ownership must never break Code Request creation
            log.warning("Staff roles could not be loaded; Code Requests go to %s", FALLBACK_CODE_REQUEST_OWNER_ROLE)
            roster = {}
    return CODE_REQUEST_OWNER_ROLE if CODE_REQUEST_OWNER_ROLE in roster else FALLBACK_CODE_REQUEST_OWNER_ROLE


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
        description=prop_body,
    )
    return ActionResult(success=True, result={"proposal_id": wi.id, "title": title, "proposal": wi.description})


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
    """Stub executor for ``open_pr``: refuses honestly instead of claiming success (#1758).

    Pre: ``repo`` and ``branch`` are present.
    Post: never returns ``success=True`` or an ``opened`` key without actually calling
    GitHub; no PR was opened, so no success audit row is written for this action.
    """
    from staff.actions import ActionResult

    repo = str(params.get("repo") or "").strip()
    branch = str(params.get("branch") or "").strip()
    if not repo or not branch:
        return ActionResult(success=False, error="Missing 'repo' or 'branch'", failure_class="invalid_params")
    return ActionResult(
        success=False,
        error="open_pr is not implemented: open the PR with gh or a staff.dispatch",
        failure_class="not_implemented",
    )


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
            name="code_request.update",
            description="Update the description of a Code Request in draft or triage state.",
            params_schema={
                "id": "string",
                "description": "string",
            },
            required_scope="code_requests.write",
            risk_class=ActionRiskClass.MEDIUM,
            executor=execute_code_request_update,
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
