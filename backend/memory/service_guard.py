"""Write/read gates for shared assistant memory (#3936)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from identity import Principal, format_caller
from memory.barb_shared_guard import MemoryWriteProvenance, entry_allowed_in_shared_memory, refuse_shared_memory_write
from memory.models import MemoryAppendRequest, MemoryEntry
from memory.store import get_memory_store


def enforce_memory_append(principal: Principal, req: MemoryAppendRequest, body: dict[str, Any]) -> None:
    prov = MemoryWriteProvenance(
        source_product=req.source_product,
        tags=tuple(req.tags),
        body=body,
        author_principal=format_caller(principal),
    )
    if reason := refuse_shared_memory_write(prov, entry_type=req.entry_type):
        raise HTTPException(status_code=422, detail={"code": "shared_memory_refused", "reason": reason})


def enforce_session_digest_append(
    caller: str,
    *,
    source_product: str,
    body: dict[str, Any],
    reported_origin_principal: str | None,
) -> None:
    prov = MemoryWriteProvenance(
        source_product=source_product,
        tags=(),
        body=body,
        author_principal=caller,
        reported_origin_principal=reported_origin_principal,
    )
    summary = str(body.get("summary") or "")
    if reason := refuse_shared_memory_write(prov, entry_type="session_digest", digest_summary=summary):
        raise HTTPException(status_code=422, detail={"code": "shared_memory_refused", "reason": reason})


def list_guarded_entries(**kwargs: Any) -> list[MemoryEntry]:
    return [
        entry
        for entry in get_memory_store().list_entries(include_superseded=False, **kwargs)
        if entry_allowed_in_shared_memory(entry)
    ]
