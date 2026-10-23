"""Shared catalogs are refreshed without exposing private paths or losing offline history."""

import asyncio

import httpx
import pytest
from library.pool import PooledCatalog, refresh_catalog


def test_invalid_refresh_preserves_snapshot_and_revoked_nodes_are_removed() -> None:
    catalog = PooledCatalog()
    catalog.update_node_sources("peer", [{"id": "old"}])
    snapshot = catalog.list_sources()
    with pytest.raises(ValueError):
        catalog.update_node_sources("peer", [{"id": "bad", "file_count": "not-a-number"}])
    assert catalog.list_sources() == snapshot
    snapshot[0]["label"] = "tampered"
    assert catalog.list_sources()[0]["label"] == "old"
    catalog.retain_nodes({"desk"})
    assert catalog.list_sources() == []


@pytest.mark.asyncio
async def test_refresh_authenticates_filters_and_revokes_sources() -> None:
    catalog = PooledCatalog()
    sources = [
        {"id": "pub", "shared": True, "root_path": "/private/path", "node": "spoof"},
        {"id": "private", "shared": False},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["shared"] == "1"
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(200, json={"sources": sources})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        kwargs = dict(
            catalog=catalog,
            fleet_nodes={"peer": "http://peer"},
            local_node="desk",
            hub_token="test-token",
            client=client,
        )
        assert await refresh_catalog(**kwargs) == []
        (row,) = catalog.list_sources()
        assert row["id"] == "pub" and row["node"] == "peer" and row["available"] is True
        assert "root_path" not in row
        sources.clear()
        await refresh_catalog(**kwargs)
        assert catalog.list_sources() == []


@pytest.mark.asyncio
async def test_offline_retains_last_seen_and_missing_token_never_probes() -> None:
    catalog = PooledCatalog()
    catalog.update_node_sources("peer", [{"id": "pub", "shared": True}])
    last_seen = catalog.list_sources()[0]["last_seen"]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        for token in ("", "test-token"):
            assert await refresh_catalog(
                catalog=catalog, fleet_nodes={"peer": "http://peer"}, local_node="desk", hub_token=token, client=client
            ) == ["peer"]
    assert len(calls) == 1
    assert catalog.list_sources()[0] | {} == {
        "id": "pub",
        "label": "pub",
        "node": "peer",
        "file_count": 0,
        "shared": True,
        "last_seen": last_seen,
        "available": False,
    }


@pytest.mark.asyncio
async def test_global_deadline_cancels_slow_peers_and_retains_fast_result() -> None:
    catalog = PooledCatalog()
    cancelled = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "fast":
            return httpx.Response(200, json={"sources": [{"id": "pub", "shared": True}]})
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()
        return httpx.Response(200, json={"sources": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await refresh_catalog(
            catalog=catalog,
            fleet_nodes={"fast": "http://fast", "slow": "http://slow"},
            local_node="desk",
            hub_token="test",
            client=client,
            deadline_s=0.02,
        )
    assert result == ["slow"] and cancelled.is_set()
    assert catalog.list_sources()[0]["node"] == "fast"
