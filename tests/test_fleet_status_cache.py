"""Tests for fleet status stale-while-revalidate cache (#2110)."""

from __future__ import annotations

import asyncio

import cache_utils
import fleet_status_cache as fsc
import pytest


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_utils, "_main_cache", cache_utils.Cache("fleet-status-cache-unit"))
    monkeypatch.setattr(cache_utils, "_swr_refreshes", {})


@pytest.mark.asyncio
async def test_fleet_status_cache_key_varies_by_exclude_pools() -> None:
    assert fsc.fleet_status_cache_key(exclude_pools=False) != fsc.fleet_status_cache_key(exclude_pools=True)


@pytest.mark.asyncio
async def test_serve_fleet_status_returns_fresh_without_stale_flag() -> None:
    key = fsc.fleet_status_cache_key(exclude_pools=True)
    cache_utils.cache_set(key, {"local": {"hostname": "h"}})

    async def collect() -> dict:
        return {"local": {"hostname": "new"}}

    result = await fsc.serve_fleet_status(key, collect)
    assert result == {"local": {"hostname": "h"}}
    assert "_stale" not in result
