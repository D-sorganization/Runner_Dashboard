"""The shared cached-runners helper backs the Conductor capacity gate.

Regression coverage for the admission gate hitting GitHub uncached on every
poll (issue: orchestrator capacity provider bypassed the shared ``runners``
cache). ``gh_utils.get_cached_org_runners`` is now the single source of truth
for the runner inventory, reused by the runners list, the health summary, and
the Conductor capacity provider.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _clear_runner_cache() -> object:
    import cache_utils

    cache_utils.cache_clear()
    yield
    cache_utils.cache_clear()


def test_warm_cache_skips_upstream_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    import cache_utils
    import gh_utils

    cache_utils.cache_set("runners", {"runners": [{"status": "online"}], "total_count": 1})

    calls = {"n": 0}

    async def _should_not_be_called(*_args: object, **_kwargs: object) -> dict:
        calls["n"] += 1
        return {"runners": []}

    monkeypatch.setattr("runner_inventory.fetch_org_runners", _should_not_be_called)

    data = asyncio.run(gh_utils.get_cached_org_runners("D-sorganization"))

    assert data["total_count"] == 1
    assert calls["n"] == 0  # warm cache → zero upstream GitHub calls


def test_cold_cache_fetches_once_then_serves_from_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    import gh_utils

    calls = {"n": 0}

    async def _fetch(*_args: object, **_kwargs: object) -> dict:
        calls["n"] += 1
        return {"runners": [{"status": "idle"}], "total_count": 1}

    monkeypatch.setattr("runner_inventory.fetch_org_runners", _fetch)

    first = asyncio.run(gh_utils.get_cached_org_runners("D-sorganization"))
    second = asyncio.run(gh_utils.get_cached_org_runners("D-sorganization"))

    assert first["total_count"] == 1
    assert second["total_count"] == 1
    assert calls["n"] == 1  # second call is served from the shared cache
