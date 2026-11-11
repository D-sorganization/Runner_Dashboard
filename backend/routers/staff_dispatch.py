"""HTTP adaptation from staff run requests to the shared dispatch service."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from identity import Principal, identity_manager
from staff import fleet as staff_fleet
from staff.barb_lease_fence import EPOCH_HEADER, SCOPE_HEADER, claim_for_write
from staff.barb_origin_fence import ORIGIN_FENCE_HEADER, apply_origin_fence_trust
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
    fleet_bearer = staff_fleet.hub_fleet_bearer_authenticated(request.headers.get("authorization"))
    obo = staff_fleet.extract_on_behalf_of(
        request.headers.get("x-staff-on-behalf-of"),
        is_peer,
        fleet_bearer_authenticated=fleet_bearer,
    )
    if obo:
        caller_id = obo["principal"]
        on_behalf_of = obo["principal"]
        surface = obo.get("surface") or surface
        thread_id = obo.get("thread_id") or thread_id
        origin_node = obo.get("origin_node") or origin_node

    is_force = force_duplicate
    authenticated_origin = dispatcher_origin(caller_id, caller.type)
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
        pr_restrictions=tuple(body.pr_restrictions),
    )
    # A request that declares Barb authority is fenced on its lease epoch (#2734, #3543);
    # when a peer forwards on behalf of another principal, evaluate the fence against the
    # original caller identity so Barb-authority forwards cannot bypass the lease fence.
    #
    # Hub fleet bearer + signed OBO (#4025): the bearer proves the hub vouched for the header;
    # the effective *run* identity is the OBO principal, but the transport is still fleet-peer,
    # so the Barb unclaimed-write fence must not treat the OBO id as the Barb seat (#3064).
    fence_principal = caller
    headers = request.headers
    # Hub UI bearer+OBO without Barb headers keeps the transport caller for claim parsing (#4025).
    # Fleet peer forwards that declare X-Barb-* use the OBO Barb identity (#4357).
    barb_declared = bool((headers.get(SCOPE_HEADER) or "").strip() or (headers.get(EPOCH_HEADER) or "").strip())
    if obo and on_behalf_of and not (fleet_bearer and obo and not barb_declared):
        raw_id = on_behalf_of.removeprefix("principal:").removeprefix("user:")
        subj = identity_manager.get_principal(raw_id)
        if subj is not None:
            fence_principal = subj
        else:
            fence_principal = Principal(
                id=raw_id,
                type="bot" if raw_id.startswith("agent-") or raw_id == "barb" else caller.type,
                name=raw_id,
                roles=list(caller.roles),
                scopes=list(caller.scopes),
                assistant_kind=caller.assistant_kind or "",
            )
    # #4025: Hub path uses HUB_FLEET_TOKEN + signed OBO (no Barb lease headers on the wire).
    # Do not treat the OBO principal as an unclaimed Barb seat for fence purposes unless the
    # request explicitly declares X-Barb-Scope + X-Barb-Lease-Epoch (#2734 / #3279).
    claim_principal = fence_principal
    if fleet_bearer and obo and on_behalf_of and not is_peer and not barb_declared:
        claim_principal = caller
    barb_claim = claim_for_write(headers, claim_principal, surface="staff.dispatch")
    trusted_forwarder = is_peer or fleet_bearer
    barb_claim = apply_origin_fence_trust(
        barb_claim,
        headers.get(ORIGIN_FENCE_HEADER),
        trusted_forwarder=trusted_forwarder,
    )
    return await dispatch_staff_run(cmd, caller, barb_claim=barb_claim)
