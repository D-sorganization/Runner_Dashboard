"""Alexandria library search API (#2165 slice 1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from identity import Principal, require_scope
from library.db import init_library_db, library_db_path
from library.search import DEFAULT_BUDGET, MAX_BUDGET, SearchHit, library_stats, search_passages

router = APIRouter(prefix="/api/v1/library", tags=["library"])
_db_override: Path | None = None


def set_db_path(path: Path | None) -> None:
    global _db_override
    _db_override = path


def _db() -> Path:
    path = _db_override or library_db_path()
    init_library_db(path)
    return path


@router.get("/stats")
def library_stats_route(
    _principal: Principal = Depends(require_scope("fleet.read")),  # noqa: B008
) -> dict[str, Any]:
    return library_stats(_db())


@router.get("/search")
def library_search(
    q: str = Query(..., min_length=1),
    k: int = Query(32, ge=1, le=100),
    budget: int | None = Query(None, alias="budget"),
    budget_tokens: int | None = Query(None),
    _principal: Principal = Depends(require_scope("fleet.read")),  # noqa: B008
) -> dict[str, Any]:
    tokens_budget = budget if budget is not None else budget_tokens or DEFAULT_BUDGET
    if tokens_budget < 1 or tokens_budget > MAX_BUDGET:
        raise HTTPException(status_code=422, detail=f"budget must be between 1 and {MAX_BUDGET}")
    try:
        hits = search_passages(_db(), q, k=k, budget_tokens=tokens_budget)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "query": q,
        "budget_tokens": tokens_budget,
        "tokens_used": sum(h.token_estimate for h in hits),
        "passages": [_hit(h) for h in hits],
    }


def _hit(hit: SearchHit) -> dict[str, Any]:
    return {
        "work_id": hit.work_id,
        "author": hit.author,
        "title": hit.title,
        "year": hit.year,
        "page_start": hit.page_start,
        "page_end": hit.page_end,
        "text": hit.text,
        "score": hit.score,
        "citation": hit.citation,
        "file_path": hit.file_path,
        "token_estimate": hit.token_estimate,
    }
