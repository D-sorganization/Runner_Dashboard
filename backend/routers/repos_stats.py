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
# Last-known-good snapshot of a fully-healthy /api/stats. Only written when the
# computation is NOT degraded, so a transient rate-limit/timeout window can fall
# back to real numbers instead of publishing zeros. 24h is generous: fleet
# topology and open PR/issue counts change slowly, and a fresh healthy compute
# overwrites it the moment GitHub recovers.
_STATS_STALE_KEY = "stats:stale"
_STATS_STALE_TTL = 60.0 * 60.0 * 24.0
# Reuse a recent /api/queue result (which has its own last-known-good fallback)
# instead of re-running the 24-repo queue fan-out inside the stats budget.
_STATS_QUEUE_REUSE_TTL = 120.0


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

    # Last-known-good snapshot — used to backfill any field whose live fetch
    # fails this cycle, so a partial GitHub outage degrades one number instead
    # of zeroing the whole summary.
    last_good = cache_get(_STATS_STALE_KEY, _STATS_STALE_TTL) or {}

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

    # --- Runners (independent; fall back to last-known-good counts) -----------
    runners_data = cache_get("runners", 25.0)
    if runners_data is None:
        runners_data = await _with_budget("runners", gh_api_admin(f"/orgs/{org}/actions/runners"), None)
        if isinstance(runners_data, dict):
            cache_set("runners", runners_data)
    runners = runners_data.get("runners", []) if isinstance(runners_data, dict) else None

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
        """Return the search total, RAISING on failure so the caller's budget
        records a degraded reason and backfills last-known-good (rather than
        silently reporting 0, which is indistinguishable from a real empty)."""
        code, stdout, _ = await run_cmd(
            ["gh", "api", f"search/issues?q={query}&per_page=1"],
            timeout=15,
        )
        if code != 0:
            raise RuntimeError(f"gh search exited {code}")
        return int(json.loads(stdout).get("total_count", 0))

    sampled_repos = repos[:_STATS_REPO_SAMPLE_LIMIT]
    all_runs_nested = await _with_budget(
        "workflow_runs",
        asyncio.gather(*[_fetch_repo_runs_local(repo["name"], per_page=10) for repo in sampled_repos]),
        [],
    )
    runs = [run for repo_runs in all_runs_nested for run in repo_runs]
    runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    runs = runs[:100]

    completed = [r for r in runs if r.get("conclusion")]
    successes = sum(1 for r in completed if r["conclusion"] == "success")
    failures = sum(1 for r in completed if r["conclusion"] == "failure")

    # --- Queue: reuse the resilient /api/queue cache (which already serves
    # last-known-good on failure). Only re-run the fan-out if no recent cache
    # exists, and on its own budget so it can't starve the rest of the summary.
    queue_data = cache_get("queue", _STATS_QUEUE_REUSE_TTL)
    if queue_data is None:
        queue_data = await _with_budget("queue", queue_impl(), None, timeout_s=8.0)

    # --- Org PR / issue counts: independent budgets so a slow search API call
    # cannot zero the queue and machine numbers alongside it.
    issues_total = await _with_budget("issues_search", _github_search_total_local(f"org:{org}+is:open+is:issue"), None)
    prs_total = await _with_budget("prs_search", _github_search_total_local(f"org:{org}+is:open+is:pr"), None)

    # --- Fleet machines (independent) ----------------------------------------
    fleet_data = await _with_budget("fleet", get_fleet_nodes_impl(), None)

    # --- Assemble, backfilling any failed field from last-known-good ----------
    if runners is None:
        runners_total = last_good.get("runners_total", 0)
        runners_online = last_good.get("runners_online", 0)
        runners_busy = last_good.get("runners_busy", 0)
        runners_idle = last_good.get("runners_idle", 0)
        runners_offline = last_good.get("runners_offline", 0)
    else:
        runners_online = sum(1 for r in runners if r.get("status") == "online")
        runners_busy = sum(1 for r in runners if r.get("busy"))
        runners_total = len(runners)
        runners_idle = max(0, runners_online - runners_busy)
        runners_offline = max(0, runners_total - runners_online)

    if isinstance(queue_data, dict):
        in_progress = queue_data.get("in_progress_count", 0)
        queued = queue_data.get("queued_count", 0)
        queue_total = queue_data.get("total", 0)
    else:
        in_progress = last_good.get("in_progress", 0)
        queued = last_good.get("queued", 0)
        queue_total = last_good.get("queue_total", 0)

    org_open_issues = issues_total if issues_total is not None else last_good.get("org_open_issues", 0)
    org_open_prs = prs_total if prs_total is not None else last_good.get("org_open_prs", 0)

    if isinstance(fleet_data, dict):
        machines_total = fleet_data.get("count", 0)
        machines_online = fleet_data.get("online_count", 0)
    else:
        machines_total = last_good.get("machines_total", 0)
        machines_online = last_good.get("machines_online", 0)

    result = {
        "runners_total": runners_total,
        "runners_online": runners_online,
        "runners_busy": runners_busy,
        "runners_idle": runners_idle,
        "runners_offline": runners_offline,
        "runs_total": len(runs),
        "runs_success": successes,
        "runs_failure": failures,
        "runs_completed": len(completed),
        "success_rate": round(successes / len(completed) * 100) if completed else 0,
        "in_progress": in_progress,
        "queued": queued,
        "queue_total": queue_total,
        "org_open_issues": org_open_issues,
        "org_open_prs": org_open_prs,
        "machines_total": machines_total,
        "machines_online": machines_online,
        "machines_offline": max(0, machines_total - machines_online),
        "repos_sampled": len(sampled_repos),
        "degraded": bool(degraded_reasons),
        "degraded_reasons": degraded_reasons,
        "stale": bool(degraded_reasons),
    }
    cache_set("stats", result)
    # Only persist a fully-healthy result as the last-known-good snapshot.
    if not degraded_reasons:
        cache_set(_STATS_STALE_KEY, result)
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
