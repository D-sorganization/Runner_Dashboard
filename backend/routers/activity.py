"""Bounded read and atomic ingestion API for fleet activity metadata."""

from __future__ import annotations

from activity import MAX_BATCH_BYTES, MAX_BATCH_EVENTS, ActivityEnvelope, ActivityPage, get_activity_store
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from identity import Principal, require_scope
from pydantic import TypeAdapter, ValidationError
from staff.rate_limit import check_rate_limit
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/api/v1/activity", tags=["activity"])

# Unconnected-tier (off-tailnet) check-ins: strict kinds, each tied to the
# repo and one other identifying field so a check-in cannot be a free-form event.
CHECKIN_KINDS: frozenset[str] = frozenset({"started", "claimed", "pushed", "pr_opened", "blocked", "done", "heartbeat"})
_CHECKIN_REQUIRES: dict[str, str] = {"claimed": "issue", "pushed": "branch", "pr_opened": "pr"}
_CHECKIN_MAX_EVENTS = 20
_CHECKIN_MAX_BYTES = 65_536


async def _read_bounded_body(request: Request) -> list[ActivityEnvelope]:
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
    return events


def _validate_checkin(event: ActivityEnvelope) -> None:
    if event.source != "checkin":
        raise HTTPException(422, "checkin events must set source='checkin'")
    if event.kind not in CHECKIN_KINDS:
        raise HTTPException(422, f"unknown checkin kind: {event.kind!r}")
    if event.repo is None:
        raise HTTPException(422, "checkin events must set repo")
    required_field = _CHECKIN_REQUIRES.get(event.kind)
    if required_field is not None and getattr(event, required_field) is None:
        raise HTTPException(422, f"checkin kind {event.kind!r} requires {required_field}")


@router.post("/events", response_model=list[ActivityEnvelope])
async def ingest_activity(
    request: Request,
    caller: Principal = Depends(require_scope("activity.write")),  # noqa: B008
) -> list[ActivityEnvelope]:
    events = await _read_bounded_body(request)
    return await run_in_threadpool(lambda: get_activity_store().ingest(events, principal=caller.id))


@router.post("/checkins", response_model=list[ActivityEnvelope])
async def checkin_activity(
    request: Request,
    caller: Principal = Depends(require_scope("activity.checkin")),  # noqa: B008
) -> list[ActivityEnvelope]:
    """Write-only tier for agents off the tailnet (Cursor cloud, unconnected boxes).

    The ``activity.checkin`` scope is never combined with a read or chat scope,
    so a check-in credential cannot read activity, staff threads or anything else.
    """
    check_rate_limit("activity.checkin", caller)
    events = await _read_bounded_body(request)
    for event in events:
        _validate_checkin(event)
    return await run_in_threadpool(
        lambda: get_activity_store().ingest(
            events, principal=caller.id, max_events=_CHECKIN_MAX_EVENTS, max_bytes=_CHECKIN_MAX_BYTES
        )
    )


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
