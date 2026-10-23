"""Alexandria library API (#2165)."""

from __future__ import annotations

import hmac
import os
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from identity import (
    Principal,
    _is_loopback_request,
    _loopback_auth_enabled,
    _loopback_principal,
    _principal_may_read_fleet,
    _resolve_principal_optional,
    auth_cookie,
    auth_header,
    require_principal,
    require_scope,
)
from library.browse import browse_directory
from library.db import init_library_db, library_db_path
from library.pool import _hit_to_dict, federated_search, get_pooled_catalog
from library.search import (
    DEFAULT_BUDGET,
    MAX_BUDGET,
    SearchOptions,
    get_work,
    library_stats,
    search_passages,
)
from library.sources import add_source, list_sources, remove_source, seed_default_source, update_source
from library.submissions import create_submission, list_submissions

router = APIRouter(prefix="/api/v1/library", tags=["library"])
_db_override: Path | None = None


def set_db_path(path: Path | None) -> None:
    global _db_override
    _db_override = path


def _db() -> Path:
    path = _db_override or library_db_path()
    init_library_db(path)
    seed_default_source(path)
    return path


def require_library_peer(
    request: Request,
    header_token: str | None = Depends(auth_header),
    cookie_token: str | None = Depends(auth_cookie),
) -> Principal:
    """Validate intra-fleet or operator credentials with fail-closed semantics (#2229)."""
    overrides = getattr(request.app, "dependency_overrides", {})
    for target in (require_principal, require_scope("fleet.read"), require_library_peer):
        if target in overrides:
            fn = overrides[target]
            res = fn() if callable(fn) else fn
            if isinstance(res, Principal):
                if _principal_may_read_fleet(res):
                    return res
                raise HTTPException(status_code=403, detail={"error": "Fleet authorization failed"})
            return res
    prin = _resolve_principal_optional(request, header_token)
    if prin is not None:
        if _principal_may_read_fleet(prin):
            return prin
        raise HTTPException(
            status_code=403,
            detail={"error": "Fleet authorization failed", "required_scope": "fleet.read", "principal": prin.id},
        )
    hub_token = os.environ.get("HUB_FLEET_TOKEN", "")
    if hub_token and header_token and header_token.startswith("Bearer "):
        if hmac.compare_digest(header_token[7:], hub_token):
            return Principal(id="fleet-peer", type="bot", name="Fleet Peer", roles=["fleet-peer"])
    if _loopback_auth_enabled() and _is_loopback_request(request):
        return _loopback_principal()
    if not hub_token:
        raise HTTPException(status_code=503, detail="Fleet token not configured")
    raise HTTPException(status_code=401, detail="Fleet authentication required")


@router.get("/browse")
def library_browse_route(
    path: str | None = None,
    _principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
) -> dict[str, Any]:
    try:
        return browse_directory(path)
    except FileNotFoundError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except (PermissionError, ValueError) as exc:
        raise HTTPException(403 if isinstance(exc, PermissionError) else 422, detail=str(exc)) from exc


@router.get("/sources")
def library_sources_route(
    request: Request,
    _principal: Principal = Depends(require_library_peer),  # noqa: B008
) -> dict[str, Any]:
    from dashboard_config import FLEET_NODES, HOSTNAME, MACHINE_ROLE

    shared = request.query_params.get("shared") in ("1", "true", "True")
    sources = list_sources(_db())
    if shared or _principal.id == "fleet-peer":
        sources = [s for s in sources if s.get("shared")]
    return {
        "sources": sources,
        "pooled_sources": get_pooled_catalog().list_sources(),
        "machine_role": MACHINE_ROLE,
        "is_hub": MACHINE_ROLE == "hub",
        "current_node": HOSTNAME,
        "nodes": [{"name": k, "url": v} for k, v in sorted(FLEET_NODES.items())],
    }


@router.post("/sources")
def library_add_source(
    body: dict[str, Any],
    principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
) -> dict[str, Any]:
    try:
        return add_source(
            _db(),
            source_id=body.get("id", ""),
            label=body.get("label", ""),
            root_path=body.get("root_path", ""),
            shared=body.get("shared", False),
            include_globs=body.get("include_globs"),
            exclude_globs=body.get("exclude_globs"),
            created_by=principal.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="source id or root_path already registered") from exc


@router.patch("/sources/{source_id}")
def library_update_source(
    source_id: str,
    body: dict[str, Any],
    _principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
) -> dict[str, Any]:
    try:
        return update_source(_db(), source_id, **body)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/sources/{source_id}")
def library_remove_source(
    source_id: str,
    _principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
) -> dict[str, bool]:
    try:
        remove_source(_db(), source_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"removed": True}


@router.post("/sources/{source_id}/reindex", include_in_schema=False)
def library_reindex_source(
    source_id: str,
    _principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
) -> dict[str, Any]:
    from library.ingest import ingest_directory
    from library.sources import get_source

    try:
        source = get_source(_db(), source_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    root = Path(source["root_path"])
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"source root not found on disk: {root}")
    stats = ingest_directory(root, db_path=_db(), source_id=source_id)
    return {"status": "ok", "source_id": source_id, "stats": stats}


@router.post("/submissions")
def library_create_submission(
    body: dict[str, Any],
    principal: Principal = Depends(require_scope("fleet.write")),  # noqa: B008
) -> dict[str, Any]:
    try:
        return create_submission(_db(), principal_id=principal.id, payload=body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/submissions")
def library_list_submissions(
    status: str | None = Query(None),
    _principal: Principal = Depends(require_scope("fleet.read")),  # noqa: B008
) -> dict[str, Any]:
    try:
        return {"submissions": list_submissions(_db(), status=status)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/stats")
def library_stats_route(
    _principal: Principal = Depends(require_scope("fleet.read")),  # noqa: B008
) -> dict[str, Any]:
    return library_stats(_db())


@router.get("/works/{work_id}")
def library_work(
    work_id: int,
    _principal: Principal = Depends(require_scope("fleet.read")),  # noqa: B008
) -> dict[str, Any]:
    try:
        return get_work(_db(), work_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/search")
async def library_search(
    request: Request,
    q: str = Query(..., min_length=1),
    k: int = Query(32, ge=1, le=100),
    budget: int | None = Query(None, alias="budget"),
    budget_tokens: int | None = Query(None),
    topic: str | None = Query(None),
    repo: str | None = Query(None),
    _principal: Principal = Depends(require_library_peer),  # noqa: B008
) -> dict[str, Any]:
    from dashboard_config import HOSTNAME

    scope = request.query_params.get("scope", "federated")
    shared = request.query_params.get("shared") in ("1", "true", "True")
    tokens_budget = budget if budget is not None else budget_tokens or DEFAULT_BUDGET
    if tokens_budget < 1 or tokens_budget > MAX_BUDGET:
        raise HTTPException(status_code=422, detail=f"budget must be between 1 and {MAX_BUDGET}")

    if scope == "local":
        shared_only = shared or _principal.id == "fleet-peer"
        try:
            hits = search_passages(
                _db(),
                q,
                k=k,
                budget_tokens=tokens_budget,
                options=SearchOptions(topic=topic, repo=repo, shared_only=shared_only),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        passages = [_hit_to_dict(h, HOSTNAME or "local") for h in hits]
        return {
            "query": q,
            "topic": topic,
            "repo": repo,
            "budget_tokens": tokens_budget,
            "tokens_used": sum(h.token_estimate for h in hits),
            "passages": passages,
            "unavailable_sources": [],
        }

    try:
        res = await federated_search(q, k=k, budget_tokens=tokens_budget, topic=topic, repo=repo, db_path=_db())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "query": res.query,
        "topic": res.topic,
        "repo": res.repo,
        "budget_tokens": res.budget_tokens,
        "tokens_used": res.tokens_used,
        "passages": res.passages,
        "unavailable_sources": res.unavailable_sources,
    }
