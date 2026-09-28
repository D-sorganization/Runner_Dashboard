"""cache_get_swr: slow aggregates serve stale data and refresh once (#1718 review).

The live Workflows and Scheduled-workflows endpoints took 15-90 s on every
cache miss; the browser's six connections per host then queued every other
panel behind them.
"""

from __future__ import annotations

import asyncio
from typing import Any

import cache_utils as cu
import pytest


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cu, "_main_cache", cu.Cache("swr-test"))
    monkeypatch.setattr(cu, "_swr_refreshes", {})


def _counting(value: Any, delay: float = 0.0) -> tuple[list[int], Any]:
    calls: list[int] = []

    async def compute() -> Any:
        calls.append(1)
        await asyncio.sleep(delay)
        return value

    return calls, compute


def _get(compute: Any, *, wait: float = 5.0) -> Any:
    return cu.cache_get_swr("k", compute, fresh_ttl=60, stale_ttl=3600, wait_timeout=wait, on_timeout=lambda: "warming")


@pytest.mark.unit
def test_cold_miss_computes_once_for_concurrent_callers() -> None:
    calls, compute = _counting({"v": 1}, delay=0.05)

    async def run() -> list[Any]:
        return list(await asyncio.gather(_get(compute), _get(compute), _get(compute)))

    assert asyncio.run(run()) == [{"v": 1}] * 3
    assert len(calls) == 1
    assert cu.cache_get("k", 60) == {"v": 1}


@pytest.mark.unit
def test_fresh_value_skips_compute() -> None:
    cu.cache_set("k", "cached")
    calls, compute = _counting("new")
    assert asyncio.run(_get(compute)) == "cached"
    assert calls == []


@pytest.mark.unit
def test_stale_value_is_served_immediately_and_refreshed_in_background(monkeypatch: pytest.MonkeyPatch) -> None:
    cu.cache_set("k", "old")
    monkeypatch.setattr(cu, "cache_get", lambda key, ttl: "old" if ttl > 60 else None)
    calls, compute = _counting("new", delay=0.01)

    async def run() -> Any:
        served = await _get(compute)
        await cu._swr_refreshes["k"]
        return served

    assert asyncio.run(run()) == "old"
    assert len(calls) == 1


@pytest.mark.unit
def test_slow_cold_miss_times_out_but_keeps_refreshing() -> None:
    calls, compute = _counting("done", delay=0.2)

    async def run() -> tuple[Any, Any]:
        first = await _get(compute, wait=0.01)
        await cu._swr_refreshes["k"]
        return first, cu.cache_get("k", 60)

    assert asyncio.run(run()) == ("warming", "done")
    assert len(calls) == 1


@pytest.mark.unit
def test_cold_failure_propagates_and_is_retried_next_call() -> None:
    attempts: list[int] = []

    async def flaky() -> str:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("github down")
        return "ok"

    with pytest.raises(RuntimeError):
        asyncio.run(_get(flaky))
    assert asyncio.run(_get(flaky)) == "ok"


@pytest.mark.unit
def test_contract_rejects_inverted_ttls() -> None:
    async def compute() -> str:
        return "x"

    with pytest.raises(AssertionError):
        asyncio.run(cu.cache_get_swr("k", compute, fresh_ttl=10, stale_ttl=5, wait_timeout=1, on_timeout=lambda: None))
