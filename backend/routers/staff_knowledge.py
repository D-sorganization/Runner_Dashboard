"""Staff knowledge pack endpoints (Issue #1479).

Split out of `routers.staff_v1` and mounted there via `router.include_router`
to keep that module under the repo's 500-line soft cap.
"""

# ruff: noqa: B008
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from identity import Principal, require_scope
from knowledge_pack import KnowledgePack
from staff.knowledge_refresh import get_knowledge_dir, pack_is_stale
from staff.models import (
    StaffKnowledgeInfoResponse,
    StaffKnowledgeSearchResponse,
)

router = APIRouter(prefix="/knowledge")


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
    return {
        "pack_id": info.pack_id,
        "title": info.title,
        "built_at": info.built_at,
        "commits": dict(info.commits),
        "stale": pack_is_stale(pack),
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
    """Search knowledge pack by BM25 query (Issue #1479)."""
    pack = _open_pack(pack_id)
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
