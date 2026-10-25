"""HTTP adaptation from staff run requests to the shared dispatch service."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from identity import Principal
from staff import fleet as staff_fleet
from staff.barb_lease_fence import parse_barb_claim
from staff.dispatch_service import DispatchCommand, dispatch_staff_run


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
    try:
        raw_json = await request.json()
        if isinstance(raw_json, dict) and raw_json.get("force_duplicate"):
            is_force = True
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
    )
    # A request that declares Barb authority is fenced on its lease epoch (#2734); others are unaffected.
    return await dispatch_staff_run(cmd, caller, barb_claim=parse_barb_claim(request.headers, caller.id))
