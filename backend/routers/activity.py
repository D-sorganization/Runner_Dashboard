"""Bounded read and atomic ingestion API for fleet activity metadata."""

from __future__ import annotations

from activity import MAX_BATCH_BYTES, MAX_BATCH_EVENTS, ActivityEnvelope, ActivityPage, get_activity_store
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from identity import Principal, require_scope
from pydantic import TypeAdapter, ValidationError
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/api/v1/activity", tags=["activity"])


@router.post("/events", response_model=list[ActivityEnvelope])
async def ingest_activity(
    request: Request,
    caller: Principal = Depends(require_scope("activity.write")),  # noqa: B008
) -> list[ActivityEnvelope]:
    """Bound the wire body before parsing; never trust source-side redaction."""
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BATCH_BYTES:
            raise HTTPException(413, "activity batch exceeds byte limit")
        body.extend(chunk)
    try:
        events = TypeAdapter(list[ActivityEnvelope]).validate_json(bytes(body))
    except ValidationError as exc:
        # Do not reflect unredacted input through validation errors.
        raise HTTPException(422, "invalid activity batch") from exc
    if len(events) > MAX_BATCH_EVENTS:
        raise HTTPException(413, "activity batch exceeds event limit")
    return await run_in_threadpool(lambda: get_activity_store().ingest(events, principal=caller.id))


@router.get("", response_model=ActivityPage)
def read_activity(
    since: int = Query(default=0, ge=0),
    repo: str | None = Query(default=None, max_length=128),
    node: str | None = Query(default=None, max_length=128),
    session_id: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=100, ge=1, le=500),
    _caller: Principal = Depends(require_scope("activity.read")),  # noqa: B008
) -> ActivityPage:
    """Return a bounded cursor page; readers cannot mutate stored metadata."""
    return get_activity_store().read(since=since, repo=repo, node=node, session_id=session_id, limit=limit)
