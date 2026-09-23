"""Fleet Coordination API (epic #1192, issue #1229). Contract: ``docs/coordination-api.md``.

Routes (all under ``/api/coordination``):
  GET  /sessions            Board sessions (all repos, ``?repo=`` filter) + staff runs in flight.
  GET  /inbox               Messages and conflicts for ``?session=``.
  POST /presence            Register or renew presence (RM ``agent_communicate register``).
  POST /presence/release    Drop presence (RM ``release``).
  POST /messages            Send to a session or ``*`` (RM ``send``).
  POST /messages/ack        Acknowledge receipt (RM ``ack``).
  GET  /claims              Is ``?repo=&issue=`` leased (RM ``check_agent_claim``)?
  POST /claims              Check then lease; 409 when another agent holds it.
  POST /claims/release      Release the lease (RM ``release_agent_lease``).
  GET  /briefing            Priorities, holds, sessions, staff runs, claim protocol and fleet rules.

Auth: reads ``require_fleet_peer``; writes ``require_coordination_writer``
(scope ``coordination.write`` or the loopback orchestrator peer) plus the
CSRF header. Reads degrade to ``available: false`` with HTTP 200; failed RM
writes return 502 with the script's ``error`` and ``guidance``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from coordination import service
from coordination.auth import Caller, require_coordination_writer
from coordination.board import BOARD_REPO, RMScriptError
from coordination.briefing import build_briefing
from coordination.claims import ClaimHeldError
from coordination.models import (
    AGENT_PATTERN,
    REPO_PATTERN,
    SESSION_PATTERN,
    AckBody,
    ClaimBody,
    ClaimReleaseBody,
    MessageBody,
    PresenceBody,
    ReleaseBody,
)
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from identity import require_fleet_peer

log = logging.getLogger("dashboard.coordination")
router = APIRouter(prefix="/api/coordination", tags=["coordination"])
WRITER = Depends(require_coordination_writer)  # one write-auth dependency for every POST


async def _write(action: Callable[[], dict[str, Any]], caller: Caller, what: str) -> dict[str, Any]:
    """Run one RM write off the event loop and map its failures to HTTP (DRY for every POST)."""
    try:
        result = await run_in_threadpool(action)
    except ClaimHeldError as exc:
        raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
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
    return await service.sessions(repo)


@router.get("/inbox")
async def get_inbox(
    session: str = Query(pattern=SESSION_PATTERN),
    repo: str = Query(default=BOARD_REPO, pattern=REPO_PATTERN),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    return await run_in_threadpool(service.inbox, session, repo)


@router.post("/presence")
async def post_presence(body: PresenceBody, caller: Caller = WRITER) -> dict[str, Any]:
    return await _write(lambda: service.register_presence(body, caller), caller, "presence register")


@router.post("/presence/release")
async def post_presence_release(body: ReleaseBody, caller: Caller = WRITER) -> dict[str, Any]:
    return await _write(lambda: service.release_presence(body), caller, "presence release")


@router.post("/messages")
async def post_message(body: MessageBody, caller: Caller = WRITER) -> dict[str, Any]:
    return await _write(lambda: service.send_message(body), caller, "message send")


@router.post("/messages/ack")
async def post_message_ack(body: AckBody, caller: Caller = WRITER) -> dict[str, Any]:
    return await _write(lambda: service.ack_message(body), caller, "message ack")


@router.get("/claims")
async def get_claim(
    repo: str = Query(pattern=REPO_PATTERN),
    issue: int = Query(gt=0),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    return await run_in_threadpool(service.check_claim, repo, issue)


@router.post("/claims")
async def post_claim(body: ClaimBody, caller: Caller = WRITER) -> dict[str, Any]:
    return await _write(lambda: service.claim_issue(body, caller), caller, f"claim {body.repo}#{body.issue}")


@router.post("/claims/release")
async def post_claim_release(body: ClaimReleaseBody, caller: Caller = WRITER) -> dict[str, Any]:
    return await _write(lambda: service.release_claim(body, caller), caller, f"release {body.repo}#{body.issue}")


@router.get("/briefing")
async def get_briefing(
    repo: str = Query(pattern=REPO_PATTERN),
    agent: str | None = Query(default=None, pattern=AGENT_PATTERN),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    return await build_briefing(repo, agent)
