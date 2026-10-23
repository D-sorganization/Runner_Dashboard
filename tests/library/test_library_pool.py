"""Tests for Alexandria Library Pool: federated search and pooled catalog (#2229)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from library.db import init_library_db
from library.pool import FederatedSearchResult, PooledCatalog, federated_search, validate_node_url
from library.search import MAX_BUDGET
from library.sources import add_source
from server import app


def _p(pid: int, wid: int, auth: str, tit: str, tokens: int, score: float = 0.9, node: str = "") -> dict[str, Any]:
    d = dict(passage_id=pid, work_id=wid, author=auth, title=tit, year=2024, score=score, text=f"text {pid}")
    d.update(citation=f"{auth}, {tit}, p. 1", source_id="src", node=node, file_path="a.txt", token_estimate=tokens)
    d["ref"] = dict(node=node, source_id="src", work_id=wid, passage_id=pid, page_start=1, page_end=1)
    d["cite_key"] = f"[ref:{node}:s:w{wid}:p{pid}#1234:p.1-1]"
    return d


def test_ssrf_guard_validates_registered_urls() -> None:
    allowed = {"node1": "http://node1.tailnet:8000", "node2": "https://node2.tailnet:8443"}
    assert validate_node_url("http://node1.tailnet:8000", allowed) == "http://node1.tailnet:8000"
    assert validate_node_url("https://node2.tailnet:8443/", allowed) == "https://node2.tailnet:8443"
    for bad_url in ("http://attacker.com", "ftp://node1.tailnet:8000"):
        with pytest.raises(ValueError):
            validate_node_url(bad_url, allowed)


def test_pooled_catalog_caches_and_retains_offline_sources() -> None:
    catalog = PooledCatalog()
    catalog.update_node_sources("node-desk", [{"id": "crucial-x9", "label": "Crucial X9", "file_count": 142}])
    items = catalog.list_sources()
    assert len(items) == 1 and items[0]["id"] == "crucial-x9" and items[0]["node"] == "node-desk"
    assert items[0]["file_count"] == 142 and "last_seen" in items[0] and catalog.list_sources() == items


@pytest.mark.asyncio
async def test_federated_search_merges_two_nodes_and_enforces_budget() -> None:
    fleet_nodes = {"peer-1": "http://peer1:8000", "peer-2": "http://peer2:8000"}
    resp1 = {"query": "q", "passages": [_p(1, 10, "Alice", "A", 300), _p(2, 10, "Alice", "A", 250)]}
    resp2 = {"query": "q", "passages": [_p(1, 20, "Bob", "B", 350)]}

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=resp1 if "peer1" in str(request.url) else resp2)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await federated_search(
            "q", k=10, budget_tokens=500, fleet_nodes=fleet_nodes, client=client, local_search_fn=lambda *a, **k: []
        )
        assert len(result.passages) >= 1 and result.tokens_used <= 500 and result.unavailable_sources == []
        assert {p["node"] for p in result.passages} <= {"peer-1", "peer-2"}
        assert {"node", "source_id", "work_id", "passage_id"} <= set(result.passages[0]["ref"])


@pytest.mark.asyncio
async def test_federated_search_node_timeout_returns_partial_and_unavailable_sources() -> None:
    fleet_nodes = {"peer-ok": "http://peer-ok:8000", "peer-down": "http://peer-down:8000"}
    resp_ok = {"query": "q", "passages": [_p(9, 99, "Carl", "C", 100)]}

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        if "peer-ok" in str(request.url):
            return httpx.Response(200, json=resp_ok)
        raise httpx.ReadTimeout("node timed out", request=request)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await federated_search("q", fleet_nodes=fleet_nodes, client=client, local_search_fn=lambda *a, **k: [])
        assert len(result.passages) == 1 and result.passages[0]["author"] == "Carl"
        assert result.unavailable_sources == ["peer-down"]


def test_dbc_budget_and_k_bounds() -> None:
    run = pytest.importorskip("asyncio").run
    for b, k in [(0, 10), (MAX_BUDGET + 1, 10), (100, 0)]:
        with pytest.raises(ValueError):
            run(federated_search("q", budget_tokens=b, k=k))


def test_unshared_sources_never_leave_node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from library.ingest import ingest_sources
    from routers.library import set_db_path

    db = init_library_db(tmp_path / "library.sqlite")
    set_db_path(db)
    pub, priv = tmp_path / "pub", tmp_path / "priv"
    pub.mkdir()
    priv.mkdir()
    add_source(db, source_id="shared-src", label="Shared", root_path=pub, shared=True)
    add_source(db, source_id="secret-src", label="Secret", root_path=priv, shared=False)
    (pub / "Pub.txt").write_text("common knowledge fleet", encoding="utf-8")
    (priv / "Priv.txt").write_text("common knowledge secret", encoding="utf-8")
    ingest_sources(db)

    monkeypatch.setenv("HUB_FLEET_TOKEN", "tok")
    client = TestClient(app, raise_server_exceptions=False)
    hdrs = {"Authorization": "Bearer tok"}
    r_src = client.get("/api/v1/library/sources?shared=1", headers=hdrs)
    assert r_src.status_code == 200 and [s["id"] for s in r_src.json()["sources"]] == ["shared-src"]
    r_search = client.get("/api/v1/library/search?q=common&scope=local", headers=hdrs)
    hits = r_search.json()["passages"]
    assert r_search.status_code == 200 and hits and all(h["source_id"] == "shared-src" for h in hits)


def test_peer_auth_fail_closed_token_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app, raise_server_exceptions=False)
    monkeypatch.setenv("HUB_FLEET_TOKEN", "secret-token")
    assert client.get("/api/v1/library/sources?shared=1").status_code == 401
    assert client.get("/api/v1/library/sources?shared=1", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert (
        client.get("/api/v1/library/sources?shared=1", headers={"Authorization": "Bearer secret-token"}).status_code
        == 200
    )

    monkeypatch.delenv("HUB_FLEET_TOKEN", raising=False)
    assert client.get("/api/v1/library/sources?shared=1", headers={"Authorization": "Bearer any"}).status_code in (
        401,
        503,
    )

    from routers.library import require_library_peer

    req = MagicMock(headers={"Authorization": "Bearer any"}, client=MagicMock(host="10.0.0.1"))
    with pytest.raises(HTTPException) as exc:
        require_library_peer(req, header_token="Bearer any")
    assert exc.value.status_code == 503 and "Fleet token not configured" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_search_endpoint_federated_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_res = FederatedSearchResult("test", [_p(1, 2, "A", "T", 25)], 25, 1500, ["down"])
    with patch("routers.library.federated_search", new=AsyncMock(return_value=fake_res)):
        monkeypatch.setenv("HUB_FLEET_TOKEN", "tok")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/library/search?q=test&scope=federated", headers={"Authorization": "Bearer tok"})
        assert resp.status_code == 200 and resp.json()["unavailable_sources"] == ["down"]
