"""Regression coverage for /api/stats partial-failure resilience.

The Overview summary previously bundled the org PR/issue search, the queue
fan-out, and the fleet probe under a single timeout. A slow search (or a
secondary rate-limit) timed out the whole bundle and published ZEROS for PRs,
queue, and machines at once — even though the standalone /api/queue endpoint
was fine. These tests pin the fixed behavior: each source is budgeted
independently, the queue is reused from its own resilient cache, and any failed
field falls back to the last-known-good snapshot instead of zero.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))


def _request(path: str = "/api/stats") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": b"local=1",
            "headers": [],
            "server": ("testserver", 80),
            "scheme": "http",
            "client": ("testclient", 50000),
        }
    )


async def _default_run_cmd(args, timeout: int = 15):  # type: ignore[no-untyped-def]
    return 0, '{"workflow_runs":[]}', ""


async def _default_gh_admin(_path: str):  # type: ignore[no-untyped-def]
    return {"runners": [{"status": "online", "busy": True}, {"status": "online", "busy": False}]}


async def _default_recent(*, limit: int):  # type: ignore[no-untyped-def]
    return [{"name": "Repository_Management"}]


async def _default_queue():  # type: ignore[no-untyped-def]
    return {"in_progress_count": 1, "queued_count": 2, "total": 3}


async def _default_fleet():  # type: ignore[no-untyped-def]
    return {"count": 4, "online_count": 3}


def _wire(cache, **overrides):  # type: ignore[no-untyped-def]
    from routers import repos

    repos.set_dependencies(
        cache_get=lambda k, _ttl: cache.get(k),
        cache_set=lambda k, v: cache.__setitem__(k, v),
        cache_delete=lambda k: cache.pop(k, None),
        run_cmd=overrides.get("run_cmd", _default_run_cmd),
        gh_api_admin=overrides.get("gh_api_admin", _default_gh_admin),
        get_recent_org_repos=overrides.get("recent", _default_recent),
        get_fleet_nodes_impl=overrides.get("fleet", _default_fleet),
        queue_impl=overrides.get("queue_impl", _default_queue),
        pr_inventory=SimpleNamespace(),
        issue_inventory=SimpleNamespace(),
        linear_router=SimpleNamespace(),
        linear_inventory=SimpleNamespace(),
        unified_issue_inventory=SimpleNamespace(),
        lease_synchronizer=SimpleNamespace(),
        usage_monitoring=SimpleNamespace(),
        org="D-sorganization",
    )


async def _search_fails_run_cmd(args, timeout: int = 15):  # type: ignore[no-untyped-def]
    if "search/issues" in " ".join(args):
        return 1, "", "secondary rate limit"
    return 0, '{"workflow_runs":[]}', ""


def test_queue_numbers_survive_search_failure() -> None:
    """The exact reported symptom: searches fail but the queue (served from its
    own cache) must still show real numbers, not zero."""
    cache = {"queue": {"in_progress_count": 3, "queued_count": 3, "total": 6}}
    _wire(cache, run_cmd=_search_fails_run_cmd)

    from routers import repos_stats

    payload = asyncio.run(repos_stats.get_stats(_request()))
    assert payload["queued"] == 3
    assert payload["in_progress"] == 3
    assert payload["queue_total"] == 6
    assert payload["degraded"] is True
    assert any("search" in r for r in payload["degraded_reasons"])


def test_failed_fields_fall_back_to_last_known_good() -> None:
    last_good = {
        "org_open_prs": 42,
        "org_open_issues": 50,
        "in_progress": 1,
        "queued": 1,
        "queue_total": 2,
        "machines_total": 3,
        "machines_online": 3,
    }
    cache = {"stats:stale": last_good}
    _wire(cache, run_cmd=_search_fails_run_cmd)

    from routers import repos_stats

    payload = asyncio.run(repos_stats.get_stats(_request()))
    assert payload["org_open_prs"] == 42
    assert payload["org_open_issues"] == 50
    assert payload["degraded"] is True


def test_healthy_compute_persists_last_known_good() -> None:
    cache: dict[str, object] = {}

    async def run_cmd(args, timeout: int = 15):  # type: ignore[no-untyped-def]
        cmd = " ".join(args)
        if "search/issues" in cmd:
            total = 42 if "is:pr" in cmd else 50
            return 0, f'{{"total_count":{total}}}', ""
        return 0, '{"workflow_runs":[]}', ""

    _wire(cache, run_cmd=run_cmd)

    from routers import repos_stats

    payload = asyncio.run(repos_stats.get_stats(_request()))
    assert payload["degraded"] is False
    assert payload["org_open_prs"] == 42
    assert payload["org_open_issues"] == 50
    assert payload["queued"] == 2
    assert payload["queue_total"] == 3
    assert payload["runners_online"] == 2
    # A fully-healthy result is persisted as the last-known-good snapshot.
    assert cache.get("stats:stale") is not None
    assert cache["stats:stale"]["org_open_prs"] == 42  # type: ignore[index]


def test_degraded_result_is_not_persisted_as_last_known_good() -> None:
    cache: dict[str, object] = {}
    _wire(cache, run_cmd=_search_fails_run_cmd)

    from routers import repos_stats

    payload = asyncio.run(repos_stats.get_stats(_request()))
    assert payload["degraded"] is True
    # Degraded zeros must never overwrite/populate the last-known-good snapshot.
    assert "stats:stale" not in cache
