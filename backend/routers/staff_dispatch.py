"""HTTP adaptation from staff run requests to the shared dispatch service."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from identity import Principal
from staff import fleet as staff_fleet
from staff.barb_lease_fence import claim_for_write
from staff.dispatch_service import DispatchCommand, dispatch_staff_run
from staff.dispatcher_metrics import dispatcher_origin


async def run_dispatch(
    role: str,
    body: Any,
    request: Request,
    caller: Principal,
    operation_id: str = "",
    force_duplicate: bool = False,
) -> dict[str, Any]:
    """Apply the /run request policy before calling the shared dispatcher."""
    caller_id = staff_fleet.caller_identity(caller)
    is_peer = staff_fleet.is_fleet_peer(caller)
    if is_peer and body.operation_id:
        operation_id = body.operation_id
    surface = body.surface or "api"
    thread_id = body.thread_id or ""
    origin_node = body.origin_node or ""
    on_behalf_of = ""
    obo = staff_fleet.extract_on_behalf_of(request.headers.get("x-staff-on-behalf-of"), is_peer)
    if obo:
        caller_id = obo["principal"]
        on_behalf_of = obo["principal"]
        surface = obo.get("surface") or surface
        thread_id = obo.get("thread_id") or thread_id
        origin_node = obo.get("origin_node") or origin_node

    is_force = force_duplicate
    authenticated_origin = dispatcher_origin(caller_id, caller.type)
    if caller.assistant_kind == "grokbot":
        authenticated_origin = "grok"
    try:
        raw_json = await request.json()
        if isinstance(raw_json, dict):
            if raw_json.get("force_duplicate"):
                is_force = True
            forwarded_origin = raw_json.get("dispatch_origin")
            if is_peer and obo and forwarded_origin in {"grok", "schedule", "console", "watch_tick", "other"}:
                authenticated_origin = forwarded_origin
    except Exception:
        pass

    cmd = DispatchCommand(
        role=role,
        requested_by=caller_id,
        provider=body.provider,
        model=body.model,
        repo=body.repo,
        issue=body.issue,
        pr=body.pr,
        prompt=body.prompt,
        machine=body.machine,
        dry_run=body.dry_run,
        work_item_id=body.work_item_id or "",
        on_behalf_of=on_behalf_of,
        surface=surface,
        thread_id=thread_id,
        origin_node=origin_node,
        ignore_budget=body.ignore_budget,
        operation_id=operation_id,
        task_type=body.task_type,
        standards_extra=body.standards_extra,
        requested_skill=body.skill,
        force_duplicate=is_force,
        force_claim_override=body.force_claim_override,
        claim_override_reason=body.claim_override_reason,
        execution_mode=getattr(body, "execution_mode", "local") or "local",
        dispatch_origin=authenticated_origin,
    )
    # A request that declares Barb authority is fenced on its lease epoch (#2734, #3543);
    # when a peer forwards on behalf of another principal, evaluate the fence against the
    # original caller identity so Barb-authority forwards cannot bypass the lease fence.
    fence_principal = caller
    if obo and on_behalf_of:
        raw_id = on_behalf_of.removeprefix("principal:").removeprefix("user:")
        fence_principal = Principal(
            id=raw_id,
            type="bot" if raw_id.startswith("agent-") or raw_id == "barb" else caller.type,
            name=raw_id,
            roles=list(caller.roles),
            scopes=list(caller.scopes),
            assistant_kind="grokbot" if authenticated_origin == "grok" else (caller.assistant_kind or ""),
        )
    return await dispatch_staff_run(
        cmd,
        caller,
        barb_claim=claim_for_write(request.headers, fence_principal, surface="staff.dispatch"),
    )
