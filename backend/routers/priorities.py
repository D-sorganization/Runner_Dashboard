"""Fleet priorities API (issue #1227, epic #1192 — Fleet Coordination API contract v1).

Routes (all under ``/api/priorities``):
  GET ""                   Latest board consensus, active directives, portfolios.
  GET /meetings            Dated board-meeting folders with the files each has.
  GET /meetings/{date}     One meeting: parsed consensus + raw packet/instructions markdown.
  GET /directives          Active (unexpired) operator directives + the list ``version``.
  PUT /directives          Replace the directive list (409 when ``version`` is stale).

Auth: reads use ``require_fleet_peer``; the PUT uses ``coordination.auth.require_priorities_writer``
(principal with ``priorities.write`` — operators, not bots — or a loopback orchestrator peer) plus
the CSRF header enforced by middleware. ``set_by`` always comes from the authenticated caller (#1243).
Logic lives in ``priorities.service``.
"""

from __future__ import annotations

import logging
from typing import Any

from coordination.auth import Caller, require_priorities_writer
from fastapi import APIRouter, Depends, HTTPException
from fastapi import Path as PathParam
from identity import require_fleet_peer
from priorities import service
from priorities.directives import (
    MAX_PRIORITY,
    MAX_SET_BY,
    MAX_TEXT,
    MIN_PRIORITY,
    DirectivesList,
    VersionConflictError,
    get_directives,
)
from priorities.sources import DATE_PATTERN, valid_meeting_date
from pydantic import BaseModel, Field

log = logging.getLogger("dashboard.priorities")
router = APIRouter(prefix="/api/priorities", tags=["priorities"])

MAX_DIRECTIVES = 100


class DirectiveBody(BaseModel):
    """One directive as sent by an operator or agent. DbC: the JSON shape of ``priorities.directives.Directive``."""

    id: str | None = Field(default=None, max_length=64)
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    repo: str = Field(default="*", max_length=100)
    priority: int = Field(default=3, ge=MIN_PRIORITY, le=MAX_PRIORITY)
    expires: str | None = Field(default=None, max_length=40)
    set_by: str | None = Field(default=None, max_length=MAX_SET_BY, description="Ignored; the server sets it.")
    set_on: str | None = Field(default=None, max_length=40)


class DirectivesBody(BaseModel):
    directives: list[DirectiveBody] = Field(max_length=MAX_DIRECTIVES)
    version: str | None = Field(
        default=None, max_length=64, description="``version`` from the GET you edited; 409 when stale."
    )


def _listing(directives: DirectivesList) -> dict[str, Any]:
    """Active directives plus the stored list's version. Never exposes the storage path."""
    return {"directives": [d.to_dict() for d in directives.active()], "version": directives.version()}


@router.get("")
async def get_priorities(_peer: str = Depends(require_fleet_peer)) -> dict[str, Any]:
    """Latest board consensus + directives + portfolios; ``available:false`` with ``reason`` when no board."""
    return service.priorities_snapshot()


@router.get("/meetings")
async def list_meetings(_peer: str = Depends(require_fleet_peer)) -> dict[str, Any]:
    return service.meetings_index()


@router.get("/meetings/{meeting_date}")
async def get_meeting(
    meeting_date: str = PathParam(pattern=DATE_PATTERN, description="Meeting date, YYYY-MM-DD."),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    if not valid_meeting_date(meeting_date):
        raise HTTPException(status_code=422, detail=f"{meeting_date!r} is not a calendar date")
    detail = service.meeting_detail(meeting_date)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"no board meeting on {meeting_date}")
    return detail


@router.get("/directives")
async def get_directives_route(_peer: str = Depends(require_fleet_peer)) -> dict[str, Any]:
    return _listing(get_directives())


@router.put("/directives")
async def put_directives(
    body: DirectivesBody,
    caller: Caller = Depends(require_priorities_writer),  # noqa: B008
) -> dict[str, Any]:
    """Replace the directive list. ``set_by`` is the authenticated caller (body values are ignored)."""
    directives = get_directives()
    items = [d.model_dump(exclude_none=True, exclude={"set_by"}) for d in body.directives]
    try:
        directives.replace(items, author=caller.label, expected_version=body.version)
    except VersionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    log.info("priorities: directives replaced by %s (%d directives)", caller.label, len(items))
    return _listing(directives)
