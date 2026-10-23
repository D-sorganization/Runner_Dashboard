"""Assistant shared memory API (/api/v1/memory)."""

# ruff: noqa: B008
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Response
from identity import Principal, format_caller, require_scope
from memory.models import (
    MemoryAppendRequest,
    MemoryAppendResponse,
    MemoryListResponse,
    SessionDigestAppendRequest,
    SessionDigestAppendResponse,
)
from memory.service import append_entry, append_session_digest, build_context_pack, list_entries
from staff.audit import record_audit

log = logging.getLogger("dashboard.memory")
router = APIRouter(prefix="/api/v1/memory", tags=["assistant-memory"])


@router.get("", response_model=MemoryListResponse)
async def get_memory_entries(
    since: str | None = Query(None),
    entry_type: str | None = Query(None, alias="type"),
    tag: str | None = Query(None),
    author: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    _reader: Principal = Depends(require_scope("memory.read")),
) -> MemoryListResponse:
    return MemoryListResponse(
        entries=list_entries(since=since, entry_type=entry_type, tag=tag, author=author, limit=limit)
    )


@router.get("/context-pack")
async def get_context_pack(
    _reader: Principal = Depends(require_scope("memory.read")),
) -> dict:
    return build_context_pack().model_dump()


@router.post("", response_model=MemoryAppendResponse, status_code=201)
async def post_memory_entry(
    body: MemoryAppendRequest,
    writer: Principal = Depends(require_scope("memory.write")),
) -> MemoryAppendResponse:
    entry = append_entry(writer, body)
    record_audit(
        action="memory_append",
        target=entry.id,
        principal=format_caller(writer),
        surface="api",
        detail={"entry_type": entry.entry_type, "source_product": entry.source_product},
        fail_closed=False,
    )
    log.info("memory: appended %s by %s", entry.id, format_caller(writer))
    return MemoryAppendResponse(entry=entry)


@router.post("/session-digest", response_model=SessionDigestAppendResponse)
async def post_session_digest(
    body: SessionDigestAppendRequest,
    response: Response,
    writer: Principal = Depends(require_scope("memory.shared.summarize")),
) -> SessionDigestAppendResponse:
    entry, replayed = append_session_digest(writer, body)
    if replayed:
        response.status_code = 200
    else:
        response.status_code = 201
    record_audit(
        action="memory_session_digest_append",
        target=entry.id,
        principal=format_caller(writer),
        surface="api",
        detail={"source_product": entry.source_product, "replayed": replayed},
        fail_closed=False,
    )
    log.info("memory: session digest %s by %s (replayed=%s)", entry.id, format_caller(writer), replayed)
    return SessionDigestAppendResponse(entry=entry, replayed=replayed)
