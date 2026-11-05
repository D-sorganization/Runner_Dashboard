"""HTTP router for the per-agent inbox (``/api/v1/agent-inbox``, #3931 S3).

Per-agent send/list/ack mailbox, distinct from GitHub-issue-backed
``/api/coordination/*``. ``GET /overdue`` feeds the Barb overdue-ack alert.
Auth: reads use ``require_fleet_peer``; writes need ``coordination.write``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated, TypeVar

from agent_inbox_store import AgentInboxStore, InboxConflictError, InboxMessageRecord, default_agent_inbox_db_path
from fastapi import APIRouter, Depends, HTTPException, Query
from identity import require_fleet_peer, require_scope
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/agent-inbox", tags=["agent-inbox"])
_require_writer = require_scope("coordination.write")
FleetPeer = Annotated[str, Depends(require_fleet_peer)]
_T = TypeVar("_T")


def _store() -> AgentInboxStore:
    return AgentInboxStore(default_agent_inbox_db_path())


def _or_409(call: Callable[[], _T]) -> _T:
    """Run a store write, turning ``InboxConflictError`` into a 409."""
    try:
        return call()
    except InboxConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class MessageOut(BaseModel):
    message_id: str
    to_agent: str
    from_agent: str
    body: str
    sent_at: float
    requires_ack: bool
    ack_deadline_seconds: float | None
    acked_at: float | None

    @classmethod
    def from_record(cls, rec: InboxMessageRecord) -> MessageOut:
        return cls(**asdict(rec))


class SendBody(BaseModel):
    to: str = Field(min_length=1, max_length=128)
    from_: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=8000)
    requires_ack: bool = False
    ack_deadline_seconds: float | None = Field(default=None, gt=0, le=86400)


class AckBody(BaseModel):
    message_id: str = Field(min_length=1, max_length=64)
    agent: str = Field(min_length=1, max_length=128)


@router.post("/send")
def send_message(body: SendBody, _who: str = Depends(_require_writer)) -> MessageOut:
    rec = _store().send(
        body.to, body.from_, body.body, requires_ack=body.requires_ack, ack_deadline_seconds=body.ack_deadline_seconds
    )
    return MessageOut.from_record(rec)


@router.get("/list")
def list_messages(
    _peer: FleetPeer,
    agent: str = Query(min_length=1, max_length=128),
    include_acked: bool = Query(default=False),
) -> list[MessageOut]:
    recs = _store().list_for(agent, include_acked=include_acked)
    return [MessageOut.from_record(rec) for rec in recs]


@router.post("/ack")
def ack_message(body: AckBody, _who: str = Depends(_require_writer)) -> MessageOut:
    rec = _or_409(lambda: _store().ack(body.message_id, body.agent))
    return MessageOut.from_record(rec)


@router.get("/overdue")
def overdue_messages(_peer: FleetPeer) -> list[MessageOut]:
    recs = _store().overdue()
    return [MessageOut.from_record(rec) for rec in recs]
