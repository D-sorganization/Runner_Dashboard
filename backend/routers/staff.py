"""Staff Hub API (epic #1192, issues #1194 / #1195 / #1197).

Routes (all under ``/api/staff``):
  GET  /roster                Roster: roles, provider availability, active counts.
  GET  /board                 Status monitor: fleet-wide when peers exist, ``?local=1`` for this node.
  GET  /summary               One-call brief for Barb / Orchestrator (in flight, attention, spend, holds).
  GET  /runs                  Run history (filters: role, status, since, limit).
  GET  /runs/{id}             One run + its most recent events.
  GET  /runs/{id}/stream      Server-sent events: live output until the run ends.
  POST /{role}/run            Dispatch here, forward to ``machine`` or place with ``machine: auto``;
                              ``dry_run`` previews the plan.
  POST /runs/{id}/cancel      Terminate a run.

Auth: reads use ``require_fleet_peer`` (hub/peer readable, same as the fleet
routes). ``POST /{role}/run`` requires a bearer principal with ``staff.dispatch``
(fleet token or service token); loopback dev auth does not substitute (#2053).
Cancel and other mutations use scoped bearer auth as documented per route.
"""

# ruff: noqa: B008
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from identity import Principal, format_caller, require_scope
from pydantic import BaseModel, Field, field_validator
from routers.staff_dispatch import run_dispatch
from staff import fleet as staff_fleet
from staff.adapters import ADAPTERS, available_providers, chat_only_providers, unattended_providers
from staff.audit import (
    export_audit_csv,
    export_audit_ndjson,
    get_audit_store,
    record_audit,
)
from staff.cli_version import provider_versions
from staff.dispatch_auth import require_staff_run_dispatch
from staff.github_app_preflight import run_preflight_and_cache
from staff.github_credential_service import mint_github_credential_for_run
from staff.models import (
    StaffAuditListResponse,
    StaffBoardResponse,
    StaffCancelResponse,
    StaffDispatchResponse,
    StaffProvidersResponse,
    StaffRosterResponse,
    StaffRunDetailResponse,
    StaffRunsResponse,
    StaffSummaryResponse,
)
from staff.rm_sync import source_status as source_status  # noqa: F401
from routers.staff_avatars import enrich_roster_role
from staff.runner import get_runner
from staff.store import ACTIVE_STATUSES, RUN_STATUSES
from staff.summary_view import build_staff_summary

log = logging.getLogger("dashboard.staff")
router = APIRouter(prefix="/api/staff", tags=["staff"])

MAX_LIMIT = 500
STREAM_POLL_SECONDS = 0.5
STREAM_IDLE_TIMEOUT_SECONDS = 6 * 3600


class RunBody(BaseModel):
    """POST body for a dispatch. DbC: exactly the fields the runner understands."""

    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=80)
    repo: str = Field(default="", max_length=120)
    issue: int | None = Field(default=None, ge=1)
    pr: int | None = Field(default=None, ge=1)
    prompt: str = Field(default="", max_length=20000)
    machine: str = Field(default="local", max_length=60)
    surface: str | None = Field(default=None, max_length=40)
    thread_id: str | None = Field(default=None, max_length=120)
    work_item_id: str | None = Field(default=None, max_length=120)
    origin_node: str | None = Field(default=None, max_length=60)
    dry_run: bool = False
    # Run even when the role's budget or the provider's plan window is spent (#1588); audited.
    ignore_budget: bool = False
    # Set by a forwarding peer so a retried forward admits one run (#1796); ignored from others.
    operation_id: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9-]+$")
    # Unattended skill selection (#2157): filters registry skills; omit to match role/repo automatically.
    task_type: str = Field(default="", max_length=80)
    standards_extra: str = Field(default="", max_length=8000)
    skill: str | None = Field(
        default=None,
        max_length=120,
        description="Explicit skill package name; must exist in the registry or dispatch is rejected.",
    )
    execution_mode: Literal["local", "cloud"] = "local"

    @field_validator("repo")
    @classmethod
    def _bare_repo(cls, value: str) -> str:
        value = value.strip()
        if "/" in value or ".." in value:
            raise ValueError("repo must be a bare repository name (no owner, no path)")
        return value


def _today_iso() -> str:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


@router.get("/roster", response_model=StaffRosterResponse, response_model_exclude_none=True)
@router.get("/roles", response_model=StaffRosterResponse, response_model_exclude_none=True)
async def roster(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    runner = get_runner()
    roles, active = runner.roles(), runner.store.active_runs()
    per_role = {s: sum(1 for a in active if a.role == s) for s in roles}
    return {
        "machine": runner.machine,
        "roles": [
            enrich_roster_role(name, spec, per_role.get(name, 0)) for name, spec in sorted(roles.items())
        ],
        "providers": available_providers(),
        "provider_versions": await asyncio.to_thread(provider_versions, ADAPTERS),
        "chat_only_providers": chat_only_providers(),
        "active_runs": len(active),
    }


@router.get("/providers", response_model=StaffProvidersResponse, response_model_exclude_none=True)
async def providers(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Provider availability, chat-only/unattended capabilities, and CLI versions (#1697)."""
    return {
        "providers": available_providers(),
        "chat_only_providers": chat_only_providers(),
        "unattended_providers": unattended_providers(),
        "provider_versions": await asyncio.to_thread(provider_versions, ADAPTERS),
    }


@router.get("/board", response_model=StaffBoardResponse, response_model_exclude_none=True)
async def board(
    local: bool = Query(
        default=False,
        description="Return only this node's board (used by hub fan-out).",
    ),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Status monitor. With peers configured this is the fleet-wide view (#1195)."""
    runner = get_runner()
    mine = staff_fleet.local_board(runner)
    peers = {} if local else staff_fleet.peer_nodes()
    if not peers:
        return mine
    return await staff_fleet.aggregate_board(mine, peers)


@router.get("/summary", response_model=StaffSummaryResponse, response_model_exclude_none=True)
async def summary(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """The one-call brief for Barb and Orchestrator (#1195).

    Flat payload: what is in flight fleet-wide, what needs attention (failed or
    blocked in the last 24 h on this node), spend today, provider availability
    per machine, active holds, late/dead scheduled roles (#1209) and the
    roster with schedules.
    """
    return await build_staff_summary()


@router.get("/github-app/preflight")
async def github_app_preflight(
    _peer: Principal = Depends(require_scope("fleet.read")),
) -> dict[str, Any]:
    """Compare staff role GitHub App permission needs to the live installation grant (#2038)."""
    runner = get_runner()
    return run_preflight_and_cache(runner.roles())


@router.get("/audit", response_model=StaffAuditListResponse, response_model_exclude_none=True)
async def list_audit(
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    principal: str | None = Query(default=None, max_length=120),
    thread_id: str | None = Query(default=None, max_length=120),
    run_id: str | None = Query(default=None, max_length=120),
    action: str | None = Query(default=None, max_length=60),
    surface: str | None = Query(default=None, max_length=30),
    target: str | None = Query(default=None, max_length=120),
    since: str | None = Query(default=None, max_length=40),
    format: str | None = Query(default=None),
    _peer: Principal = Depends(require_scope("staff.audit.read")),
) -> Any:
    """Retrieve durable append-only staff audit rows (SC-A8, Issue #1298)."""
    store = get_audit_store()
    items = [
        ("principal", principal),
        ("thread_id", thread_id),
        ("run_id", run_id),
        ("action", action),
        ("surface", surface),
        ("target", target),
        ("since", since),
    ]
    filt = {k: v for k, v in items if v is not None}
    entries = store.list_entries(limit=limit, offset=offset, **filt)
    if format == "csv":
        headers = {"Content-Disposition": "attachment; filename=staff_audit.csv"}
        return Response(content=export_audit_csv(entries), media_type="text/csv", headers=headers)
    if format == "ndjson":
        return Response(content=export_audit_ndjson(entries), media_type="application/x-ndjson")
    return {
        "entries": [e.to_dict() for e in entries],
        "count": len(entries),
        "total": store.count_entries(**filt),
    }


@router.get("/runs", response_model=StaffRunsResponse, response_model_exclude_none=True)
async def list_runs(
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    role: str | None = Query(default=None, max_length=60),
    status: str | None = Query(default=None, max_length=20),
    since: str | None = Query(default=None, max_length=40),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    if status is not None and status not in RUN_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(RUN_STATUSES)}")
    runs = get_runner().store.list_runs(limit=limit, role=role, status=status, since=since)
    return {"runs": [r.to_dict() for r in runs], "count": len(runs)}


@router.get(
    "/runs/{run_id}",
    response_model=StaffRunDetailResponse,
    response_model_exclude_none=True,
)
async def get_run(
    run_id: str,
    events: int = Query(default=200, ge=0, le=MAX_LIMIT),
    local: bool = Query(default=False, include_in_schema=False),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    store = get_runner().store
    rec = store.get_run(run_id)
    if rec is None:
        if not (local or staff_fleet.is_fleet_peer(_peer)):
            from staff.remote_runs import (
                RemoteRunLookupUnavailable,
                RemoteRunNotFound,
                find_remote_run,
                proxy_remote_run,
            )  # noqa: PLC0415

            try:
                remote = await find_remote_run(run_id)
            except RemoteRunLookupUnavailable as exc:
                raise HTTPException(status_code=503, detail="peer run lookup unavailable") from exc
            if remote is not None:
                peer_name, peer_url = remote
                try:
                    return await proxy_remote_run(
                        run_id,
                        events,
                        peer_name,
                        peer_url,
                        caller_id=getattr(_peer, "id", ""),
                    )
                except RemoteRunNotFound as exc:
                    raise HTTPException(status_code=404, detail="run not found") from exc
        raise HTTPException(status_code=404, detail="run not found")
    evs = store.events_after(run_id, 0, limit=MAX_LIMIT)
    attempts = [a.to_dict() for a in store.get_attempts(run_id)]
    return {
        "run": rec.to_dict(),
        "events": evs[-events:] if events else [],
        "attempts": attempts,
        "logs_ref": f"/api/v1/diagnostics/logs?run_id={run_id}",
    }


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: str,
    after: int = Query(default=0, ge=0),
    local: bool = Query(default=False, description="Do not search peer nodes for this run."),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> StreamingResponse:
    """SSE feed of run events. Ends with an ``end`` event when the run finishes."""
    store = get_runner().store
    if store.get_run(run_id) is None:
        if not (local or staff_fleet.is_fleet_peer(_peer)):
            from staff.remote_runs import (
                RemoteRunLookupUnavailable,
                find_remote_run,
                stream_remote_run,
            )  # noqa: PLC0415

            try:
                remote = await find_remote_run(run_id)
            except RemoteRunLookupUnavailable as exc:
                raise HTTPException(status_code=503, detail="peer run lookup unavailable") from exc
            if remote is not None:
                peer_name, peer_url = remote
                return StreamingResponse(
                    stream_remote_run(
                        run_id,
                        after,
                        peer_name,
                        peer_url,
                        caller_id=getattr(_peer, "id", ""),
                    ),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
        raise HTTPException(status_code=404, detail="run not found")

    async def _gen():  # type: ignore[no-untyped-def]
        cursor = after
        idle = 0.0
        while True:
            batch = store.events_after(run_id, cursor, limit=200)
            for ev in batch:
                cursor = int(ev["seq"])
                yield f"id: {cursor}\nevent: {ev['kind']}\ndata: {json.dumps(ev)}\n\n"
            rec = store.get_run(run_id)
            if rec is None or rec.status not in ACTIVE_STATUSES:
                payload = rec.to_dict() if rec else {"id": run_id, "status": "missing"}
                yield f"event: end\ndata: {json.dumps(payload)}\n\n"
                return
            if not batch:
                idle += STREAM_POLL_SECONDS
                if idle >= STREAM_IDLE_TIMEOUT_SECONDS:
                    yield 'event: end\ndata: {"reason": "idle timeout"}\n\n'
                    return
                if int(idle) % 15 == 0 and idle > 0:
                    yield ": keep-alive\n\n"
            else:
                idle = 0.0
            await asyncio.sleep(STREAM_POLL_SECONDS)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class GithubCredentialResponse(BaseModel):
    token: str
    expires_at: float


def _assert_staff_run_principal(principal: Principal, run_id: str) -> None:
    parts = principal.id.split(":")
    if len(parts) != 3 or parts[0] != "staff" or parts[2] != run_id:
        raise HTTPException(status_code=403, detail="token is not authorized for this run")


@router.post(
    "/runs/{run_id}/github-credential",
    response_model=GithubCredentialResponse,
    response_model_exclude_none=True,
)
async def refresh_github_credential(
    run_id: str,
    caller: Principal = Depends(require_scope("staff.github_credential")),
) -> GithubCredentialResponse:
    """Mint a fresh GitHub App installation token for an active staff run (#2038)."""
    _assert_staff_run_principal(caller, run_id)
    cred = mint_github_credential_for_run(run_id)
    return GithubCredentialResponse(token=cred.token, expires_at=cred.expires_at)


@router.post(
    "/runs/{run_id}/cancel",
    response_model=StaffCancelResponse,
    response_model_exclude_none=True,
)
async def cancel_run(
    run_id: str,
    local: bool = Query(default=False, description="Do not search peer nodes for this run."),
    caller: Principal = Depends(require_scope("staff.cancel")),
) -> dict[str, Any]:
    runner = get_runner()
    if runner.store.get_run(run_id) is None:
        if not (local or staff_fleet.is_fleet_peer(caller)):
            from staff.remote_runs import (
                RemoteRunLookupUnavailable,
                RemoteRunNotFound,
                find_remote_run,
                proxy_remote_cancel,
            )  # noqa: PLC0415

            try:
                remote = await find_remote_run(run_id)
            except RemoteRunLookupUnavailable as exc:
                raise HTTPException(status_code=503, detail="peer run lookup unavailable") from exc
            if remote is not None:
                peer_name, peer_url = remote
                try:
                    return await proxy_remote_cancel(
                        run_id,
                        peer_name,
                        peer_url,
                        caller_id=getattr(caller, "id", ""),
                    )
                except RemoteRunNotFound as exc:
                    raise HTTPException(status_code=404, detail="run not found") from exc
                except RemoteRunLookupUnavailable as exc:
                    raise HTTPException(status_code=503, detail="peer run lookup unavailable") from exc
        raise HTTPException(status_code=404, detail="run not found")
    ok = runner.cancel(run_id)
    caller_str = format_caller(caller)
    record_audit(
        action="cancel",
        target=f"run:{run_id}",
        principal=caller_str,
        surface="api",
        run_id=run_id,
        outcome="success" if ok else "not_found",
        detail={"cancelled": ok},
        fail_closed=True,
    )
    log.info("staff: cancel %s by %s → %s", run_id, caller_str, ok)
    rec = runner.store.get_run(run_id)
    return {"cancelled": ok, "run": rec.to_dict() if rec else None}


@router.post(
    "/{role}/run",
    response_model=StaffDispatchResponse,
    response_model_exclude_unset=True,
)
async def dispatch(
    role: str,
    body: RunBody,
    request: Request,
    force_duplicate: bool = Query(default=False, include_in_schema=False),
    caller: Principal = Depends(require_staff_run_dispatch()),
) -> dict[str, Any]:
    """Dispatch a staff run on this node, or preview it with ``dry_run``.

    Precondition: the role exists and is dispatchable; the provider is allowed
    for the role; one of issue/pr/prompt is given. Postcondition: on a real
    dispatch a ``queued`` run row exists before the response is returned. When
    the role carries ``strategy.consolidate_when`` and a repo is given, the
    PR-consolidation decision (#1213) is evaluated first and lands in
    ``plan.consolidation`` and the run's ``strategy_mode``.
    """
    return await run_dispatch(role, body, request, caller, force_duplicate=force_duplicate)
