"""/api/scheduled-workflows keeps collecting after a timed-out request (#1718 review).

The inventory walk took longer than the 20 s budget on the live hub, the
timeout cancelled it, and nothing was cached, so every visit started cold and
answered 504 through the proxy.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import cache_utils
import proxy_utils
import pytest
from routers import runs_workflows as rw


class _Report:
    def to_dict(self) -> dict[str, Any]:
        return {"repositories": [{"name": "alpha"}], "scheduled_workflow_count": 1}


@pytest.fixture
def slow_inventory(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    monkeypatch.setattr(cache_utils, "_main_cache", cache_utils.Cache("scheduled-test"))
    monkeypatch.setattr(cache_utils, "_swr_refreshes", {})
    monkeypatch.setenv("SCHEDULED_WORKFLOWS_TIMEOUT", "0.01")
    calls: list[int] = []

    async def collect(*args: object, **kwargs: object) -> _Report:
        calls.append(1)
        await asyncio.sleep(0.1)
        return _Report()

    monkeypatch.setattr(rw.scheduled_workflow_inventory, "collect_inventory", collect)
    return calls


@pytest.mark.unit
def test_timed_out_inventory_finishes_in_background_and_is_served_next(slow_inventory: list[int]) -> None:
    async def run() -> tuple[dict, dict]:
        first = await rw._scheduled_workflows_impl()
        await asyncio.gather(*cache_utils._swr_refreshes.values())
        second = await rw._scheduled_workflows_impl()
        return first, second

    first, second = asyncio.run(run())

    assert first["status"] == "degraded"
    assert first["repositories"] == []
    assert second["status"] == "ok"
    assert second["scheduled_workflow_count"] == 1
    assert len(slow_inventory) == 1


@pytest.mark.unit
def test_wait_stays_below_hub_proxy_timeout_for_a_proxied_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hub must answer degraded before a proxying node's own timeout fires (#1745).

    ``proxy_to_hub`` gives up after ``HUB_PROXY_TIMEOUT_S`` seconds, so the hub's
    own wait for a cold inventory must stay below that, even when an operator
    configures a large ``SCHEDULED_WORKFLOWS_TIMEOUT``.
    """
    monkeypatch.setattr(cache_utils, "_main_cache", cache_utils.Cache("scheduled-test-clamp"))
    monkeypatch.setattr(cache_utils, "_swr_refreshes", {})
    monkeypatch.setenv("SCHEDULED_WORKFLOWS_TIMEOUT", "60")
    monkeypatch.setattr(proxy_utils, "HUB_PROXY_TIMEOUT_S", 3.2)

    async def never_finishes(*args: object, **kwargs: object) -> _Report:
        await asyncio.sleep(999)
        return _Report()

    monkeypatch.setattr(rw.scheduled_workflow_inventory, "collect_inventory", never_finishes)

    start = time.monotonic()
    result = asyncio.run(rw._scheduled_workflows_impl())
    elapsed = time.monotonic() - start

    assert result["status"] == "degraded"
    assert elapsed < proxy_utils.HUB_PROXY_TIMEOUT_S
