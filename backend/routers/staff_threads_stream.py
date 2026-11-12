"""SSE stream endpoint for Staff Conversation & Thread API v1 (SC-B3 #1306).

Split out of routers.staff_threads (#3705 follow-up) to keep that file under
the repo's 500-line soft cap; this endpoint has no dependency on the rest of
that router beyond the shared store/bus helpers.
"""

# ruff: noqa: B008
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from identity import Principal, require_scope
from routers.staff_threads import _get_store_or_503
from staff.thread_bus import get_thread_bus
from staff.thread_helpers import thread_readable_by

router = APIRouter(tags=["staff-threads"])

STREAM_HEARTBEAT_SECONDS = 15.0


@router.get(
    "/threads/{thread_id}/stream",
)
@router.get("/threads/{thread_id}/events", include_in_schema=False)
async def stream_thread(
    thread_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    since_seq: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    follow: bool = Query(default=True, description="Keep stream open for live updates"),
    caller: Principal = Depends(require_scope("staff.read")),
) -> StreamingResponse:
    """SSE feed for thread updates (token deltas, message completions, proposals)."""
    store = _get_store_or_503()
    thread = store.get_thread(thread_id)
    if not thread or not thread_readable_by(thread, caller):
        raise HTTPException(status_code=404, detail="thread not found")

    bus = get_thread_bus()
    queue = await bus.subscribe(thread_id)

    # Determine replay point
    replay_after = 0
    if since_seq is not None:
        replay_after = since_seq
    elif last_event_id is not None:
        try:
            replay_after = int(last_event_id)
        except ValueError:
            replay_after = 0

    async def _event_generator():  # type: ignore[no-untyped-def]
        try:
            # 1. Replay missed messages from SQLite
            missed = store.list_messages(thread_id, limit=limit, since_seq=replay_after)
            for m in missed:
                yield f"id: {m.seq}\nevent: message\ndata: {json.dumps(m.to_dict())}\n\n"

            if not follow:
                return

            # 2. Live event loop
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=STREAM_HEARTBEAT_SECONDS)
                    eid = ev.get("id")
                    eid_str = f"id: {eid}\n" if eid is not None else ""
                    yield f"{eid_str}event: {ev['event']}\ndata: {json.dumps(ev['data'])}\n\n"
                except TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            await bus.unsubscribe(thread_id, queue)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
