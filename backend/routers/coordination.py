"""Fleet Coordination API (epic #1192, issue #1229). Contract: ``docs/coordination-api.md``.

Routes (all under ``/api/coordination``):
  GET  /sessions            Board + staff runs + hub-registered lanes (``?repo=`` filter); staff_runs also listed.
  POST /sessions/register   Register/heartbeat a hub-local lane session (Cursor cloud, Barb).
  GET  /inbox               Messages and conflicts for ``?session=``.
  POST /presence            Register or renew presence (RM ``agent_communicate register``).
  POST /presence/release    Drop presence (RM ``release``).
  POST /messages            Send to a session or ``*`` (RM ``send``).
  POST /messages/ack        Acknowledge receipt (RM ``ack``).
  GET  /claims              Is ``?repo=&issue=`` leased (RM ``check_agent_claim``)?
  POST /claims              Check then lease; 409 when another agent holds it.
  POST /claims/release      Release the lease (RM ``release_agent_lease``).
  GET  /jobs                Claimable work (``?repo=&limit=&include_excluded=``); read-only (epic #2781 S1).
  POST /jobs/claim          Atomically claim a listed job with fencing (#2784).
  POST /jobs/release        Release a job-board claim (current fence required, #2784).
  POST /jobs/heartbeat      Renew a job-board claim TTL (#2785).
  GET  /jobs/releases       Recent job-board claim releases (activity projection; slice toward #2785).
  GET  /briefing            Priorities, holds, sessions, staff runs, claim protocol and fleet rules.
  GET  /postman/reports     Newest postman dry-run reports (what it would deliver; nothing is sent).
  POST /lane-prompts/force-release  Operator/admin release of a live checkout without its fence (#4677).

Auth: reads ``require_fleet_peer``; writes ``require_coordination_writer``
(scope ``coordination.write`` or the loopback orchestrator peer) plus the
CSRF header. Force-release uses ``require_priorities_writer`` (operator/admin).
A bot principal ``agent-<name>`` may only act as ``<name>`` on
sessions ``<name>-*`` (403 otherwise). Reads degrade to ``available: false``
with HTTP 200; failed RM writes return 502 with the script's ``error`` and
``guidance``; 409 when the issue is held or the sender has no live presence.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from coordination import job_releases as job_releases_mod
from coordination import jobs as jobs_mod
from coordination import lane_prompts as lane_prompts_mod
from coordination import postman_runner, service
from coordination.auth import Caller, require_coordination_writer, require_priorities_writer
from coordination.board import BOARD_REPO, RMScriptError
from coordination.briefing import build_briefing
from coordination.claims import AgingPrBlockedError, ClaimHeldError
from coordination.claims_check_cache import ClaimCheckUnavailable
from coordination.fleet_efficiency import BriefValidationError, fleet_efficiency_metadata
from coordination.jobs import JobIneligibleError, StaleFenceError
from coordination.models import (
    AGENT_PATTERN,
    REPO_PATTERN,
    SESSION_PATTERN,
    AckBody,
    ClaimBody,
    ClaimReleaseBody,
    HubSessionRegisterBody,
    JobClaimBody,
    JobHeartbeatBody,
    JobReleaseBody,
    MessageBody,
    PresenceBody,
    ReleaseBody,
)
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from identity import require_fleet_peer
from pydantic import BaseModel, Field
from routers.repo_query import bare_repo_query as _repo

log = logging.getLogger("dashboard.coordination")
router = APIRouter(prefix="/api/coordination", tags=["coordination"])
WRITER = Depends(require_coordination_writer)  # one write-auth dependency for every POST
OPERATOR = Depends(require_priorities_writer)  # force-release: operator/admin only (#4677)


async def _write(action: Callable[[], dict[str, Any]], caller: Caller, what: str) -> dict[str, Any]:
    """Run one RM write off the event loop and map its failures to HTTP (DRY for every POST)."""
    try:
        result = await run_in_threadpool(action)
    except (ClaimHeldError, AgingPrBlockedError, service.NotRegisteredError) as exc:
        raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
    except (JobIneligibleError, StaleFenceError) as exc:
        raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
    except service.ImpersonationError as exc:
        log.warning("coordination: %s refused for %s: %s", what, caller.label, exc)
        raise HTTPException(status_code=403, detail={"error": str(exc)}) from exc
    except BriefValidationError as exc:
        raise HTTPException(status_code=422, detail={"error": str(exc)}) from exc
    except RMScriptError as exc:
        log.warning("coordination: %s by %s failed: %s", what, caller.label, exc.error)
        raise HTTPException(status_code=502, detail=exc.to_detail()) from exc
    except service.MissingAgentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    log.info("coordination: %s by %s", what, caller.label)
    return result


@router.get("/sessions")
async def get_sessions(
    repo: str | None = Query(default=None, pattern=REPO_PATTERN),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    return await service.sessions(_repo(repo))


@router.post("/sessions/register")
async def post_sessions_register(body: HubSessionRegisterBody, caller: Caller = WRITER) -> dict[str, Any]:
    """Register or heartbeat a hub-local session for cloud lanes and external agents (#4458)."""
    return await _write(lambda: service.register_hub_session(body, caller), caller, "hub session register")


@router.get("/inbox")
async def get_inbox(
    session: str = Query(pattern=SESSION_PATTERN),
    repo: str = Query(default=BOARD_REPO, pattern=REPO_PATTERN),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    return await run_in_threadpool(service.inbox, session, _repo(repo) or BOARD_REPO)


@router.post("/presence")
async def post_presence(body: PresenceBody, caller: Caller = WRITER) -> dict[str, Any]:
    return await _write(lambda: service.register_presence(body, caller), caller, "presence register")


@router.post("/presence/release")
async def post_presence_release(body: ReleaseBody, caller: Caller = WRITER) -> dict[str, Any]:
    return await _write(lambda: service.release_presence(body, caller), caller, "presence release")


@router.post("/messages")
async def post_message(body: MessageBody, caller: Caller = WRITER) -> dict[str, Any]:
    return await _write(lambda: service.send_message(body, caller), caller, "message send")


@router.post("/messages/ack")
async def post_message_ack(body: AckBody, caller: Caller = WRITER) -> dict[str, Any]:
    return await _write(lambda: service.ack_message(body, caller), caller, "message ack")


@router.get("/claims")
async def get_claim(
    repo: str = Query(pattern=REPO_PATTERN),
    issue: int = Query(gt=0),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    try:
        return await run_in_threadpool(service.check_claim, _repo(repo) or "", issue)
    except ClaimCheckUnavailable as exc:
        retry = max(1, int(exc.retry_after))
        raise HTTPException(
            status_code=503,
            detail=exc.to_detail(),
            headers={"Retry-After": str(retry)},
        ) from exc


@router.post("/claims")
async def post_claim(body: ClaimBody, caller: Caller = WRITER) -> dict[str, Any]:
    return await _write(lambda: service.claim_issue(body, caller), caller, f"claim {body.repo}#{body.issue}")


@router.post("/claims/release")
async def post_claim_release(body: ClaimReleaseBody, caller: Caller = WRITER) -> dict[str, Any]:
    return await _write(lambda: service.release_claim(body, caller), caller, f"release {body.repo}#{body.issue}")


@router.get("/jobs")
async def get_jobs(
    repo: str | None = Query(default=None, pattern=REPO_PATTERN),
    limit: int = Query(default=50, ge=1, le=200),
    include_excluded: bool = Query(default=False),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    return await run_in_threadpool(jobs_mod.list_jobs, repo=_repo(repo), limit=limit, include_excluded=include_excluded)


@router.post("/jobs/claim")
async def post_jobs_claim(body: JobClaimBody, caller: Caller = WRITER) -> dict[str, Any]:
    return await _write(lambda: service.claim_job(body, caller), caller, f"jobs claim {body.repo}#{body.issue}")


@router.post("/jobs/release")
async def post_jobs_release(body: JobReleaseBody, caller: Caller = WRITER) -> dict[str, Any]:
    return await _write(lambda: service.release_job(body, caller), caller, f"jobs release {body.repo}#{body.issue}")


@router.post("/jobs/heartbeat")
async def post_jobs_heartbeat(body: JobHeartbeatBody, caller: Caller = WRITER) -> dict[str, Any]:
    return await _write(lambda: service.heartbeat_job(body, caller), caller, f"jobs heartbeat {body.repo}#{body.issue}")


@router.get("/jobs/releases")
async def get_job_releases(
    since: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=500),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    try:
        job_releases_mod.parse_since(since)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": f"since is not ISO-8601: {exc}"}) from exc
    return await run_in_threadpool(job_releases_mod.list_job_releases, since=since, limit=limit)


@router.get("/fleet-efficiency")
async def get_fleet_efficiency(
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    """Read-only fleet efficiency knobs: wake coalescing, brief cap, planned reset (#4562)."""
    from coordination.service import now_iso

    return {**fleet_efficiency_metadata(), "generated_at": now_iso()}


@router.get("/briefing")
async def get_briefing(
    repo: str | None = Query(default=None, pattern=REPO_PATTERN),
    agent: str | None = Query(default=None, pattern=AGENT_PATTERN),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    return await build_briefing(_repo(repo), agent)


@router.get("/postman/reports")
async def get_postman_reports(
    limit: int = Query(default=10, ge=1, le=postman_runner.REPORT_KEEP),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    """Read-only: the postman runs in dry run only, so ``live`` is always False."""
    reports = await run_in_threadpool(postman_runner.read_reports, None, limit)
    return {"live": False, "reports": reports}


class LaneCheckoutBody(BaseModel):
    session_id: str = Field(min_length=1, max_length=128, pattern=SESSION_PATTERN)
    idempotency_key: str = Field(min_length=1, max_length=128)
    lane_id: str | None = Field(default=None, max_length=64)
    role: str | None = Field(default=None, max_length=64)
    provider: str | None = Field(default=None, max_length=32)
    host: str | None = Field(default=None, max_length=64)
    ttl_sec: int = Field(default=3600, ge=60, le=14400)


class LaneHeartbeatBody(BaseModel):
    checkout_id: str = Field(min_length=1, max_length=64)
    fence_token: int = Field(ge=1)
    session_id: str = Field(min_length=1, max_length=128, pattern=SESSION_PATTERN)
    ttl_sec: int = Field(default=3600, ge=60, le=14400)
    progress: bool = False


class LaneReleaseBody(BaseModel):
    checkout_id: str = Field(min_length=1, max_length=64)
    fence_token: int = Field(ge=1)
    session_id: str = Field(min_length=1, max_length=128, pattern=SESSION_PATTERN)
    reason: str = Field(default="completed", max_length=128)


class LaneForceReleaseBody(BaseModel):
    """Operator release. Exactly one of ``lane_id`` or ``checkout_id`` is required."""

    lane_id: str | None = Field(default=None, max_length=64)
    checkout_id: str | None = Field(default=None, max_length=64)
    reason: str = Field(default="operator force-release", min_length=1, max_length=128)


def _check_lane_session(caller: Caller, session_id: str) -> None:
    """Keep lane checkout writes within the caller's authenticated session namespace."""
    try:
        caller.check_session(session_id)
    except service.ImpersonationError as exc:
        raise HTTPException(status_code=403, detail={"error": str(exc)}) from exc


@router.get("/lane-prompts/catalog")
async def get_lane_prompt_catalog(
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    lanes = await run_in_threadpool(lane_prompts_mod.get_lane_prompt_store().list_catalog)
    return {"lanes": lanes}


@router.get("/lane-prompts/health")
async def get_lane_prompt_health(
    stall_heartbeat_sec: int = 900,
    stall_progress_sec: int = 1800,
    expired_window_sec: int = 86400,
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    return await run_in_threadpool(
        lane_prompts_mod.get_lane_prompt_store().health_summary,
        stall_heartbeat_sec=stall_heartbeat_sec,
        stall_progress_sec=stall_progress_sec,
        expired_window_sec=expired_window_sec,
    )


@router.post("/lane-prompts/checkout")
async def post_lane_prompt_checkout(body: LaneCheckoutBody, caller: Caller = WRITER) -> dict[str, Any]:
    _check_lane_session(caller, body.session_id)
    try:
        return await run_in_threadpool(
            lane_prompts_mod.get_lane_prompt_store().checkout,
            session_id=body.session_id,
            principal=caller.label.removeprefix("principal:"),
            idempotency_key=body.idempotency_key,
            lane_id=body.lane_id,
            role=body.role,
            provider=body.provider,
            host=body.host,
            ttl_sec=body.ttl_sec,
        )
    except lane_prompts_mod.LaneRoleUnavailableError as exc:
        detail: dict[str, Any] = {
            "role_unavailable": True,
            "reason": exc.reason,
            "requested_role": exc.requested_role,
        }
        if exc.assigned_role is not None:
            detail["assigned_role"] = exc.assigned_role
        raise HTTPException(status_code=409, detail=detail) from exc
    except lane_prompts_mod.LaneConflictError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc


@router.post("/lane-prompts/heartbeat")
async def post_lane_prompt_heartbeat(body: LaneHeartbeatBody, caller: Caller = WRITER) -> dict[str, Any]:
    _check_lane_session(caller, body.session_id)
    try:
        return await run_in_threadpool(
            lane_prompts_mod.get_lane_prompt_store().heartbeat,
            checkout_id=body.checkout_id,
            fence_token=body.fence_token,
            session_id=body.session_id,
            ttl_sec=body.ttl_sec,
            progress=body.progress,
        )
    except lane_prompts_mod.StaleLaneFenceError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc


@router.post("/lane-prompts/release")
async def post_lane_prompt_release(body: LaneReleaseBody, caller: Caller = WRITER) -> dict[str, Any]:
    _check_lane_session(caller, body.session_id)
    try:
        return await run_in_threadpool(
            lane_prompts_mod.get_lane_prompt_store().release,
            checkout_id=body.checkout_id,
            fence_token=body.fence_token,
            session_id=body.session_id,
            reason=body.reason,
        )
    except lane_prompts_mod.StaleLaneFenceError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc


@router.post("/lane-prompts/force-release")
async def post_lane_prompt_force_release(
    body: LaneForceReleaseBody,
    caller: Caller = OPERATOR,
) -> dict[str, Any]:
    """Release a dead checkout without the holder's session id or fence token."""
    if not body.lane_id and not body.checkout_id:
        raise HTTPException(status_code=422, detail={"error": "lane_id or checkout_id is required"})
    try:
        return await run_in_threadpool(
            lane_prompts_mod.get_lane_prompt_store().force_release,
            actor=caller.label,
            reason=body.reason,
            lane_id=body.lane_id,
            checkout_id=body.checkout_id,
        )
    except lane_prompts_mod.LaneNotActiveError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
