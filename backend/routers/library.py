"""Alexandria library API (#2165)."""

from __future__ import annotations

import asyncio
import hmac
import os
import re
import sqlite3
from dataclasses import replace
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
from library.pool import _hit_to_dict, federated_search, get_pooled_catalog, refresh_catalog
from library.search import (
    DEFAULT_BUDGET,
    MAX_BUDGET,
    AmbiguousPassageError,
    SearchOptions,
    fetch_passage,
    get_work,
    library_stats,
    search_passages,
)
from library.sources import add_source, list_sources, remove_source, seed_default_source, update_source
from library.submissions import create_submission, list_submissions
from library.verify import verify_content

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
async def library_sources_route(
    request: Request,
    _principal: Principal = Depends(require_library_peer),  # noqa: B008
) -> dict[str, Any]:
    from dashboard_config import FLEET_NODES, HOSTNAME, MACHINE_ROLE

    shared = request.query_params.get("shared") in ("1", "true", "True")
    sources = list_sources(_db())
    peer_request = shared or _principal.id == "fleet-peer"
    catalog = get_pooled_catalog()
    unavailable: list[str] = []
    if peer_request:
        sources = [s for s in sources if s.get("shared")]
    else:
        catalog.update_node_sources(HOSTNAME, [s for s in sources if s.get("shared")])
        unavailable = await refresh_catalog(
            catalog=catalog,
            fleet_nodes=FLEET_NODES,
            local_node=HOSTNAME,
            hub_token=os.environ.get("HUB_FLEET_TOKEN", ""),
        )
    return {
        "sources": sources,
        "pooled_sources": [] if peer_request else catalog.list_sources(),
        "unavailable_sources": unavailable,
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


_CITE_KEY = re.compile(
    r"^\[ref:(?P<node>[^:\]]+):(?P<source>[a-z0-9][a-z0-9-]{0,62}):w(?P<work>[1-9][0-9]*):"
    r"p(?P<passage>[1-9][0-9]*)#sha256=(?P<sha>[0-9a-fA-F]{8}):p\.(?P<start>[1-9][0-9]*)-(?P<end>[1-9][0-9]*)\]$"
)
_CITE_URI = re.compile(
    r"^lib://(?P<source>[a-z0-9][a-z0-9-]{0,62})/w(?P<work>[1-9][0-9]*)/"
    r"p(?P<passage>[1-9][0-9]*)@(?P<sha>[0-9a-fA-F]{8})#p(?P<start>[1-9][0-9]*)-(?P<end>[1-9][0-9]*)$"
)


def _parse_cite(cite: str) -> dict[str, Any]:
    match = _CITE_KEY.fullmatch(cite) or _CITE_URI.fullmatch(cite)
    if match is None:
        raise HTTPException(status_code=422, detail="cite must be a canonical library reference")
    values = match.groupdict()
    return {
        "node": values.get("node"),
        "source_id": values["source"],
        "work_id": int(values["work"]),
        "passage_id": int(values["passage"]),
        "sha_prefix": values["sha"].lower(),
        "page_start": int(values["start"]),
        "page_end": int(values["end"]),
    }


MAX_FETCH_TEXT_BYTES = 400  # Snippet cap; canonical citation is separate.


def _cap_fetch_text(value: Any) -> str:
    return str(value or "").encode("utf-8")[:MAX_FETCH_TEXT_BYTES].decode("utf-8", errors="ignore")


def _fetch_local(cite: dict[str, Any], *, shared_only: bool) -> dict[str, Any]:
    from dashboard_config import HOSTNAME

    try:
        hit = fetch_passage(_db(), **{k: v for k, v in cite.items() if k != "node"}, shared_only=shared_only)
    except AmbiguousPassageError as exc:
        raise HTTPException(status_code=409, detail="citation digest is ambiguous") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="citation not found") from exc
    hit = replace(hit, node=HOSTNAME or "local")
    return {
        "citation": hit.citation,
        "cite_key": hit.cite_key,
        "cite_uri": hit.cite_uri,
        "file_sha256": hit.file_sha256,
        "work_sha256": hit.work_sha256,
        "source_id": hit.source_id,
        "node": hit.node,
        "work_id": hit.work_id,
        "passage_id": hit.passage_id,
        "page_start": hit.page_start,
        "page_end": hit.page_end,
        "text": _cap_fetch_text(hit.text),
        "token_estimate": hit.token_estimate,
    }


@router.get("/fetch")
async def library_fetch(
    cite: str = Query(..., min_length=1, max_length=512),
    scope: str = Query("federated", pattern="^(local|federated)$"),
    _principal: Principal = Depends(require_library_peer),  # noqa: B008
) -> dict[str, Any]:
    """Fetch one bounded, digest-verified passage through trusted fleet nodes."""
    import httpx
    from dashboard_config import FLEET_NODES, HOSTNAME
    from library.pool import validate_node_url

    ref = _parse_cite(cite)
    local_name = HOSTNAME or "local"
    local_missing: HTTPException | None = None
    if scope == "local" or ref["node"] in (None, local_name, "local"):
        try:
            return _fetch_local(ref, shared_only=_principal.id == "fleet-peer")
        except HTTPException as exc:
            if scope == "local" or ref["node"] is not None:
                raise
            local_missing = exc
    targets = [(ref["node"], FLEET_NODES.get(ref["node"]))] if ref["node"] else sorted(FLEET_NODES.items())
    targets = [(name, url) for name, url in targets if url and name != local_name]
    token = os.environ.get("HUB_FLEET_TOKEN", "")
    hits: list[dict[str, Any]] = []
    if token and targets:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.0)) as client:
            for node, url in targets:
                if not url:
                    continue
                try:
                    target = validate_node_url(url, FLEET_NODES)
                    response = await client.get(
                        f"{target}/api/v1/library/fetch",
                        params={"cite": cite, "scope": "local"},
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    if response.status_code == 200:
                        payload = response.json()
                        if not isinstance(payload, dict):
                            continue
                        try:
                            returned = _parse_cite(str(payload.get("cite_key", "")))
                        except HTTPException:
                            continue
                        sha = str(payload.get("file_sha256", "")).lower()
                        work_sha = str(payload.get("work_sha256", "")).lower()
                        if (
                            payload.get("source_id") != ref["source_id"]
                            or int(payload.get("work_id", 0)) != ref["work_id"]
                            or int(payload.get("passage_id", 0)) != ref["passage_id"]
                            or int(payload.get("page_start", 0)) != ref["page_start"]
                            or int(payload.get("page_end", 0)) != ref["page_end"]
                            or not re.fullmatch(r"[0-9a-f]{64}", sha)
                            or not re.fullmatch(r"[0-9a-f]{64}", work_sha)
                            or not sha.startswith(ref["sha_prefix"])
                            or returned["source_id"] != ref["source_id"]
                            or returned["work_id"] != ref["work_id"]
                            or returned["passage_id"] != ref["passage_id"]
                            or returned["page_start"] != ref["page_start"]
                            or returned["page_end"] != ref["page_end"]
                            or returned["sha_prefix"] != ref["sha_prefix"]
                        ):
                            continue
                        payload["text"] = _cap_fetch_text(payload.get("text"))
                        payload["node"] = node
                        hits.append(payload)
                except (httpx.HTTPError, ValueError):
                    continue
    if not hits:
        if local_missing is not None:
            raise local_missing
        raise HTTPException(status_code=404, detail="citation not found on registered nodes")
    identities = {
        (
            str(hit.get("file_sha256", "")).lower(),
            int(hit.get("page_start", 0)),
            int(hit.get("page_end", 0)),
            str(hit.get("text", "")),
        )
        for hit in hits
    }
    if len(identities) != 1:
        raise HTTPException(status_code=409, detail="citation is ambiguous across registered nodes")
    return hits[0]


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


@router.post("/verify")
async def library_verify_route(
    request: Request,
    _principal: Principal = Depends(require_library_peer),  # noqa: B008
) -> dict[str, Any]:
    try:
        payload = await request.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="request body must be JSON") from exc
    if not isinstance(payload, dict) or set(payload) - {"content", "run_id"}:
        raise HTTPException(status_code=422, detail="expected content and optional run_id")
    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=422, detail="content must be text")
    if payload.get("run_id") is not None and not isinstance(payload["run_id"], str):
        raise HTTPException(status_code=422, detail="run_id must be a string")
    try:
        run_id = payload.get("run_id")
        if run_id is not None:
            from library.retrieval_ledger import authorize_run_owner
            from staff.store import get_run_store

            authorize_run_owner(get_run_store(), run_id=run_id, principal_id=_principal.id)
        return await asyncio.to_thread(verify_content, _db(), content, run_id=run_id, repo_root=Path.cwd())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/verify")
def library_verify_citation_route(
    cite: str = Query(..., min_length=1, max_length=500),
    _principal: Principal = Depends(require_library_peer),  # noqa: B008
) -> dict[str, Any]:
    result = verify_content(_db(), cite)
    if not result["citations"]:
        raise HTTPException(status_code=422, detail="cite must contain a canonical library citation")
    return result
