"""FastAPI router for Staff Conversation & Thread API v1 (SC-B3, Issue #1306).

Endpoints:
- POST /api/v1/staff/threads: create conversation thread (direct, group, auto to Barb)
- GET /api/v1/staff/threads: list threads with filters (role, unread, status) and pagination
- GET /api/v1/staff/threads/{id}: thread details with messages
- PATCH /api/v1/staff/threads/{id}: rename or archive thread
- POST /api/v1/staff/threads/{id}/messages: send message with Idempotency-Key (202 Accepted)
- GET /api/v1/staff/threads/{id}/stream: SSE event stream with Last-Event-ID resume & heartbeats
- POST /api/v1/staff/threads/{id}/read: mark thread read for caller
- GET /api/v1/staff/inbox: list threads requiring user attention
"""

# ruff: noqa: B008
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from identity import Principal, format_caller, require_scope
from pydantic import BaseModel, Field
from staff.chat import run_chat_turn_in_background
from staff.conversations import (
    ConversationsUnavailableError,
    MessageRecord,
    get_conversation_store,
)
from staff.pagination import paginate_items
from staff.thread_bus import get_thread_bus

log = logging.getLogger("dashboard.staff.threads")

router = APIRouter(tags=["staff-threads"])

STREAM_HEARTBEAT_SECONDS = 15.0


def _get_store_or_503() -> Any:
    """Retrieve ConversationStore, failing closed with 503 if unavailable."""
    store = get_conversation_store()
    if not store.status.available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "conversations_unavailable",
                "message": store.status.banner_message or "Conversations unavailable",
                "retryable": True,
            },
        )
    return store


# ── REQUEST & RESPONSE MODELS ────────────────────────────────────────────────


class CreateThreadRequest(BaseModel):
    title: str | None = None
    kind: str = "direct"
    role: str | None = None
    participants: list[str] = Field(default_factory=list)


class UpdateThreadRequest(BaseModel):
    title: str | None = None
    status: str | None = None


class PostMessageRequest(BaseModel):
    body: str
    kind: str = "text"
    meta: dict[str, Any] = Field(default_factory=dict)


class AnswerNeedsInputRequest(BaseModel):
    answer: str


# ── THREAD MANAGEMENT ────────────────────────────────────────────────────────


@router.post(
    "/threads",
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_none=True,
)
async def create_thread(
    body: CreateThreadRequest,
    caller: Principal = Depends(require_scope("staff.chat")),
) -> dict[str, Any]:
    """Create a new conversation thread."""
    store = _get_store_or_503()
    caller_id = format_caller(caller)

    kind = body.kind or "direct"
    role = body.role
    if kind == "auto" or role == "auto":
        kind = "auto"
        role = "barb"

    participants = list(body.participants)
    if role and role not in participants:
        participants.append(role)
    if caller_id not in participants:
        participants.append(caller_id)

    title = body.title
    if not title:
        title = f"Conversation with {role.title()}" if role else "New conversation"

    try:
        rec = store.create_thread(
            title=title,
            kind=kind,
            participants=participants,
            created_by=caller_id,
        )
        return rec.to_dict()
    except ConversationsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/threads",
    response_model_exclude_none=True,
)
async def list_threads(
    role: str | None = Query(default=None),
    unread: bool | None = Query(default=None),
    thread_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
    caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """List conversation threads with optional filtering by role, status, unread."""
    store = _get_store_or_503()
    caller_id = format_caller(caller)

    try:
        all_threads = store.list_threads(
            status=thread_status,
            limit=200,
        )
    except ConversationsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if role:
        all_threads = [t for t in all_threads if role in t.participants]
    if unread:
        all_threads = [t for t in all_threads if t.unread_counters.get(caller_id, 0) > 0]

    dicts = [t.to_dict() for t in all_threads]
    page = paginate_items(
        dicts,
        cursor=cursor,
        limit=limit,
        key_fn=lambda d: (
            str(d.get("updated_at") or d.get("created_at") or ""),
            str(d.get("id", "")),
        ),
    )
    return {
        "items": page.items,
        "threads": page.items,
        "next_cursor": page.next_cursor,
        "prev_cursor": page.prev_cursor,
        "has_more": page.has_more,
    }


@router.get(
    "/threads/{thread_id}",
    response_model_exclude_none=True,
)
async def get_thread_detail(
    thread_id: str,
    since_seq: int = Query(default=0, ge=0),
    _caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Get thread metadata along with its historical messages."""
    store = _get_store_or_503()
    try:
        thread = store.get_thread(thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="thread not found")
        messages = store.list_messages(thread_id, limit=200, since_seq=since_seq)
    except ConversationsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "thread": thread.to_dict(),
        "messages": [m.to_dict() for m in messages],
    }


@router.patch(
    "/threads/{thread_id}",
    response_model_exclude_none=True,
)
async def update_thread(
    thread_id: str,
    body: UpdateThreadRequest,
    caller: Principal = Depends(require_scope("staff.chat")),
) -> dict[str, Any]:
    """Update thread metadata (rename or archive)."""
    store = _get_store_or_503()
    caller_id = format_caller(caller)

    try:
        thread = store.get_thread(thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="thread not found")

        if body.status == "archived":
            updated = store.archive_thread(thread_id, principal=caller_id)
        elif body.title:
            updated = store.update_thread(thread_id, title=body.title)
        else:
            updated = thread

        if not updated:
            raise HTTPException(status_code=404, detail="thread not found")
        return updated.to_dict()
    except ConversationsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ── MESSAGES & STREAMING ─────────────────────────────────────────────────────


@router.post(
    "/threads/{thread_id}/messages",
    status_code=status.HTTP_202_ACCEPTED,
    response_model_exclude_none=True,
)
async def post_message(
    thread_id: str,
    body: PostMessageRequest,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    caller: Principal = Depends(require_scope("staff.chat")),
) -> dict[str, Any]:
    """Post a user message to a thread; returns 202 with message and reply placeholder."""
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "missing_idempotency_key",
                "message": "Idempotency-Key header is required for posting messages",
            },
        )

    store = _get_store_or_503()
    caller_id = format_caller(caller)

    try:
        thread = store.get_thread(thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="thread not found")

        # Check existing idempotent message
        existing = store._conn.execute(
            "SELECT * FROM messages WHERE thread_id = ? AND idempotency_key = ?",
            (thread_id, idempotency_key.strip()),
        ).fetchone()

        if existing:
            user_msg = MessageRecord.from_row(existing)
            # Find reply placeholder or message in reply to this
            reply_row = store._conn.execute(
                "SELECT * FROM messages WHERE thread_id = ? AND seq = ?",
                (thread_id, user_msg.seq + 1),
            ).fetchone()
            reply_placeholder = (
                MessageRecord.from_row(reply_row).to_dict()
                if reply_row
                else {
                    "id": f"pending_{user_msg.id}",
                    "thread_id": thread_id,
                    "author_kind": "role",
                    "author": "barb",
                    "kind": "text",
                    "delivery": "pending",
                }
            )
            response.status_code = status.HTTP_200_OK
            response.headers["Idempotent-Replay"] = "true"
            return {
                "message": user_msg.to_dict(),
                "reply_placeholder": reply_placeholder,
            }

        # Persist user message
        user_msg = store.add_message(
            thread_id=thread_id,
            author_kind="user",
            author=caller_id,
            kind=body.kind or "text",
            body_md=body.body,
            meta=body.meta or {},
            idempotency_key=idempotency_key.strip(),
            delivery="complete",
        )

        # Resolve target role for reply placeholder
        target_role = "barb"
        for p in thread.participants:
            if p != caller_id and p:
                target_role = p
                break

        # Persist pending reply placeholder
        reply_placeholder_rec = store.add_message(
            thread_id=thread_id,
            author_kind="role",
            author=target_role,
            kind="text",
            body_md="",
            meta={"in_reply_to": user_msg.id},
            delivery="pending",
        )

        # Broadcast live events on thread bus
        bus = get_thread_bus()
        asyncio.create_task(bus.publish_message(thread_id, user_msg.to_dict()))
        asyncio.create_task(bus.publish_message(thread_id, reply_placeholder_rec.to_dict()))

        # Spawn background chat turn execution (SC-B4, #1307)
        asyncio.create_task(
            run_chat_turn_in_background(thread_id, user_msg.id, reply_placeholder_rec.id, target_role, caller_id)
        )

        return {
            "message": user_msg.to_dict(),
            "reply_placeholder": reply_placeholder_rec.to_dict(),
        }
    except ConversationsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/threads/{thread_id}/runs/{run_id}/answer",
    response_model_exclude_none=True,
)
async def answer_thread_run(
    thread_id: str,
    run_id: str,
    body: AnswerNeedsInputRequest,
    caller: Principal = Depends(require_scope("staff.chat")),
) -> dict[str, Any]:
    """Provide an answer to a needs_input question and trigger a continuation run."""
    store = _get_store_or_503()
    caller_id = format_caller(caller)
    from staff.run_link import answer_needs_input

    continuation = answer_needs_input(
        thread_id=thread_id,
        run_id=run_id,
        answer=body.answer,
        caller_id=caller_id,
        conv_store=store,
    )
    if continuation is None:
        raise HTTPException(status_code=404, detail="Run not found or cannot be continued")
    return {
        "ok": True,
        "continuation_run_id": continuation.id,
        "thread_id": thread_id,
    }


@router.get(
    "/threads/{thread_id}/stream",
)
async def stream_thread(
    thread_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    since_seq: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    follow: bool = Query(default=True, description="Keep stream open for live updates"),
    _caller: Principal = Depends(require_scope("staff.read")),
) -> StreamingResponse:
    """SSE feed for thread updates (token deltas, message completions, proposals)."""
    store = _get_store_or_503()
    thread = store.get_thread(thread_id)
    if not thread:
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


# ── READ STATUS & INBOX ──────────────────────────────────────────────────────


@router.post(
    "/threads/{thread_id}/read",
    response_model_exclude_none=True,
)
async def mark_thread_read(
    thread_id: str,
    caller: Principal = Depends(require_scope("staff.chat")),
) -> dict[str, Any]:
    """Mark a thread as read for the calling principal."""
    store = _get_store_or_503()
    caller_id = format_caller(caller)
    try:
        thread = store.get_thread(thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="thread not found")
        store.mark_thread_read(thread_id, principal=caller_id)
        return {"ok": True, "thread_id": thread_id}
    except ConversationsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/inbox",
    response_model_exclude_none=True,
)
async def get_inbox(
    caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """List threads requiring caller attention (unread messages or pending proposals)."""
    store = _get_store_or_503()
    caller_id = format_caller(caller)
    try:
        open_threads = store.list_threads(status="open", limit=200)
        inbox_items: list[dict[str, Any]] = []

        for th in open_threads:
            has_unread = th.unread_counters.get(caller_id, 0) > 0
            proposals = store.list_proposals(thread_id=th.id, state="proposed", limit=10)
            has_proposals = len(proposals) > 0

            if has_unread or has_proposals:
                item = th.to_dict()
                item["pending_proposals_count"] = len(proposals)
                item["caller_unread_count"] = th.unread_counters.get(caller_id, 0)
                inbox_items.append(item)

        return {
            "items": inbox_items,
            "inbox": inbox_items,
            "count": len(inbox_items),
        }
    except ConversationsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
