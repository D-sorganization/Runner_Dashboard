"""Standard action executors and verifiers for Staff Console (SC-B6, Issue #1313)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from identity import format_caller
from staff.audit import record_audit

log = logging.getLogger("dashboard.staff.action_executors")

DEFAULT_REVIEWER_ROLE = "fleet-critic"
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
    from staff.actions import ActionContext, ActionResult


def execute_staff_dispatch(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    from staff.actions import ActionResult

    role = str(params.get("role") or "").strip()
    if not role:
        return ActionResult(success=False, error="Missing required parameter 'role'", failure_class="invalid_params")
    from staff.runner import RunRequest, get_runner

    runner = get_runner()
    raw_issue = str(params.get("issue") or "")
    issue_num = int(raw_issue) if raw_issue.isdigit() else None
    raw_pr = str(params.get("pr") or "")
    pr_num = int(raw_pr) if raw_pr.isdigit() else None

    req = RunRequest(
        role=role,
        repo=str(params.get("repo") or ""),
        prompt=str(params.get("prompt") or ""),
        issue=issue_num,
        pr=pr_num,
        provider=params.get("provider"),
        model=params.get("model"),
        machine=params.get("machine"),
        requested_by=format_caller(ctx.caller) if ctx.caller else "staff_action",
        thread_id=ctx.thread_id,
    )
    rec = runner.submit(req)
    return ActionResult(
        success=True,
        result={"run_id": rec.id, "role": rec.role, "repo": rec.repo, "status": rec.status},
        run_id=rec.id,
    )


def verify_staff_dispatch(res: ActionResult, params: dict[str, Any], ctx: ActionContext) -> tuple[bool, str]:
    if not res.run_id:
        return False, "No run_id produced"
    from staff.runner import get_runner

    run = get_runner().store.get_run(res.run_id)
    if not run:
        return False, f"Run {res.run_id} not found in store"
    return True, f"Run {res.run_id} verified in state {run.status}"


def execute_review_pr(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    from staff.actions import ActionResult

    repo = str(params.get("repo") or "").strip()
    pr = params.get("pr")
    if not repo or not pr:
        return ActionResult(success=False, error="Missing 'repo' or 'pr'", failure_class="invalid_params")
    reviewer = str(params.get("reviewer") or DEFAULT_REVIEWER_ROLE)
    focus = str(params.get("focus") or "code review")
    prompt = f"Review PR #{pr} in {repo}. Focus: {focus}"
    params_copy = dict(params)
    params_copy["role"] = reviewer
    params_copy["prompt"] = prompt
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


def execute_maintenance_action(params: dict[str, Any], ctx: ActionContext, action_name: str) -> ActionResult:
    from staff.maintenance import execute_maintenance

    return execute_maintenance(action_name, params, ctx)
