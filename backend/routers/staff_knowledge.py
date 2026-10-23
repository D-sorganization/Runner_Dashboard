"""Staff knowledge pack endpoints (Issue #1479).

Split out of `routers.staff_v1` and mounted there via `router.include_router`
to keep that module under the repo's 500-line soft cap.
"""

# ruff: noqa: B008
from __future__ import annotations

import asyncio
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from identity import Principal, require_scope
from knowledge_pack import KnowledgePack
from knowledge_pack.cartographer import format_codebase_reference
from staff.codebase_freshness import snapshot_is_stale
from staff.knowledge_refresh import get_knowledge_dir, pack_is_stale
from staff.models_insights import (
    StaffKnowledgeInfoResponse,
    StaffKnowledgeSearchResponse,
)

router = APIRouter(prefix="/knowledge")


def _codebase_state(pack: KnowledgePack) -> tuple[dict[str, Path], bool]:
    """Compare indexed commits with local heads and cached remote defaults.

    Cartographer serves a committed snapshot. Dirty working files do not replace
    its evidence; each emitted passage is separately checked against its Git blob.
    Content scanning belongs to pack refresh, never an indexed read request.
    """
    from staff.workspace import find_repo_checkout

    commits = pack.info().commits
    roots = {name: root for name in commits if (root := find_repo_checkout(name)) is not None}
    stale = (
        not roots
        or len(roots) != len(commits)
        or any(snapshot_is_stale(root, commits[name]) for name, root in roots.items())
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


_CODEBASE_CITATION = re.compile(
    r"^\[ref:([A-Za-z0-9][A-Za-z0-9.-]{0,63}):codebase:([A-Za-z0-9][A-Za-z0-9_.-]{0,127})@([0-9a-f]{8}):([^#]+)#L([1-9][0-9]*)-L([1-9][0-9]*)\]$"
)


def _ledger_references(passages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for passage in passages:
        citation = passage.get("citation")
        match = _CODEBASE_CITATION.fullmatch(citation) if isinstance(citation, str) else None
        commit = passage.get("commit")
        content_hash = passage.get("content_hash")
        if match is None or not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("emitted Cartographer citation is not canonical")
        node, repo, short_commit, relative_path, start, end = match.groups()
        if repo != passage.get("repo") or not commit.startswith(short_commit):
            raise ValueError("emitted Cartographer citation does not match its source identity")
        references.append(
            {
                "node": node,
                "repo": repo,
                "commit_sha": commit,
                "relative_path": relative_path,
                "line_start": int(start),
                "line_end": int(end),
                "content_hash": content_hash,
                "citation": citation,
            }
        )
    return references


def _record_codebase_references(run_id: str, passages: list[dict[str, Any]]) -> None:
    from library.codebase_retrieval_ledger import record_codebase_references
    from library.db import library_db_path

    record_codebase_references(library_db_path(), run_id=run_id, references=_ledger_references(passages))


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
    run_id: str | None = Query(default=None, min_length=1, max_length=120),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Search knowledge packs; optional run_id records emitted Cartographer citations for that run owner."""
    if run_id is not None and pack_id != "cartographer":
        raise HTTPException(status_code=422, detail="run_id is supported only for Cartographer search")
    if run_id is not None:
        try:
            from library.retrieval_ledger import authorize_run_owner
            from staff.store import get_run_store

            authorize_run_owner(get_run_store(), run_id=run_id, principal_id=_peer.id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    pack = _open_pack(pack_id)
    if pack_id == "cartographer":
        result = await asyncio.to_thread(_search_codebase, pack, q, k, include_superseded)
        if run_id is not None:
            try:
                await asyncio.to_thread(_record_codebase_references, run_id, result["passages"])
            except ValueError as exc:
                raise HTTPException(status_code=500, detail="Cartographer references could not be recorded") from exc
            result["run_id"] = run_id
        return result
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
