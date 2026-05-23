"""Aggregate stats and usage monitoring routes.

Extracted from ``backend/routers/repos.py`` to keep modules under the
500-line soft cap. Routes:
  GET /api/stats
  GET /api/usage

Depends on the dependency singletons injected by ``server.py`` via
``repos.set_dependencies``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Request
from proxy_utils import proxy_to_hub, should_proxy_fleet_to_hub

router = APIRouter(tags=["repos"])

_STATS_FANOUT_TIMEOUT_S = 6.0
_STATS_REPO_SAMPLE_LIMIT = 12


def _state() -> Any:
    """Return the dependency state from :mod:`repos` lazily."""
    # Imported lazily so that dependency injection in ``repos.set_dependencies``
    # is honoured regardless of import order.
    from . import repos as _repos

    return _repos


@router.get("/api/stats")
async def get_stats(request: Request) -> Any:
    """Aggregate organization, runner, queue, and workflow statistics."""
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    repos_mod = _state()
    cache_get = repos_mod._cache_get
    cache_set = repos_mod._cache_set
    gh_api_admin = repos_mod._gh_api_admin
    run_cmd = repos_mod._run_cmd
    get_recent_org_repos = repos_mod._get_recent_org_repos
    get_fleet_nodes_impl = repos_mod._get_fleet_nodes_impl
    queue_impl = repos_mod._queue_impl
    org = repos_mod.ORG
    stats_ttl = repos_mod._STATS_TTL

    cached = cache_get("stats", stats_ttl)
    if cached is not None:
        return cached

    degraded_reasons: list[str] = []

    async def _with_budget(label: str, coro: Any, fallback: Any, timeout_s: float = _STATS_FANOUT_TIMEOUT_S) -> Any:
        try:
            return await asyncio.wait_for(coro, timeout=timeout_s)
        except TimeoutError:
            degraded_reasons.append(f"{label}_timeout")
            return fallback
        except Exception as exc:  # noqa: BLE001 - stats must degrade, not pin the event loop
            degraded_reasons.append(f"{label}_error:{type(exc).__name__}")
            return fallback

    runners_data = cache_get("runners", 25.0)
    if runners_data is None:
        runners_data = await _with_budget("runners", gh_api_admin(f"/orgs/{org}/actions/runners"), {"runners": []})
        cache_set("runners", runners_data)
    runners = runners_data.get("runners", [])

    repos = await _with_budget("recent_repos", get_recent_org_repos(limit=30), [])

    async def _fetch_repo_runs_local(repo_name: str, per_page: int = 10) -> list[dict]:
        code, stdout, _ = await run_cmd(
            ["gh", "api", f"/repos/{org}/{repo_name}/actions/runs?per_page={per_page}"],
            timeout=15,
        )
        if code != 0:
            return []
        try:
            return json.loads(stdout).get("workflow_runs", [])
        except (json.JSONDecodeError, ValueError):
            return []

    async def _github_search_total_local(query: str) -> int:
        code, stdout, _ = await run_cmd(
            ["gh", "api", f"search/issues?q={query}&per_page=1"],
            timeout=15,
        )
        if code != 0:
            return 0
        try:
            return int(json.loads(stdout).get("total_count", 0))
        except (json.JSONDecodeError, TypeError, ValueError):
            return 0

    sampled_repos = repos[:_STATS_REPO_SAMPLE_LIMIT]
    all_runs_nested = await _with_budget(
        "workflow_runs",
        asyncio.gather(*[_fetch_repo_runs_local(repo["name"], per_page=10) for repo in sampled_repos]),
        [],
    )
    runs = [run for repo_runs in all_runs_nested for run in repo_runs]
    runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    runs = runs[:100]

    online = sum(1 for r in runners if r["status"] == "online")
    busy = sum(1 for r in runners if r.get("busy"))
    completed = [r for r in runs if r.get("conclusion")]
    successes = sum(1 for r in completed if r["conclusion"] == "success")
    failures = sum(1 for r in completed if r["conclusion"] == "failure")

    org_open_issues, org_open_prs, queue_data, fleet_data = await _with_budget(
        "summary_fanout",
        asyncio.gather(
            _github_search_total_local(f"org:{org}+is:open+is:issue"),
            _github_search_total_local(f"org:{org}+is:open+is:pr"),
            queue_impl(),
            get_fleet_nodes_impl(),
        ),
        (0, 0, {}, {}),
    )

    result = {
        "runners_total": len(runners),
        "runners_online": online,
        "runners_busy": busy,
        "runners_idle": max(0, online - busy),
        "runners_offline": max(0, len(runners) - online),
        "runs_total": len(runs),
        "runs_success": successes,
        "runs_failure": failures,
        "runs_completed": len(completed),
        "success_rate": round(successes / len(completed) * 100) if completed else 0,
        "in_progress": queue_data.get("in_progress_count", 0),
        "queued": queue_data.get("queued_count", 0),
        "queue_total": queue_data.get("total", 0),
        "org_open_issues": org_open_issues,
        "org_open_prs": org_open_prs,
        "machines_total": fleet_data.get("count", 0),
        "machines_online": fleet_data.get("online_count", 0),
        "machines_offline": max(0, fleet_data.get("count", 0) - fleet_data.get("online_count", 0)),
        "repos_sampled": len(sampled_repos),
        "degraded": bool(degraded_reasons),
        "degraded_reasons": degraded_reasons,
    }
    cache_set("stats", result)
    return result


@router.get("/api/usage")
async def get_usage_monitoring(request: Request) -> dict:
    """Return normalized subscription and local tool usage summaries."""
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    repos_mod = _state()
    cache_get = repos_mod._cache_get
    cache_set = repos_mod._cache_set
    usage_monitoring = repos_mod._usage_monitoring
    usage_ttl = repos_mod._USAGE_MONITORING_TTL

    cached = cache_get("usage_monitoring", usage_ttl)
    if cached is not None:
        return cached

    summary = usage_monitoring.normalize_usage_summary(usage_monitoring.load_usage_sources_config())
    cache_set("usage_monitoring", summary)
    return summary
