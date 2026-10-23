"""Route helpers for bounded source/work/page library passage fetches."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException, Request
from identity import Principal
from library.citations import _cap_fetch_text, _parse_cite
from library.fetch_locator import fetch_locator
from library.pool import validate_node_url
from library.run_reference_recording import record_run_references, requested_run_id
from library.search import AmbiguousPassageError
from routers.library_read import requires_shared_content

PAGE_FETCH_DEADLINE_S = 4.0
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _payload_identity(payload: Any, node: str, source_id: str, work_id: int, page: int) -> tuple[Any, ...]:
    if not isinstance(payload, dict):
        raise ValueError("peer returned an invalid payload")
    sha = str(payload.get("file_sha256") or "").lower()
    work_sha = str(payload.get("work_sha256") or "").lower()
    if not _DIGEST.fullmatch(sha) or not _DIGEST.fullmatch(work_sha):
        raise ValueError("peer returned an invalid content digest")
    if (
        payload.get("node") != node
        or payload.get("source_id") != source_id
        or type(payload.get("work_id")) is not int
        or payload["work_id"] != work_id
        or type(payload.get("passage_id")) is not int
        or payload["passage_id"] < 1
        or type(payload.get("page_start")) is not int
        or type(payload.get("page_end")) is not int
        or payload["page_start"] < 1
        or payload["page_end"] < payload["page_start"]
        or not payload["page_start"] <= page <= payload["page_end"]
        or not isinstance(payload.get("text"), str)
    ):
        raise ValueError("peer returned a mismatched locator")
    try:
        reference = _parse_cite(str(payload.get("cite_key") or ""))
    except HTTPException as exc:
        raise ValueError("peer returned an invalid canonical citation") from exc
    if (
        reference["node"] != node
        or reference["source_id"] != source_id
        or reference["work_id"] != work_id
        or reference["passage_id"] != payload["passage_id"]
        or reference["page_start"] != payload["page_start"]
        or reference["page_end"] != payload["page_end"]
        or reference["sha_prefix"] != sha[:8]
    ):
        raise ValueError("peer returned a mismatched canonical citation")
    payload["text"] = _cap_fetch_text(payload["text"])
    payload["file_sha256"] = sha
    payload["work_sha256"] = work_sha
    return (
        payload["source_id"],
        payload["work_id"],
        payload["passage_id"],
        sha,
        work_sha,
        payload["page_start"],
        payload["page_end"],
        payload["text"],
    )


def _local_payload(
    db_path: Path,
    *,
    node: str,
    source_id: str,
    work_id: int,
    page: int,
    shared_only: bool,
) -> dict[str, Any] | None:
    try:
        hit = fetch_locator(db_path, source_id=source_id, work_id=work_id, page=page, shared_only=shared_only)
    except LookupError:
        return None
    except AmbiguousPassageError as exc:
        raise HTTPException(status_code=409, detail="page locator is ambiguous") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "citation": hit.citation,
        "cite_key": hit.cite_key,
        "cite_uri": hit.cite_uri,
        "file_sha256": hit.file_sha256,
        "work_sha256": hit.work_sha256,
        "source_id": hit.source_id,
        "node": node,
        "work_id": hit.work_id,
        "passage_id": hit.passage_id,
        "page_start": hit.page_start,
        "page_end": hit.page_end,
        "text": _cap_fetch_text(hit.text),
        "token_estimate": hit.token_estimate,
    }


async def _remote_payload(
    client: httpx.AsyncClient,
    *,
    node: str,
    url: str,
    nodes: dict[str, str],
    token: str,
    source_id: str,
    work_id: int,
    page: int,
) -> dict[str, Any] | None:
    target = validate_node_url(url, nodes)
    response = await client.get(
        f"{target}/api/v1/library/fetch",
        params={"source_id": source_id, "work_id": work_id, "page": page, "scope": "local"},
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise ValueError(f"registered node {node} returned HTTP {response.status_code}")
    payload = response.json()
    _payload_identity(payload, node, source_id, work_id, page)
    return payload


async def fetch_page_locator(
    request: Request,
    principal: Principal,
    db_path: Path,
    *,
    source_id: str | None,
    work_id: int | None,
    page: int | None,
    scope: str,
    local_node: str,
    node: str | None = None,
) -> dict[str, Any]:
    """Resolve a locator locally, on one registered node, or across the fleet."""
    run_id = requested_run_id(request, principal)
    if source_id is None or work_id is None or page is None:
        raise HTTPException(status_code=422, detail="source_id, work_id, and page are required together")
    if scope not in ("local", "federated"):
        raise HTTPException(status_code=422, detail="scope must be local or federated")

    from dashboard_config import FLEET_NODES

    nodes = dict(FLEET_NODES)
    if node is not None and node not in nodes and node not in (local_node, "local"):
        raise HTTPException(status_code=422, detail="node must be a registered fleet node")
    if node in (local_node, "local") or (node is None and scope == "local"):
        result = await asyncio.to_thread(
            _local_payload,
            db_path,
            node=local_node,
            source_id=source_id,
            work_id=work_id,
            page=page,
            shared_only=requires_shared_content(principal) or scope == "federated",
        )
        if result is None:
            raise HTTPException(status_code=404, detail="page locator not found")
        try:
            _payload_identity(result, local_node, source_id, work_id, page)
        except (KeyError, TypeError, ValueError, HTTPException) as exc:
            raise HTTPException(status_code=502, detail="local library returned an invalid locator") from exc
        record_run_references(run_id, [result], db_path)
        return result

    targets = (
        [(node, nodes[node])]
        if node is not None
        else sorted((name, url) for name, url in nodes.items() if name != local_node)
    )
    if node is None and scope == "federated":
        targets = sorted((name, url) for name, url in nodes.items() if name != local_node)
    elif node is None:
        targets = []
    token = os.environ.get("HUB_FLEET_TOKEN", "")
    if targets and not token:
        raise HTTPException(status_code=503, detail="fleet token is not configured")

    results: list[dict[str, Any] | None] = []
    local_result: dict[str, Any] | None = None
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(PAGE_FETCH_DEADLINE_S, connect=min(1.0, PAGE_FETCH_DEADLINE_S))
        ) as client:
            async with asyncio.timeout(PAGE_FETCH_DEADLINE_S):
                remote_requests = [
                    _remote_payload(
                        client,
                        node=target_node,
                        url=url,
                        nodes=nodes,
                        token=token,
                        source_id=source_id,
                        work_id=work_id,
                        page=page,
                    )
                    for target_node, url in targets
                ]
                if node is None and scope == "federated":
                    local_task = asyncio.to_thread(
                        _local_payload,
                        db_path,
                        node=local_node,
                        source_id=source_id,
                        work_id=work_id,
                        page=page,
                        shared_only=True,
                    )
                    combined: list[Any] = await asyncio.gather(local_task, *remote_requests, return_exceptions=True)
                    for item in combined:
                        if isinstance(item, asyncio.CancelledError):
                            raise item
                        if isinstance(item, HTTPException):
                            raise item
                        if isinstance(item, Exception):
                            raise ValueError("registered library node lookup failed") from item
                    local_result = combined[0]
                    results = combined[1:]
                elif remote_requests:
                    remote_results: list[Any] = await asyncio.gather(*remote_requests, return_exceptions=True)
                    results = remote_results
                    for item in results:
                        if isinstance(item, asyncio.CancelledError):
                            raise item
                        if isinstance(item, HTTPException):
                            raise item
                        if isinstance(item, Exception):
                            raise ValueError("registered library node lookup failed") from item
    except (TimeoutError, httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="registered library nodes could not be fully checked") from exc

    hits = [item for item in ([local_result] + results) if item is not None]
    if not hits:
        raise HTTPException(status_code=404, detail="page locator not found on registered nodes")
    try:
        identities = {_payload_identity(hit, str(hit["node"]), source_id, work_id, page) for hit in hits}
    except (KeyError, TypeError, ValueError, HTTPException) as exc:
        raise HTTPException(status_code=503, detail="registered node returned an invalid locator") from exc
    if len(identities) != 1:
        raise HTTPException(status_code=409, detail="page locator is ambiguous across registered nodes")
    result = sorted(hits, key=lambda hit: (hit.get("node") != local_node, str(hit.get("node"))))[0]
    record_run_references(run_id, [result], db_path)
    return result
