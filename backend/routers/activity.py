"""Bounded read and atomic ingestion API for fleet activity metadata and transcript chunks."""

from __future__ import annotations

import os
import sqlite3
import time

from activity import (
    GAP_KINDS,
    MAX_BATCH_BYTES,
    MAX_BATCH_EVENTS,
    REDACTION_VERSION,
    ActivityEnvelope,
    ActivityPage,
    ActivitySource,
    ActivityStore,
    get_activity_store,
)
from activity_sessions import DEFAULT_SESSIONS_LIMIT, MAX_SESSIONS_LIMIT, SessionsPage, SessionStatus, compute_sessions
from activity_transcript import (
    DEFAULT_MAX_CHUNK_BYTES,
    DEFAULT_NODE_MAX_BYTES,
    DEFAULT_SESSION_MAX_BYTES,
    TranscriptChunkStore,
    emit_capture_gap,
    get_transcript_chunk_store,
    record_reject,
    redact_terminal_stream,
    strip_ansi,
)
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from identity import Principal, require_scope
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from staff.rate_limit import check_rate_limit
from staff.redaction import redact_sensitive_content
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/api/v1/activity", tags=["activity"])

# Unconnected-tier (off-tailnet) check-ins: strict kinds, each tied to the
# repo and one other identifying field so a check-in cannot be a free-form event.
CHECKIN_KINDS: frozenset[str] = frozenset({"started", "claimed", "pushed", "pr_opened", "blocked", "done", "heartbeat"})
_CHECKIN_REQUIRES: dict[str, str] = {"claimed": "issue", "pushed": "branch", "pr_opened": "pr"}
_CHECKIN_MAX_EVENTS = 20
_CHECKIN_MAX_BYTES = 65_536


class TranscriptChunkWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    seq: int = Field(ge=0)
    node: str = Field(min_length=1, max_length=128)
    source: ActivitySource = "recorder"
    principal: str | None = Field(default=None, min_length=1, max_length=128)
    data: str = Field(min_length=1, validation_alias=AliasChoices("data", "raw", "text"))
    redaction_version: int = Field(default=REDACTION_VERSION, ge=1)


class TranscriptChunk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    seq: int
    node: str
    principal: str
    raw: str
    text: str
    data: str
    byte_len: int
    redaction_version: int = REDACTION_VERSION
    stored_at: float


class TranscriptChunksPage(BaseModel):
    session_id: str
    items: list[TranscriptChunk]
    gaps: list[ActivityEnvelope]
    next_seq: int | None = None


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return default if not raw else max(0, int(raw))


async def _read_bounded_body(
    request: Request,
    *,
    max_bytes: int = MAX_BATCH_BYTES,
    max_events: int = MAX_BATCH_EVENTS,
) -> list[ActivityEnvelope]:
    """Bound the wire body before parsing; never trust source-side redaction."""
    if max_bytes < 1 or max_events < 1:
        raise ValueError("max_bytes and max_events must be positive")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise HTTPException(413, "activity batch exceeds byte limit")
        body.extend(chunk)
    try:
        events = TypeAdapter(list[ActivityEnvelope]).validate_json(bytes(body))
    except ValidationError as exc:
        # Do not reflect unredacted input through validation errors.
        raise HTTPException(422, "invalid activity batch") from exc
    if len(events) > max_events:
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
    events = await _read_bounded_body(request, max_bytes=_CHECKIN_MAX_BYTES)
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


@router.get("/sessions", response_model=SessionsPage)
def read_activity_sessions(
    node: str | None = Query(default=None, max_length=128),
    status: SessionStatus | None = Query(default=None),  # noqa: B008
    repo: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=DEFAULT_SESSIONS_LIMIT, ge=1, le=MAX_SESSIONS_LIMIT),
    _caller: Principal = Depends(require_scope("activity.read")),  # noqa: B008
) -> SessionsPage:
    """Every agent/session checked into the fleet, grouped by node and status (#2860).

    Status is derived entirely from persisted activity-log events (never from an
    in-memory cache), so this is identical after a restart. See
    ``activity_sessions.compute_sessions`` for the late-event and stale/ended rules.
    """
    return compute_sessions(get_activity_store(), node=node, status=status, repo=repo, limit=limit)


def _ingest_chunk_sync(
    chunk_store: TranscriptChunkStore,
    activity_store: ActivityStore,
    *,
    session_id: str,
    chunk: TranscriptChunkWrite,
    principal: str,
) -> TranscriptChunk:
    if not session_id or len(session_id) > 128:
        raise HTTPException(422, "invalid session_id")
    if chunk.principal is not None and chunk.principal != principal:
        raise HTTPException(403, "activity principal does not match caller")
    if any(redact_sensitive_content(val) != val for val in (session_id, chunk.node, principal)):
        raise HTTPException(422, "activity identifiers must not contain sensitive content")
    with sqlite3.connect(str(activity_store.db_path)) as conn:
        foreign_meta = conn.execute(
            "SELECT 1 FROM activity_events WHERE session_id=? AND json_extract(envelope,'$.principal') != ? LIMIT 1",
            (session_id, principal),
        ).fetchone()
        has_meta = bool(
            conn.execute(
                "SELECT 1 FROM activity_events WHERE session_id=? AND seq=? "
                "AND json_extract(envelope,'$.kind') NOT IN ('OverflowGap','RetentionGap','CaptureGap') LIMIT 1",
                (session_id, chunk.seq),
            ).fetchone()
        )
    with sqlite3.connect(str(chunk_store.db_path)) as conn:
        foreign_chunk = conn.execute(
            "SELECT 1 FROM transcript_chunks WHERE session_id=? AND principal != ? LIMIT 1",
            (session_id, principal),
        ).fetchone()
    if foreign_meta or foreign_chunk:
        raise HTTPException(403, "activity session belongs to another principal")

    redacted = redact_terminal_stream(chunk.data)
    body = redacted.encode("utf-8")
    max_chunk = _env_int("RUNNER_DASHBOARD_ACTIVITY_MAX_CHUNK_BYTES", DEFAULT_MAX_CHUNK_BYTES)
    if max_chunk > 0 and (len(chunk.data.encode("utf-8")) > max_chunk or len(body) > max_chunk):
        record_reject(413)
        if has_meta:
            emit_capture_gap(
                activity_store, session_id, chunk.seq, principal, chunk.node, "missing_chunk", {"status_code": 413}
            )
        raise HTTPException(413, "transcript chunk exceeds max byte size")

    if chunk_store.get_chunk_body(session_id, chunk.seq) is None:
        s_cap = _env_int("RUNNER_DASHBOARD_ACTIVITY_SESSION_MAX_BYTES", DEFAULT_SESSION_MAX_BYTES)
        n_cap = _env_int("RUNNER_DASHBOARD_ACTIVITY_NODE_MAX_BYTES", DEFAULT_NODE_MAX_BYTES)
        if (s_cap > 0 and chunk_store.spool_bytes(session_id=session_id) + len(body) > s_cap) or (
            n_cap > 0 and chunk_store.spool_bytes(node=chunk.node) + len(body) > n_cap
        ):
            record_reject(429)
            if has_meta:
                emit_capture_gap(
                    activity_store, session_id, chunk.seq, principal, chunk.node, "missing_chunk", {"status_code": 429}
                )
            raise HTTPException(429, "transcript byte cap exceeded", headers={"Retry-After": "60"})

    try:
        ref = chunk_store.put_chunk(
            session_id=session_id, seq=chunk.seq, node=chunk.node, principal=principal, body=body, stored_at=time.time()
        )
    except ValueError as exc:
        raise HTTPException(409, "conflicting transcript chunk payload") from exc

    if not has_meta:
        emit_capture_gap(
            activity_store, session_id, chunk.seq, principal, chunk.node, "missing_metadata", {"bytes": ref.byte_len}
        )
    return TranscriptChunk(
        session_id=session_id,
        seq=ref.seq,
        node=ref.node,
        principal=ref.principal,
        raw=redacted,
        text=strip_ansi(redacted),
        data=redacted,
        byte_len=ref.byte_len,
        redaction_version=REDACTION_VERSION,
        stored_at=ref.stored_at,
    )


@router.post("/sessions/{session_id}/chunks", response_model=TranscriptChunk, include_in_schema=False)
async def ingest_session_chunk(
    session_id: str,
    chunk: TranscriptChunkWrite,
    caller: Principal = Depends(require_scope("activity.write")),  # noqa: B008
) -> TranscriptChunk:
    return await run_in_threadpool(
        lambda: _ingest_chunk_sync(
            get_transcript_chunk_store(),
            get_activity_store(),
            session_id=session_id,
            chunk=chunk,
            principal=caller.id,
        )
    )


@router.get("/sessions/{session_id}/chunks", response_model=TranscriptChunksPage, include_in_schema=False)
def read_session_chunks(
    session_id: str,
    after_seq: int = Query(default=-1, ge=-1),
    limit: int = Query(default=100, ge=1, le=500),
    _caller: Principal = Depends(require_scope("activity.read")),  # noqa: B008
) -> TranscriptChunksPage:
    chunk_store = get_transcript_chunk_store()
    activity_store = get_activity_store()
    stored = {ref.seq: ref for ref in chunk_store.list_chunks(session_id=session_id)}
    for ev in activity_store.read(session_id=session_id, limit=500).items:
        if ev.kind not in GAP_KINDS and ev.seq not in stored:
            emit_capture_gap(activity_store, session_id, ev.seq, ev.principal, ev.node, "missing_chunk")
    ordered = sorted((ref for ref in stored.values() if ref.seq > after_seq), key=lambda r: r.seq)
    items = [
        TranscriptChunk(
            session_id=session_id,
            seq=ref.seq,
            node=ref.node,
            principal=ref.principal,
            raw=(raw := (chunk_store.get_chunk_body(session_id, ref.seq) or b"").decode("utf-8", errors="replace")),
            text=strip_ansi(raw),
            data=raw,
            byte_len=ref.byte_len,
            redaction_version=REDACTION_VERSION,
            stored_at=ref.stored_at,
        )
        for ref in ordered[:limit]
    ]
    gaps = [
        ev
        for ev in activity_store.read(session_id=session_id, limit=500).items
        if ev.kind in GAP_KINDS and int(ev.payload.get("chunk_seq", ev.seq)) > after_seq
    ]
    return TranscriptChunksPage(
        session_id=session_id,
        items=items,
        gaps=gaps,
        next_seq=items[-1].seq if len(ordered) > limit and items else None,
    )
