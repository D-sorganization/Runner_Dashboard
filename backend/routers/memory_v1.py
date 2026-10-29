"""Assistant shared memory API (/api/v1/memory)."""

# ruff: noqa: B008
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request, Response
from identity import Principal, format_caller, require_principal, require_scope
from memory.models import (
    MemoryAppendRequest,
    MemoryAppendResponse,
    MemoryListResponse,
    MemoryReviewRequest,
    SessionDigestAppendRequest,
    SessionDigestAppendResponse,
)
from memory.service import (
    append_entry,
    append_session_digest,
    build_context_pack,
    is_barb_principal,
    list_entries,
    review_entry,
)
from staff.audit import record_audit
from staff.barb_lease_fence import barb_write_fence, claim_for_write

log = logging.getLogger("dashboard.memory")
router = APIRouter(prefix="/api/v1/memory", tags=["assistant-memory"])


@router.get("", response_model=MemoryListResponse)
async def get_memory_entries(
    since: str | None = Query(None),
    entry_type: str | None = Query(None, alias="type"),
    repo: str | None = Query(None, include_in_schema=False),
    review_status: str | None = Query(None, include_in_schema=False),
    tag: str | None = Query(None),
    author: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    _reader: Principal = Depends(require_scope("memory.read")),
) -> MemoryListResponse:
    kw = {"since": since, "entry_type": entry_type, "repo": repo, "review_status": review_status}
    return MemoryListResponse(
        entries=list_entries(tag=tag, author=author, include_barb_only=is_barb_principal(_reader), limit=limit, **kw)
    )


@router.get("/context-pack")
async def get_context_pack(
    repo: str | None = Query(None, include_in_schema=False),
    scope: str | None = Query(None, include_in_schema=False),
    data_class: str | None = Query(None, include_in_schema=False),
    reader: Principal = Depends(require_scope("memory.read")),
) -> dict:
    return build_context_pack(reader, repo=repo, scope=scope, data_class=data_class).model_dump()


@router.post("", response_model=MemoryAppendResponse, status_code=201)
async def post_memory_entry(
    body: MemoryAppendRequest,
    request: Request,
    writer: Principal = Depends(require_principal),
) -> MemoryAppendResponse:
    # Authorization is entry-type-specific and enforced inside append_entry: a
    # lesson proposal is authorized by active-run binding (authenticated_lesson_run),
    # everything else still requires the "memory.write" scope (#2665).
    claim = claim_for_write(request.headers, writer, surface="memory")
    with barb_write_fence(claim, surface="memory", target="memory:append", principal=format_caller(writer)):
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


@router.post(
    "/session-digest",
    response_model=SessionDigestAppendResponse,
    status_code=201,
    responses={200: {"model": SessionDigestAppendResponse, "description": "Idempotent replay"}},
)
async def post_session_digest(
    body: SessionDigestAppendRequest,
    response: Response,
    request: Request,
    writer: Principal = Depends(require_scope("memory.shared.summarize")),
) -> SessionDigestAppendResponse:
    claim = claim_for_write(request.headers, writer, surface="memory")
    with barb_write_fence(claim, surface="memory", target="memory:session-digest", principal=format_caller(writer)):
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


@router.patch("/{entry_id}/review", response_model=MemoryAppendResponse, include_in_schema=False)
async def patch_memory_review(
    entry_id: str, body: MemoryReviewRequest, reviewer: Principal = Depends(require_scope("memory.write"))
) -> MemoryAppendResponse:
    e = review_entry(reviewer, entry_id, body)
    caller = format_caller(reviewer)
    d = {"review_status": e.review_status}
    record_audit(action="memory_review", target=e.id, principal=caller, detail=d, fail_closed=False)
    log.info("memory: reviewed %s status=%s by %s", e.id, e.review_status, caller)
    return MemoryAppendResponse(entry=e)
