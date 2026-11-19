"""Tests for fleet status stale-while-revalidate cache (#2110, #4576)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import cache_utils
import fleet_status_cache as fsc
import pytest

_NOW = datetime(2026, 11, 19, 22, 0, tzinfo=UTC)


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
    assert result["local"]["hostname"] == "h"
    assert result["local"]["stale"] is True
    assert result["local"]["_sampled_at"] is None
    assert "_cache_stale" not in result
    assert "_stale" not in result
    stored = cache_utils.cache_get(key, fsc.FLEET_STATUS_FRESH_TTL_S)
    assert stored == {"local": {"hostname": "h"}}


def test_host_telemetry_freshness_is_per_host_and_keeps_aggregate_cache_flag() -> None:
    """#4576 slice 1: consumers degrade per host, not fleet-wide, when the cache is stale."""
    payload = {
        "_cache_stale": True,
        "desk": {"hostname": "desk", "timestamp": "2026-11-19T21:59:30+00:00"},
        "laptop": {"hostname": "laptop", "timestamp": "2026-11-19T21:50:00Z"},
        "offline": {"status": "offline", "error": "timeout"},
        "_email_intake": {"ok": True},
    }

    out = fsc.with_host_telemetry_freshness(payload, now=_NOW, stale_after_s=90)

    assert out["_cache_stale"] is True
    assert out["desk"]["stale"] is False
    assert out["desk"]["_sampled_at"] == "2026-11-19T21:59:30Z"
    assert out["laptop"]["stale"] is True
    assert out["laptop"]["_sampled_at"] == "2026-11-19T21:50:00Z"
    assert out["offline"]["stale"] is True
    assert out["offline"]["_sampled_at"] is None
    assert out["_email_intake"] == {"ok": True}
    assert "stale" not in payload["desk"]


def test_host_telemetry_fails_closed_on_naive_or_unparsed_sample_time() -> None:
    payload = {
        "naive": {"timestamp": "2026-11-19T21:59:30"},
        "garbage": {"timestamp": "not-a-time"},
    }

    out = fsc.with_host_telemetry_freshness(payload, now=_NOW, stale_after_s=90)

    assert out["naive"]["stale"] is True
    assert out["naive"]["_sampled_at"] is None
    assert out["garbage"]["stale"] is True
    assert out["garbage"]["_sampled_at"] is None


@pytest.mark.asyncio
async def test_serve_fleet_status_renames_aggregate_flag_and_annotates_hosts() -> None:
    key = fsc.fleet_status_cache_key(exclude_pools=True)
    cached = {
        "desk": {"hostname": "desk", "timestamp": "2026-11-19T21:59:30+00:00"},
        "laptop": {"hostname": "laptop", "timestamp": "2026-11-19T21:50:00Z"},
    }
    cache_utils.cache_set(key, cached)

    def _tiered_get(cache_key: str, ttl: float):
        if cache_key != key:
            return cache_utils.cache_get(cache_key, ttl)
        if ttl <= fsc.FLEET_STATUS_FRESH_TTL_S:
            return None
        if ttl <= fsc.FLEET_STATUS_STALE_TTL_S:
            return cached
        return None

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(fsc, "cache_get", _tiered_get)
    monkeypatch.setattr(cache_utils, "cache_get", _tiered_get)

    async def slow_collect() -> dict:
        await asyncio.sleep(0.2)
        return {"desk": {"hostname": "fresh"}}

    try:
        served = await fsc.serve_fleet_status(key, slow_collect, wait_timeout=0.01, now=_NOW)
    finally:
        monkeypatch.undo()

    assert served.get("_cache_stale") is True
    assert "_stale" not in served
    assert served["desk"]["stale"] is False
    assert served["desk"]["_sampled_at"] == "2026-11-19T21:59:30Z"
    assert served["laptop"]["stale"] is True
    assert "stale" not in cached["desk"]
