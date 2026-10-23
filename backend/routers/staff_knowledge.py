"""Staff knowledge pack endpoints (Issue #1479).

Split out of `routers.staff_v1` and mounted there via `router.include_router`
to keep that module under the repo's 500-line soft cap.
"""

# ruff: noqa: B008
from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from identity import Principal, require_scope
from knowledge_pack import KnowledgePack
from knowledge_pack.cartographer import format_codebase_reference
from knowledge_pack.sources import head_commit
from staff.knowledge_refresh import get_knowledge_dir, pack_is_stale
from staff.models_insights import (
    StaffKnowledgeInfoResponse,
    StaffKnowledgeSearchResponse,
)

router = APIRouter(prefix="/knowledge")


def _codebase_state(pack: KnowledgePack) -> tuple[dict[str, Path], bool]:
    """Resolve configured checkouts and compare their current heads with the indexed snapshot."""
    from staff.workspace import find_repo_checkout

    commits = pack.info().commits
    roots = {name: root for name in commits if (root := find_repo_checkout(name)) is not None}
    stale = (
        not roots
        or len(roots) != len(commits)
        or pack.is_stale(roots)
        or any(head_commit(root) != commits[name] for name, root in roots.items())
    )
    return roots, stale


def _search_codebase(pack: KnowledgePack, query: str, k: int, include_superseded: bool) -> dict[str, Any]:
    roots, stale = _codebase_state(pack)
    passages = []
    for hit in pack.search(query, k=k, include_superseded=include_superseded):
        root = roots.get(hit.repo)
        citation = format_codebase_reference(hit, root) if root is not None else None
        if citation is not None:
            passages.append(
                {
                    **asdict(hit),
                    "citation": citation,
                    "text": hit.text.encode("utf-8")[:400].decode("utf-8", errors="ignore"),
                }
            )
    return {"pack_id": "cartographer", "query": query, "count": len(passages), "passages": passages, "stale": stale}


def _open_pack(pack_id: str) -> KnowledgePack:
    """Open a knowledge pack by id, or raise the appropriate HTTPException.

    Precondition: pack_id is a non-empty string.
    Postcondition: returns an opened KnowledgePack, or raises HTTPException
    (404 missing, 500 unreadable).
    """
    pack_path = get_knowledge_dir() / f"{pack_id}.sqlite"
    if not pack_path.is_file():
        raise HTTPException(status_code=404, detail=f"Knowledge pack '{pack_id}' not found")

    try:
        return KnowledgePack.open(pack_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to open knowledge pack '{pack_id}': {exc}") from exc


@router.get(
    "/{pack_id}",
    response_model=StaffKnowledgeInfoResponse,
    response_model_exclude_none=True,
)
async def get_knowledge_info_v1(
    pack_id: str,
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Retrieve metadata and freshness status of a knowledge pack (Issue #1479)."""
    pack = _open_pack(pack_id)
    info = pack.info()
    stale = (await asyncio.to_thread(_codebase_state, pack))[1] if pack_id == "cartographer" else pack_is_stale(pack)
    return {
        "pack_id": info.pack_id,
        "title": info.title,
        "built_at": info.built_at,
        "commits": dict(info.commits),
        "stale": stale,
        "files": info.files,
        "passages": info.passages,
        "passage_count": info.passages,
    }


@router.get(
    "/{pack_id}/search",
    response_model=StaffKnowledgeSearchResponse,
    response_model_exclude_none=True,
)
async def search_knowledge_v1(
    pack_id: str,
    q: str = Query(..., min_length=1, description="Search query string"),
    k: int = Query(default=8, ge=1, le=100, description="Max passages to return"),
    include_superseded: bool = Query(default=False, description="Include superseded passages"),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Search knowledge packs. Cartographer returns committed-line citations and <=400 UTF-8 byte snippets."""
    pack = _open_pack(pack_id)
    if pack_id == "cartographer":
        return await asyncio.to_thread(_search_codebase, pack, q, k, include_superseded)
    hits = pack.search(q, k=k, include_superseded=include_superseded)
    return {
        "pack_id": pack_id,
        "query": q,
        "count": len(hits),
        "passages": [
            {
                "repo": h.repo,
                "source": h.source,
                "anchor": h.anchor,
                "title": h.title,
                "text": h.text,
                "commit": h.commit,
                "content_hash": h.content_hash,
                "status": h.status,
                "authority": h.authority,
                "score": h.score,
                "citation": h.citation,
            }
            for h in hits
        ],
    }
