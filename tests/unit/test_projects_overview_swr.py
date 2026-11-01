"""Project overviews are served stale-while-revalidate (#3673).

The Staff inbox polls ``GET /api/v1/staff/inbox`` every 30 s and builds one
project overview per configured repo. When the 10-minute cache expired, the next
poll rebuilt every overview inline (two GitHub file reads plus the open-item
pages per repo), so one request in twenty paid the full cold cost. An expired
overview is now returned at once while a single background refresh runs.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import cache_utils
import pytest
from projects import service

pytestmark = pytest.mark.unit

KEY = "projects:Alpha"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_utils, "_main_cache", cache_utils.Cache("projects-swr-unit"))
    monkeypatch.setattr(cache_utils, "_swr_refreshes", {})
    monkeypatch.setattr(service, "last_steward_run", lambda repo, store=None: {"repo": repo, "run": "latest"})


def _age_entry(key: str, seconds: float) -> None:
    value, _ = cache_utils._main_cache._store[key]  # noqa: SLF001
    cache_utils._main_cache._store[key] = (value, time.monotonic() - seconds)  # noqa: SLF001


class _Builder:
    def __init__(self, delay: float = 0.0) -> None:
        self.calls = 0
        self.delay = delay

    async def __call__(self, repo: str, fetch: Any = None, store: Any = None) -> dict[str, Any]:
        self.calls += 1
        await asyncio.sleep(self.delay)
        return {"repo": repo, "build": self.calls, "last_steward_run": None}


@pytest.mark.asyncio
async def test_cold_miss_builds_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    builder = _Builder()
    monkeypatch.setattr(service, "build_project", builder)
    first = await service.project_overview("Alpha")
    second = await service.project_overview("Alpha")
    assert first["build"] == second["build"] == 1
    assert builder.calls == 1


@pytest.mark.asyncio
async def test_expired_overview_is_served_stale_while_one_refresh_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    builder = _Builder(delay=0.05)
    monkeypatch.setattr(service, "build_project", builder)
    cache_utils.cache_set(KEY, {"repo": "Alpha", "build": 0, "last_steward_run": None})
    _age_entry(KEY, service.CACHE_TTL_SECONDS + 1)

    started = time.monotonic()
    results = await asyncio.gather(*(service.project_overview("Alpha") for _ in range(5)))
    elapsed = time.monotonic() - started

    assert [r["build"] for r in results] == [0] * 5
    assert elapsed < builder.delay  # nobody waited for the rebuild
    await asyncio.gather(*cache_utils._swr_refreshes.values())  # noqa: SLF001
    assert builder.calls == 1
    assert (await service.project_overview("Alpha"))["build"] == 1


@pytest.mark.asyncio
async def test_steward_run_is_reread_on_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "build_project", _Builder())
    cache_utils.cache_set(KEY, {"repo": "Alpha", "build": 0, "last_steward_run": None})
    _age_entry(KEY, service.CACHE_TTL_SECONDS + 1)
    stale = await service.project_overview("Alpha")
    assert stale["last_steward_run"] == {"repo": "Alpha", "run": "latest"}
    await asyncio.gather(*cache_utils._swr_refreshes.values())  # noqa: SLF001
    fresh = await service.project_overview("Alpha")
    assert fresh["last_steward_run"] == {"repo": "Alpha", "run": "latest"}


@pytest.mark.asyncio
async def test_cold_miss_that_outlasts_the_wait_returns_a_loading_overview(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "build_project", _Builder(delay=0.2))
    monkeypatch.setattr(service, "PROJECT_COLD_WAIT_SECONDS", 0.01)
    result = await service.project_overview("Alpha")
    assert result["repo"] == "Alpha"
    assert result["charter_present"] is False
    assert "loading" in result["error"]
    await asyncio.gather(*cache_utils._swr_refreshes.values())  # noqa: SLF001
    assert (await service.project_overview("Alpha"))["build"] == 1


@pytest.mark.asyncio
async def test_overview_older_than_the_stale_window_is_rebuilt_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    builder = _Builder()
    monkeypatch.setattr(service, "build_project", builder)
    cache_utils.cache_set(KEY, {"repo": "Alpha", "build": 0, "last_steward_run": None})
    _age_entry(KEY, service.STALE_TTL_SECONDS + 1)
    assert (await service.project_overview("Alpha"))["build"] == 1
