"""Regression coverage for dashboard endpoints that must degrade under load."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))


def _request(path: str) -> Request:
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


@pytest.mark.asyncio
async def test_stats_endpoint_returns_degraded_payload_when_fanout_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow GitHub fanout must not starve the dashboard health endpoint."""
    from routers import repos, repos_stats

    cache: dict[str, object] = {}

    def cache_get(key: str, _ttl: float) -> object | None:
        return cache.get(key)

    def cache_set(key: str, value: object) -> None:
        cache[key] = value

    async def slow_run_cmd(*_args: object, **_kwargs: object) -> tuple[int, str, str]:
        await asyncio.sleep(0.05)
        return 0, '{"workflow_runs":[]}', ""

    async def gh_api_admin(_path: str) -> dict[str, object]:
        return {"runners": []}

    async def get_recent_org_repos(*, limit: int) -> list[dict[str, str]]:
        assert limit == 30
        return [{"name": "Repository_Management"}]

    async def queue_impl() -> dict[str, int]:
        return {"in_progress_count": 0, "queued_count": 0, "total": 0}

    async def fleet_nodes_impl() -> dict[str, int]:
        return {"count": 1, "online_count": 1}

    repos.set_dependencies(
        cache_get=cache_get,
        cache_set=cache_set,
        cache_delete=lambda _key: None,
        run_cmd=slow_run_cmd,
        gh_api_admin=gh_api_admin,
        get_recent_org_repos=get_recent_org_repos,
        get_fleet_nodes_impl=fleet_nodes_impl,
        queue_impl=queue_impl,
        pr_inventory=SimpleNamespace(),
        issue_inventory=SimpleNamespace(),
        linear_router=SimpleNamespace(),
        linear_inventory=SimpleNamespace(),
        unified_issue_inventory=SimpleNamespace(),
        lease_synchronizer=SimpleNamespace(),
        usage_monitoring=SimpleNamespace(),
        org="D-sorganization",
    )
    monkeypatch.setattr(repos_stats, "_STATS_FANOUT_TIMEOUT_S", 0.01)

    payload = await repos_stats.get_stats(_request("/api/stats"))

    assert payload["degraded"] is True
    assert "workflow_runs_timeout" in payload["degraded_reasons"]
    assert payload["runs_total"] == 0
    assert payload["queue_total"] == 0


def test_fleet_nodes_endpoint_has_cache_and_independent_remote_probes() -> None:
    """Fleet node aggregation must cache and let each remote probe fail independently."""
    source = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")

    assert "_FLEET_NODES_CACHE_TTL_S" in source
    assert '_cache_get("fleet_nodes", _FLEET_NODES_CACHE_TTL_S)' in source
    assert "await asyncio.gather(*[fetch_node(name, url) for name, url in FLEET_NODES.items()])" in source
    assert "fleet node fanout exceeded" not in source
