"""Barb single-authority status, parallel scoped checkout, turnover log & task handoff API.

Mounted under ``/api/v1/staff``:
  GET  /barb/status             Authority role, epoch and outbox depth (#1800)
  GET  /barb/continuity         Scoped leases, turnover log, compliance status & task inventory
  POST /barb/leases/checkout    Check out a scoped Barb sub-role (e.g. barb:front-door)
  POST /barb/leases/heartbeat   Extend the caller's own live lease (holder bound to caller)
  POST /barb/leases/release     Release a scoped Barb lease (enforces turnover compliance)
  GET  /barb/turnover           List turnover log entries
  POST /barb/turnover           Append a shift turnover log entry
  GET  /barb/inventory          List active Barb task inventory & desktop handoff queue
  POST /barb/inventory          Create or update a task in the Barb inventory
  POST /barb/handoff            Transition CLI <-> Desktop handoff state with fencing epoch
  POST /barb/classify-surface   Classify whether a task belongs on Desktop App vs. Headless CLI
"""

# ruff: noqa: B008
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import orchestration_audit as _audit
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from identity import Principal, format_caller, principal_has_scope, require_scope
from pydantic import BaseModel, Field
from staff.barb_continuity import classify_task_surface, get_barb_continuity_store
from staff.barb_lease_fence import barb_write_fence, parse_barb_claim
from staff.barb_runtime import barb_status
from staff.runner import get_runner

router = APIRouter(tags=["staff-barb"])


MAX_LEASE_TTL_SEC = 14400  # mirrors the store clamp; larger requests are rejected, not silently shortened


def bind_holder(caller: Principal, requested: str | None) -> str:
    """Derive the lease holder from the authenticated caller; reject a mismatching claim (403).

    A claimed holder must equal the caller id or extend it as ``<caller-id>:<suffix>`` (lets agents sharing
    the fleet bearer token tell their seats apart without being able to impersonate another principal).
    """
    cid = caller.id
    if not requested or requested == cid:
        return cid
    if requested.startswith(cid + ":") and len(requested) > len(cid) + 1:
        return requested
    raise HTTPException(
        status_code=403,
        detail={"error": "holder_caller_mismatch", "caller": cid, "holder": requested},
    )


class BarbLeaseCheckoutRequest(BaseModel):
    scope: str = Field(default="barb:front-door", max_length=64)
    holder: str | None = Field(default=None, max_length=128)
    persona: str = Field(default="rd-barb", max_length=64)
    machine: str = Field(default="", max_length=64)
    ttl_sec: int = Field(default=3600, ge=60, le=MAX_LEASE_TTL_SEC)
    summary: str = Field(default="", max_length=500)
    takeover: bool = False
    reason: str = Field(default="", max_length=500)


class BarbLeaseHeartbeatRequest(BaseModel):
    scope: str = Field(..., min_length=1, max_length=64)
    epoch: int = Field(..., ge=1)
    holder: str | None = Field(default=None, max_length=128)
    ttl_sec: int = Field(default=3600, ge=60, le=MAX_LEASE_TTL_SEC)


class BarbLeaseReleaseRequest(BaseModel):
    scope: str = Field(..., min_length=1, max_length=64)
    holder: str | None = Field(default=None, max_length=128)
    turnover_summary: str = Field(default="", max_length=2000)
    completed_items: list[str] = Field(default_factory=list)
    in_progress_items: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    force: bool = False


class BarbTurnoverCreateRequest(BaseModel):
    scope: str = Field(default="barb:front-door", max_length=64)
    persona: str = Field(default="rd-barb", max_length=64)
    holder: str | None = Field(default=None, max_length=128)
    machine: str = Field(default="", max_length=64)
    summary: str = Field(..., min_length=1, max_length=2000)
    completed_items: list[str] = Field(default_factory=list)
    in_progress_items: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class BarbTaskUpsertRequest(BaseModel):
    task_id: str | None = Field(default=None, max_length=64)
    title: str = Field(..., min_length=1, max_length=300)
    scope: str = Field(default="barb:dispatch-coordinator", max_length=64)
    status: str = Field(default="queued", max_length=32)
    surface: str | None = Field(default=None, max_length=32)
    requires_desktop: bool | None = None
    desktop_app: str = Field(default="", max_length=64)
    desktop_reason: str = Field(default="", max_length=500)
    assigned_persona: str = Field(default="", max_length=64)
    assigned_holder: str = Field(default="", max_length=128)
    machine: str = Field(default="", max_length=64)
    repo: str = Field(default="", max_length=128)
    target_ref: str = Field(default="", max_length=128)
    priority: str = Field(default="normal", max_length=32)


class BarbHandoffTransitionRequest(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=64)
    action: str = Field(..., min_length=1, max_length=32)
    actor: str = Field(..., min_length=1, max_length=128)
    persona: str = Field(default="", max_length=64)
    machine: str = Field(default="", max_length=64)
    reason: str = Field(default="", max_length=500)
    desktop_app: str = Field(default="", max_length=64)
    artifact_uri: str = Field(default="", max_length=500)
    resume_notes: str = Field(default="", max_length=1000)
    expected_epoch: int = Field(..., ge=0)  # fencing token is mandatory


class BarbSurfaceClassifyRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    description: str = Field(default="", max_length=2000)
    labels: list[str] = Field(default_factory=list)


@router.get("/barb/status", summary="Barb authority role, epoch and outbox depth")
async def get_barb_status(_caller: Principal = Depends(require_scope("staff.read"))) -> dict[str, Any]:
    """Role (unset/authority/replica), epoch, outbox depth and whether delivery is degraded."""
    return barb_status()


@router.get("/barb/continuity", summary="Multi-Barb scoped leases, turnover log, compliance & task inventory")
async def get_barb_continuity(
    turnover_limit: int = Query(default=15, ge=1, le=100),
    _caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    store = get_barb_continuity_store()
    snap = store.continuity_snapshot(turnover_limit=turnover_limit)
    return {"machine": get_runner().machine, "authority": barb_status(), **snap}


@router.get("/barb/leases", summary="List scoped Barb sub-role leases", include_in_schema=False)
async def get_barb_leases(
    include_released: bool = Query(default=False),
    _caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    store = get_barb_continuity_store()
    leases = store.list_leases(include_expired=include_released)
    return {"machine": get_runner().machine, "count": len(leases), "leases": leases}


@router.post("/barb/leases", summary="Check out a scoped Barb sub-role lease", include_in_schema=False)
async def post_barb_leases(
    req: BarbLeaseCheckoutRequest,
    _caller: Principal = Depends(require_scope("staff.dispatch")),
) -> dict[str, Any]:
    return await post_barb_lease_checkout(req=req, _caller=_caller)


@router.post("/barb/leases/checkout", summary="Check out a scoped Barb sub-role lease")
async def post_barb_lease_checkout(
    req: BarbLeaseCheckoutRequest,
    _caller: Principal = Depends(require_scope("staff.dispatch")),
) -> dict[str, Any]:
    holder = bind_holder(_caller, req.holder)
    if req.takeover:
        if not principal_has_scope(_caller, "staff.admin"):
            raise HTTPException(
                status_code=403, detail={"error": "takeover_requires_operator", "required_scope": "staff.admin"}
            )
        if not req.reason.strip():
            raise HTTPException(status_code=422, detail={"error": "takeover_reason_required"})
    store = get_barb_continuity_store()
    # Writers prove they still hold the seat by sending the returned ``epoch`` back as
    # X-Barb-Lease-Epoch (see staff.barb_lease_fence); takeover bumps it under the store lock.
    res = store.checkout_lease(
        scope=req.scope,
        holder=holder,
        persona=req.persona,
        machine=req.machine or get_runner().machine,
        ttl_seconds=req.ttl_sec,
        summary=req.summary,
        force=req.takeover,
    )
    if not res["acquired"]:
        raise HTTPException(status_code=409, detail=res)
    if req.takeover:
        await _audit.append_orchestration_audit(
            {
                "orchestration_type": "barb_lease",
                "action": "barb.lease.takeover",
                "scope": req.scope,
                "new_holder": holder,
                "epoch": res["lease"]["epoch"],
                "reason": req.reason.strip(),
                "requested_by": _caller.id,
                "caller": format_caller(_caller),
                "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
        )
    return {"ok": True, **res}


@router.post("/barb/leases/heartbeat", summary="Extend the caller's own live Barb lease")
async def post_barb_lease_heartbeat(
    req: BarbLeaseHeartbeatRequest,
    _caller: Principal = Depends(require_scope("staff.dispatch")),
) -> dict[str, Any]:
    holder = bind_holder(_caller, req.holder)
    res = get_barb_continuity_store().heartbeat_lease(
        scope=req.scope, holder=holder, epoch=req.epoch, ttl_seconds=req.ttl_sec
    )
    if not res["renewed"]:
        code = 403 if res["reason"] == "holder_mismatch" else 409
        raise HTTPException(status_code=code, detail=res)
    return {"ok": True, **res}


@router.post("/barb/leases/release", summary="Release a scoped Barb lease with turnover compliance check")
async def post_barb_lease_release(
    req: BarbLeaseReleaseRequest,
    _caller: Principal = Depends(require_scope("staff.dispatch")),
) -> dict[str, Any]:
    holder = bind_holder(_caller, req.holder)
    store = get_barb_continuity_store()
    if req.turnover_summary.strip():
        store.append_turnover(
            scope=req.scope,
            author=holder,
            machine=get_runner().machine,
            summary=req.turnover_summary,
            completed=req.completed_items,
            in_progress=req.in_progress_items,
            next_actions=req.next_actions,
            blockers=req.blockers,
        )
    res = store.release_lease(scope=req.scope, holder=holder, require_turnover=not req.force)
    if not res["released"]:
        raise HTTPException(status_code=409, detail=res)
    return {"ok": True, **res}


@router.get("/barb/turnover", summary="List Barb shift turnover log entries")
async def get_barb_turnover(
    scope: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=25, ge=1, le=100),
    _caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    entries = get_barb_continuity_store().list_turnover(limit=limit, scope=scope)
    return {"count": len(entries), "entries": entries}


@router.post("/barb/turnover", summary="Record a shift turnover log entry")
async def post_barb_turnover(
    req: BarbTurnoverCreateRequest,
    request: Request,
    _caller: Principal = Depends(require_scope("staff.dispatch")),
) -> dict[str, Any]:
    author = bind_holder(_caller, req.holder)
    claim = parse_barb_claim(request.headers, _caller.id)
    if claim is not None and claim.scope != req.scope:
        raise HTTPException(
            status_code=422, detail={"error": "barb_scope_mismatch", "header": claim.scope, "body": req.scope}
        )
    with barb_write_fence(
        claim, surface="barb.turnover", target=f"barb:turnover:{req.scope}", principal=format_caller(_caller)
    ):
        entry = get_barb_continuity_store().append_turnover(
            scope=req.scope,
            persona=req.persona,
            author=author,
            machine=req.machine or get_runner().machine,
            summary=req.summary,
            completed=req.completed_items,
            in_progress=req.in_progress_items,
            next_actions=req.next_actions,
            blockers=req.blockers,
        )
    return {"ok": True, "turnover": entry}


@router.get("/barb/inventory", summary="List active Barb task inventory and desktop handoff queue")
async def get_barb_inventory(
    status: str | None = Query(default=None, max_length=32),
    surface: str | None = Query(default=None, max_length=32),
    scope: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=500),
    _caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    tasks = get_barb_continuity_store().list_tasks(status=status, scope=scope, handoff_state=surface, limit=limit)
    return {"count": len(tasks), "tasks": tasks}


@router.post("/barb/inventory", summary="Create or update a task in the Barb inventory")
async def post_barb_inventory(
    req: BarbTaskUpsertRequest,
    request: Request,
    _caller: Principal = Depends(require_scope("staff.dispatch")),
) -> dict[str, Any]:
    claim = parse_barb_claim(request.headers, _caller.id)
    with barb_write_fence(
        claim, surface="barb.inventory", target=f"barb:inventory:{req.scope}", principal=format_caller(_caller)
    ):
        task = get_barb_continuity_store().upsert_task(
            task_id=req.task_id,
            title=req.title,
            scope=req.scope,
            status=req.status,
            surface=req.surface,
            owner_persona=req.assigned_persona or req.assigned_holder,
            machine=req.machine or get_runner().machine,
            repo=req.repo,
            issue_or_pr=req.target_ref,
            priority=req.priority,
            handoff_request=req.desktop_reason or None,
        )
    return {"ok": True, "task": task}


_HANDOFF_ACTION_MAP = {
    "request_desktop": "needs_desktop",
    "claim_desktop": "desktop_claimed",
    "complete_desktop": "resume_ready",
    "resume_cli": "none",
    "complete_task": "completed",
}


@router.post("/barb/handoff", summary="Transition CLI <-> Desktop handoff state with monotonic fencing epoch")
async def post_barb_handoff(
    req: BarbHandoffTransitionRequest,
    request: Request,
    _caller: Principal = Depends(require_scope("staff.dispatch")),
) -> dict[str, Any]:
    target_state = _HANDOFF_ACTION_MAP.get(req.action, req.action)
    claim = parse_barb_claim(request.headers, _caller.id)
    try:
        with barb_write_fence(
            claim, surface="barb.handoff", target=f"barb:handoff:{req.task_id}", principal=format_caller(_caller)
        ):
            res = get_barb_continuity_store().transition_handoff(
                task_id=req.task_id,
                target_state=target_state,
                actor_persona=req.persona or req.actor,
                expected_epoch=req.expected_epoch,
                handoff_request=req.reason or None,
                handoff_resolution=req.resume_notes or None,
                artifact_ref=req.artifact_uri or None,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error": "task_not_found", "detail": str(exc)}) from exc
    if not res["transitioned"]:
        raise HTTPException(status_code=409, detail=res)
    return {"ok": True, **res}


@router.post("/barb/classify-surface", summary="Classify whether a task belongs on Desktop App vs. Headless CLI")
async def post_barb_classify_surface(
    req: BarbSurfaceClassifyRequest,
    _caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    return classify_task_surface(req.title, req.description)
