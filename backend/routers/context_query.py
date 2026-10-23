"""Unified fleet context query API (/api/v1/fleet/query) (#2223)."""

# ruff: noqa: B008
from __future__ import annotations

import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from identity import Principal, principal_has_scope, require_principal
from library.db import library_db_path
from library.search import SearchOptions, search_passages
from memory.service import list_entries
from priorities.directives import get_directives
from routers.staff_schedule import current_holds
from staff.standards_registry import load_fleet_standards

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet-query"])

_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
CACHE_TTL_SECONDS = 30.0
SNIPPET_CEILING = 400
PAGE_LIMIT_CEILING = 5


def clear_context_query_cache() -> None:
    _CACHE.clear()


def _truncate(text: str) -> str:
    t = text.strip()
    return t if len(t) <= SNIPPET_CEILING else f"{t[: SNIPPET_CEILING - 3]}..."


def _query_memory_items(repo: str | None, query: str, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    try:
        for e in list_entries(since=None, entry_type=None, tag=repo, author=None, limit=limit):
            if str(e.body.get("visibility", "")).lower() == "barb-only":
                continue
            txt = str(e.body.get("summary") or e.body.get("text") or "")
            if not query or query.lower() in txt.lower():
                items.append(
                    {
                        "id": e.id,
                        "title": e.body.get("title", f"Memory {e.id}"),
                        "snippet": _truncate(txt),
                        "ref": f"/api/v1/memory/{e.id}",
                    }
                )
    except Exception:
        pass
    return items


@router.get("/query")
async def fleet_query_endpoint(
    source: Literal["context", "library", "knowledge", "memory"] = "context",
    query: str = Query(default=""),
    op: Literal["query", "request"] = "query",
    repo: str | None = Query(default=None),
    limit: int = Query(default=5, ge=1),
    page: int = Query(default=1, ge=1),
    full: bool = Query(default=False),
    caller: Principal = Depends(require_principal),
) -> dict[str, Any]:
    limit = min(limit, PAGE_LIMIT_CEILING)
    req_scopes = {"library": "fleet.read", "knowledge": "staff.read", "memory": "memory.read"}
    if source in req_scopes and not principal_has_scope(caller, req_scopes[source]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"{req_scopes[source]} scope required for {source}")

    cache_key = (caller.id, tuple(sorted(caller.scopes)), repo or "", query, page, full)
    now = time.time()
    if source == "context" and cache_key in _CACHE:
        cached_time, cached_val = _CACHE[cache_key]
        if now - cached_time < CACHE_TTL_SECONDS:
            res = dict(cached_val)
            res["cached"] = True
            return res

    items: list[dict[str, Any]] = []
    context_meta: dict[str, Any] = {}

    if source == "context":
        h: list[dict[str, Any]] | str
        d: list[dict[str, Any]] | str
        try:
            h = current_holds()
        except Exception:
            h = "unavailable"
        try:
            d = [item.to_dict() for item in get_directives().active()]
        except Exception:
            d = "unavailable"
        context_meta = {
            "pack_pin": load_fleet_standards().pin,
            "summary": "Fleet active context",
            "holds": h,
            "directives": d,
        }
        items = _query_memory_items(repo, query, 20)

    elif source == "library":
        try:
            hits = search_passages(
                library_db_path(),
                query or "fleet",
                k=limit * page,
                budget_tokens=4000,
                options=SearchOptions(repo=repo),
            )
            for hit in hits:
                items.append(
                    {
                        "id": str(hit.passage_id),
                        "title": hit.title or f"Passage {hit.passage_id}",
                        "snippet": _truncate(hit.text),
                        "citation": hit.citation,
                        "ref": f"/api/v1/library/works/{hit.work_id}",
                    }
                )
        except Exception:
            pass

    elif source == "knowledge":
        from staff.knowledge_pack import KnowledgePack
        from staff.knowledge_refresh import get_knowledge_dir

        pack_path = get_knowledge_dir() / f"{repo or 'fleet'}.sqlite"
        if pack_path.exists():
            try:
                for kh in KnowledgePack(pack_path).search(query or "fleet", k=limit * page):
                    items.append(
                        {
                            "id": kh.anchor,
                            "title": kh.title or kh.anchor,
                            "snippet": _truncate(kh.text),
                            "citation": kh.citation,
                            "ref": f"/api/v1/staff/knowledge/{repo or 'fleet'}",
                        }
                    )
            except Exception:
                pass

    elif source == "memory":
        items = _query_memory_items(repo, query, 50)

    total = len(items)
    start_idx = (page - 1) * limit
    out = {
        "source": source,
        "op": op,
        "query": query,
        "repo": repo,
        "page": page,
        "total_hits": total,
        "has_more": start_idx + limit < total,
        "items": items[start_idx : start_idx + limit],
        "context": context_meta,
        "cached": False,
    }
    if source == "context":
        _CACHE[cache_key] = (now, dict(out))
    return out
