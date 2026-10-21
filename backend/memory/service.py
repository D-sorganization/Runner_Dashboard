"""Business logic for assistant memory append and context pack."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from identity import Principal, format_caller
from memory.contract import CONTEXT_DIGEST_LIMIT, CONTEXT_TASK_LIMIT, enforce_entry_size
from memory.models import MemoryAppendRequest, MemoryContextPack, MemoryEntry
from memory.redact import redact_memory_text
from memory.store import get_memory_store, new_entry_id
from priorities.directives import get_directives
from routers.staff_schedule import current_holds
from staff.store import _now  # noqa: PLC2701


def _redact_body(body: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in body.items():
        if isinstance(value, str):
            out[key] = redact_memory_text(value)
        elif isinstance(value, list):
            out[key] = [redact_memory_text(str(v)) if isinstance(v, str) else v for v in value]
        else:
            out[key] = value
    return out


def append_entry(principal: Principal, req: MemoryAppendRequest) -> MemoryEntry:
    body = _redact_body(req.body)
    enforce_entry_size(body, req.entry_type)
    store = get_memory_store()
    if req.supersedes_id:
        prior = store.get(req.supersedes_id)
        if prior is None:
            raise HTTPException(status_code=404, detail="supersedes_id not found")
    entry = MemoryEntry(
        id=new_entry_id(),
        entry_type=req.entry_type,
        author_principal=format_caller(principal),
        source_product=req.source_product.strip(),
        tags=[t.strip()[:64] for t in req.tags if t.strip()],
        body=body,
        created_at=_now(),
        expires_at=req.expires_at,
        supersedes_id=req.supersedes_id,
    )
    try:
        return store.append(entry)
    except ValueError as exc:
        if str(exc) == "store_size_cap_exceeded":
            raise HTTPException(status_code=507, detail="assistant memory store size cap exceeded") from exc
        raise


def list_entries(
    *,
    since: str | None,
    entry_type: str | None,
    tag: str | None,
    author: str | None,
    limit: int,
) -> list[MemoryEntry]:
    return get_memory_store().list_entries(
        since=since,
        entry_type=entry_type,
        tag=tag,
        author=author,
        include_superseded=False,
        limit=limit,
    )


def _open_tasks(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    closed = {"done", "closed", "cancelled", "complete", "completed"}
    open_entries = [e for e in entries if str(e.body.get("status", "open")).lower() not in closed]
    return open_entries[:CONTEXT_TASK_LIMIT]


def build_context_pack() -> MemoryContextPack:
    store = get_memory_store()
    digests = store.list_entries(entry_type="session_digest", limit=CONTEXT_DIGEST_LIMIT)
    tasks = _open_tasks(store.list_entries(entry_type="task", limit=CONTEXT_TASK_LIMIT * 2))
    directives = [d.to_dict() for d in get_directives().active()]
    holds = current_holds()
    return MemoryContextPack(
        session_digests=digests,
        open_tasks=tasks,
        directives=directives,
        holds=holds,
    )
