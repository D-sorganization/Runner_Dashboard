"""Fleet library pool: federated search and pooled catalog over Tailscale (#2229)."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from library.search import DEFAULT_BUDGET, MAX_BUDGET, SearchHit, SearchOptions, _pack_hits, search_passages

DEFAULT_PROBE_READ_TIMEOUT_S = 2.0
DEFAULT_PROBE_CONNECT_TIMEOUT_S = 1.0


@dataclass(frozen=True)
class FederatedSearchResult:
    query: str
    passages: list[dict[str, Any]]
    tokens_used: int
    budget_tokens: int
    unavailable_sources: list[str] = field(default_factory=list)
    topic: str | None = None
    repo: str | None = None


class PooledCatalog:
    """In-memory cache of shared library sources across fleet nodes."""

    def __init__(self) -> None:
        self._sources: dict[tuple[str, str], dict[str, Any]] = {}

    def update_node_sources(self, node: str, sources: list[dict[str, Any]]) -> None:
        now_iso = datetime.now(UTC).isoformat()
        for s in sources:
            sid = str(s.get("id") or "")
            if sid:
                self._sources[(node, sid)] = dict(
                    id=sid,
                    label=str(s.get("label") or sid),
                    node=node,
                    file_count=int(s.get("file_count") or 0),
                    shared=True,
                    last_seen=now_iso,
                )

    def list_sources(self) -> list[dict[str, Any]]:
        return sorted(self._sources.values(), key=lambda s: (s["node"], s["id"]))

    def clear(self) -> None:
        self._sources.clear()


_CATALOG = PooledCatalog()


def get_pooled_catalog() -> PooledCatalog:
    return _CATALOG


def validate_node_url(url: str, allowed_nodes: dict[str, str]) -> str:
    """SSRF guard: ensure target URL is strictly in registered fleet nodes."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"invalid node url scheme or netloc: {url}")
    clean = url.rstrip("/")
    if clean not in {u.rstrip("/") for u in allowed_nodes.values()}:
        raise ValueError(f"node url '{url}' is not in registered fleet nodes")
    return clean


def get_fleet_nodes() -> dict[str, str]:
    """Retrieve registered fleet nodes from dashboard config."""
    from dashboard_config import FLEET_NODES

    return dict(FLEET_NODES)


def _hit_to_dict(h: SearchHit, default_node: str) -> dict[str, Any]:
    n = h.node or default_node
    keys = (
        "passage_id work_id author title year page_start page_end text score citation cite_key cite_uri "
        "file_path token_estimate file_sha256 work_sha256"
    ).split()
    d = {k: getattr(h, k) for k in keys}
    d["node"] = n
    d["source_id"] = h.source_id
    d["ref"] = {k: getattr(h, k) for k in ("source_id", "work_id", "passage_id", "page_start", "page_end")}
    d["ref"]["node"] = n
    return d


def _raw_to_search_hit(d: dict[str, Any], node_name: str) -> SearchHit:
    ref = d.get("ref") or {}
    digests = {key: str(d.get(key) or "").lower() for key in ("file_sha256", "work_sha256")}
    if any(value and not re.fullmatch(r"[0-9a-f]{64}", value) for value in digests.values()):
        raise ValueError("invalid peer content digest")
    return SearchHit(
        int(d.get("passage_id") or ref.get("passage_id") or 0),
        int(d.get("work_id") or ref.get("work_id") or 0),
        d.get("author"),
        d.get("title"),
        d.get("year"),
        int(d.get("page_start") or ref.get("page_start") or 1),
        int(d.get("page_end") or ref.get("page_end") or 1),
        str(d.get("text") or ""),
        float(d.get("score") or 0.0),
        d.get("file_path"),
        int(d.get("token_estimate") or 1),
        source_id=str(d.get("source_id") or ref.get("source_id") or "default"),
        node=node_name,
        **digests,
    )


async def federated_search(
    query: str,
    *,
    k: int = 32,
    budget_tokens: int = DEFAULT_BUDGET,
    topic: str | None = None,
    repo: str | None = None,
    db_path: Path | None = None,
    fleet_nodes: dict[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
    hub_token: str | None = None,
    local_search_fn: Callable[..., list[SearchHit]] | None = None,
) -> FederatedSearchResult:
    """Federated search across fleet nodes with RRF fusion and single-point budget enforcement."""
    if budget_tokens < 1 or budget_tokens > MAX_BUDGET:
        raise ValueError(f"budget must be between 1 and {MAX_BUDGET}")
    if k < 1 or k > 100:
        raise ValueError("k must be between 1 and 100")

    import os

    from dashboard_config import HOSTNAME

    local_name = HOSTNAME or "local"
    nodes = fleet_nodes if fleet_nodes is not None else get_fleet_nodes()
    token = hub_token if hub_token is not None else os.environ.get("HUB_FLEET_TOKEN", "")

    local_hits: list[SearchHit] = []
    if local_search_fn is not None:
        local_hits = local_search_fn(query, k=k, budget_tokens=MAX_BUDGET, topic=topic, repo=repo)
    elif db_path is not None and db_path.exists():
        raw = search_passages(
            db_path,
            query,
            k=k,
            budget_tokens=MAX_BUDGET,
            options=SearchOptions(topic=topic, repo=repo, shared_only=True),
        )
        local_hits = [replace(h, node=local_name) for h in raw]

    hit_lists: list[list[SearchHit]] = [local_hits] if local_hits else []
    unavailable_sources: list[str] = []

    async def _query_node(node_name: str, node_url: str, cli: httpx.AsyncClient) -> tuple[str, list[SearchHit] | None]:
        try:
            target = validate_node_url(node_url, nodes)
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            params: dict[str, Any] = {"q": query, "k": k, "budget": MAX_BUDGET, "scope": "local"}
            if topic:
                params["topic"] = topic
            if repo:
                params["repo"] = repo
            timeout = httpx.Timeout(DEFAULT_PROBE_READ_TIMEOUT_S, connect=DEFAULT_PROBE_CONNECT_TIMEOUT_S)
            resp = await cli.get(f"{target}/api/v1/library/search", params=params, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                passages = resp.json().get("passages") or []
                return node_name, [_raw_to_search_hit(p, node_name) for p in passages]
            return node_name, None
        except Exception:
            return node_name, None

    peers = [(n, u) for n, u in nodes.items() if n != local_name]
    if peers:
        async with httpx.AsyncClient() as default_cli:
            active_cli = client or default_cli
            results = await asyncio.gather(*[_query_node(n, u, active_cli) for n, u in peers])
        for node_name, hits in results:
            if hits is None:
                unavailable_sources.append(node_name)
            else:
                hit_lists.append(hits)

    rrf_scores: dict[tuple, float] = {}
    hits_by_key: dict[tuple, SearchHit] = {}
    for hit_list in hit_lists:
        for rank, hit in enumerate(hit_list, start=1):
            digest = hit.file_sha256 or hit.work_sha256
            key = (
                ("content", digest, hit.page_start, hit.page_end, hit.text)
                if digest
                else ("location", hit.node, hit.source_id, hit.work_id, hit.passage_id)
            )
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (60.0 + rank)
            if key not in hits_by_key:
                hits_by_key[key] = hit

    ranked = [
        replace(hits_by_key[k], score=score)
        for k, score in sorted(rrf_scores.items(), key=lambda i: i[1], reverse=True)
    ]
    packed = _pack_hits(ranked[:k], budget_tokens=budget_tokens)
    passages = [_hit_to_dict(h, local_name) for h in packed]

    return FederatedSearchResult(
        query=query,
        passages=passages,
        tokens_used=sum(p["token_estimate"] for p in passages),
        budget_tokens=budget_tokens,
        unavailable_sources=sorted(unavailable_sources),
        topic=topic,
        repo=repo,
    )
