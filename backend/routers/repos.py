"""Repository, PR, and issue inventory routes.

Extracted from server.py (issue #360).
Routes:
  GET /api/repos
  GET /api/prs
  GET /api/prs/{owner}/{repo_name}/{number}
  GET /api/issues
  GET /api/tests/ci-results
  POST /api/tests/rerun
  GET /api/stats
  GET /api/usage
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from identity import require_scope
from proxy_utils import proxy_to_hub, should_proxy_fleet_to_hub

log = logging.getLogger("dashboard.repos")
router = APIRouter(tags=["repos"])

# ---------------------------------------------------------------------------
# Injected dependencies (set by server.py after import)
# ---------------------------------------------------------------------------

_cache_get = None
_cache_set = None
_cache_delete = None
_run_cmd = None
_gh_api_admin = None
_get_recent_org_repos = None
_get_fleet_nodes_impl = None
_queue_impl = None
_pr_inventory = None
_issue_inventory = None
_linear_router = None
_linear_inventory = None
_unified_issue_inventory = None
_lease_synchronizer = None
_usage_monitoring = None

ORG: str = "D-sorganization"

_REPOS_TTL = 120.0
_CI_TEST_RESULTS_TTL = 60.0
_STATS_TTL = 30.0
_USAGE_MONITORING_TTL = 60.0

_CI_FLEET_REPOS = [
    "Repository_Management",
    "AffineDrift",
    "Controls",
    "Drake_Models",
    "Games",
    "Gasification_Model",
    "MEB_Conversion",
    "MLProjects",
    "Movement_Optimizer",
    "MuJoCo_Models",
    "OpenSim_Models",
    "Pinocchio_Models",
    "Playground",
    "QuatEngine",
    "Tools",
    "UpstreamDrift",
    "Worksheet-Workshop",
]


def set_dependencies(
    *,
    cache_get,
    cache_set,
    cache_delete,
    run_cmd,
    gh_api_admin,
    get_recent_org_repos,
    get_fleet_nodes_impl,
    queue_impl,
    pr_inventory,
    issue_inventory,
    linear_router,
    linear_inventory,
    unified_issue_inventory,
    lease_synchronizer,
    usage_monitoring,
    org: str,
    repos_ttl: float = 120.0,
    ci_test_results_ttl: float = 60.0,
    stats_ttl: float = 30.0,
    usage_monitoring_ttl: float = 60.0,
) -> None:
    """Inject server-level singletons (called from server.py)."""
    global _cache_get, _cache_set, _cache_delete, _run_cmd, _gh_api_admin
    global _get_recent_org_repos, _get_fleet_nodes_impl, _queue_impl
    global _pr_inventory, _issue_inventory, _linear_router, _linear_inventory
    global _unified_issue_inventory, _lease_synchronizer, _usage_monitoring
    global ORG, _REPOS_TTL, _CI_TEST_RESULTS_TTL, _STATS_TTL, _USAGE_MONITORING_TTL
    _cache_get = cache_get
    _cache_set = cache_set
    _cache_delete = cache_delete
    _run_cmd = run_cmd
    _gh_api_admin = gh_api_admin
    _get_recent_org_repos = get_recent_org_repos
    _get_fleet_nodes_impl = get_fleet_nodes_impl
    _queue_impl = queue_impl
    _pr_inventory = pr_inventory
    _issue_inventory = issue_inventory
    _linear_router = linear_router
    _linear_inventory = linear_inventory
    _unified_issue_inventory = unified_issue_inventory
    _lease_synchronizer = lease_synchronizer
    _usage_monitoring = usage_monitoring
    ORG = org
    _REPOS_TTL = repos_ttl
    _CI_TEST_RESULTS_TTL = ci_test_results_ttl
    _STATS_TTL = stats_ttl
    _USAGE_MONITORING_TTL = usage_monitoring_ttl


@router.get("/api/repos")
async def get_repos(request: Request):
    """Get all org repos with open PRs, open issues, and last CI status."""
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    cached = _cache_get("repos", _REPOS_TTL)
    if cached is not None:
        return cached

    repos: list[dict] = []
    for page in range(1, 3):
        code, stdout, _stderr = await _run_cmd(
            ["gh", "api", f"/orgs/{ORG}/repos?per_page=100&page={page}&sort=updated&direction=desc"],
            timeout=30,
        )
        if code != 0:
            break
        try:
            batch = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            break
        if not batch:
            break
        repos.extend(batch)

    results: list[dict] = []

    async def enrich_repo(repo: dict) -> dict:
        name = repo["name"]
        full_name = repo["full_name"]
        info: dict[str, Any] = {
            "name": name,
            "full_name": full_name,
            "description": repo.get("description", ""),
            "url": repo.get("html_url", ""),
            "private": repo.get("private", False),
            "language": repo.get("language"),
            "default_branch": repo.get("default_branch", "main"),
            "updated_at": repo.get("updated_at", ""),
            "open_issues_count": repo.get("open_issues_count", 0),
            "open_prs": 0,
            "open_issues": 0,
            "last_ci_status": None,
            "last_ci_conclusion": None,
            "last_ci_run_url": None,
            "last_ci_updated": None,
        }
        pr_code, pr_out, _ = await _run_cmd(
            ["gh", "api", "--paginate", f"/repos/{full_name}/pulls?state=open&per_page=100"],
            timeout=30,
        )
        if pr_code == 0:
            try:
                info["open_prs"] = len(json.loads(pr_out))
            except (json.JSONDecodeError, ValueError):
                pass
        info["open_issues"] = max(0, info["open_issues_count"] - info["open_prs"])
        run_code, run_out, _ = await _run_cmd(
            ["gh", "api", f"/repos/{full_name}/actions/runs?per_page=1"],
            timeout=15,
        )
        if run_code == 0:
            try:
                runs_data = json.loads(run_out)
                runs_list = runs_data.get("workflow_runs", [])
                if runs_list:
                    last_run = runs_list[0]
                    info["last_ci_status"] = last_run.get("status")
                    info["last_ci_conclusion"] = last_run.get("conclusion")
                    info["last_ci_run_url"] = last_run.get("html_url")
                    info["last_ci_updated"] = last_run.get("updated_at")
            except (json.JSONDecodeError, ValueError):
                pass
        return info

    batch_size = 5
    for i in range(0, len(repos), batch_size):
        batch = repos[i : i + batch_size]
        batch_results = await asyncio.gather(*[enrich_repo(r) for r in batch])
        results.extend(batch_results)

    results.sort(key=lambda r: (r["last_ci_updated"] or "",), reverse=True)
    result = {"repos": results, "total_count": len(results), "org": ORG}
    _cache_set("repos", result)
    return result


@router.get("/api/prs")
async def get_prs(
    repo: list[str] | None = None,
    include_drafts: bool = True,
    author: str | None = None,
    label: list[str] | None = None,
    limit: int = 500,
) -> dict:
    """Aggregate open pull-requests across organisation repositories."""
    if repo:
        repos = list(repo)
    else:
        org_repos = await _get_recent_org_repos(limit=50)
        repos = [r["full_name"] for r in org_repos]

    return await _pr_inventory.fetch_all_prs(
        repos,
        include_drafts=include_drafts,
        author=author,
        labels=list(label) if label else None,
        limit=limit,
    )


@router.get("/api/prs/{owner}/{repo_name}/{number}")
async def get_pr_detail(owner: str, repo_name: str, number: int) -> dict:
    """Return detailed information for a single pull-request."""
    try:
        return await _pr_inventory.fetch_pr_detail(owner, repo_name, number)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/api/issues")
async def get_issues(
    repo: list[str] | None = None,
    state: str = "open",
    label: list[str] | None = None,
    source: str = "github",
    assignee: str | None = None,
    pickable_only: bool = False,
    complexity: list[str] | None = None,
    effort: list[str] | None = None,
    judgement: list[str] | None = None,
    limit: int = 500,
) -> dict:
    """Aggregate open issues across organisation repositories."""
    if repo:
        repos = list(repo)
    else:
        org_repos = await _get_recent_org_repos(limit=50)
        repos = [r["full_name"] for r in org_repos]

    labels = list(label) if label else None
    complexity_filters = list(complexity) if complexity else None
    effort_filters = list(effort) if effort else None
    judgement_filters = list(judgement) if judgement else None

    if source == "github":
        issues = await _issue_inventory.fetch_all_issues(
            repos,
            state=state,
            labels=labels,
            assignee=assignee,
            pickable_only=pickable_only,
            complexity=complexity_filters,
            effort=effort_filters,
            judgement=judgement_filters,
            limit=limit,
        )
    elif source in {"linear", "unified"}:
        linear_config = _linear_router.load_linear_config()
        if not _linear_router.has_configured_linear_key(linear_config):
            raise HTTPException(status_code=503, detail=_linear_router.LINEAR_NOT_CONFIGURED_DETAIL)
        linear_client = _linear_router.build_linear_client(linear_config)
        try:
            if source == "linear":
                issues = await _linear_inventory.fetch_all_issues(
                    linear_config,
                    linear_client,
                    state=state,
                    pickable_only=pickable_only,
                    complexity=complexity_filters,
                    effort=effort_filters,
                    judgement=judgement_filters,
                    limit=limit,
                )
                issues["stats"] = {"linear_total": len(issues.get("items", []))}
            else:
                issues = await _unified_issue_inventory.fetch_unified_issues(
                    github_repos=repos,
                    linear_config=linear_config,
                    linear_client=linear_client,
                    state=state,
                    labels=labels,
                    assignee=assignee,
                    pickable_only=pickable_only,
                    complexity=complexity_filters,
                    effort=effort_filters,
                    judgement=judgement_filters,
                    limit=limit,
                )
        finally:
            await linear_client.aclose()
    else:
        raise HTTPException(status_code=422, detail="source must be one of github, linear, unified")

    sync_items = issues if isinstance(issues, list) else issues.get("items", [])
    if isinstance(sync_items, list):
        await _lease_synchronizer.sync_github_leases(sync_items)

    return issues


@router.get("/api/tests/ci-results")
async def get_tests_ci_results() -> dict:
    """Return recent ci-standard workflow runs for key fleet repos."""
    cached = _cache_get("ci_test_results", _CI_TEST_RESULTS_TTL)
    if cached is not None:
        return cached

    results = []
    for repo_name in _CI_FLEET_REPOS:
        try:
            data = await _gh_api_admin(
                f"/repos/{ORG}/{repo_name}/actions/workflows/ci-standard.yml/runs?per_page=3&branch=main"
            )
            runs = data.get("workflow_runs", []) if data else []
            if runs:
                latest = runs[0]
                results.append({
                    "repo": repo_name,
                    "run_id": latest.get("id"),
                    "run_number": latest.get("run_number"),
                    "status": latest.get("status"),
                    "conclusion": latest.get("conclusion"),
                    "head_branch": latest.get("head_branch"),
                    "html_url": latest.get("html_url"),
                    "created_at": latest.get("created_at"),
                    "updated_at": latest.get("updated_at"),
                })
            else:
                results.append({"repo": repo_name, "run_id": None, "conclusion": None})
        except Exception as e:  # noqa: BLE001
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            results.append({"repo": repo_name, "run_id": None, "conclusion": "error"})

    out: dict = {"results": results}
    _cache_set("ci_test_results", out)
    return out


@router.post("/api/tests/rerun")
async def rerun_ci_test(
    request: Request,
    *,
    principal=Depends(require_scope("tests.rerun")),  # noqa: B008
) -> dict:
    """Re-run a failed GitHub Actions workflow run (failed jobs only)."""
    body = await request.json()
    repo_name = body.get("repo", "")
    run_id = body.get("run_id")

    if not repo_name or not run_id:
        raise HTTPException(status_code=400, detail="repo and run_id are required")

    try:
        code, _stdout, stderr = await _run_cmd(
            [
                "gh", "api",
                f"/repos/{ORG}/{repo_name}/actions/runs/{run_id}/rerun-failed-jobs",
                "--method", "POST",
            ]
        )
        if code != 0:
            raise HTTPException(status_code=502, detail=f"GitHub API error: {stderr}")
        _cache_delete("ci_test_results")
        return {"status": "triggered", "repo": repo_name, "run_id": run_id}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        log.exception("Failed to rerun run %s in %s", run_id, repo_name)
        raise HTTPException(status_code=500, detail="Internal server error") from None


@router.get("/api/stats")
async def get_stats(request: Request):
    """Aggregate organization, runner, queue, and workflow statistics."""
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    cached = _cache_get("stats", _STATS_TTL)
    if cached is not None:
        return cached

    runners_data = _cache_get("runners", 25.0)
    if runners_data is None:
        runners_data = await _gh_api_admin(f"/orgs/{ORG}/actions/runners")
        _cache_set("runners", runners_data)
    runners = runners_data.get("runners", [])

    repos = await _get_recent_org_repos(limit=30)

    async def _fetch_repo_runs_local(repo_name: str, per_page: int = 10) -> list[dict]:
        code, stdout, _ = await _run_cmd(
            ["gh", "api", f"/repos/{ORG}/{repo_name}/actions/runs?per_page={per_page}"],
            timeout=15,
        )
        if code != 0:
            return []
        try:
            return json.loads(stdout).get("workflow_runs", [])
        except (json.JSONDecodeError, ValueError):
            return []

    async def _github_search_total_local(query: str) -> int:
        code, stdout, _ = await _run_cmd(
            ["gh", "api", f"search/issues?q={query}&per_page=1"],
            timeout=15,
        )
        if code != 0:
            return 0
        try:
            return int(json.loads(stdout).get("total_count", 0))
        except (json.JSONDecodeError, TypeError, ValueError):
            return 0

    all_runs_nested = await asyncio.gather(
        *[_fetch_repo_runs_local(repo["name"], per_page=10) for repo in repos[:20]]
    )
    runs = [run for repo_runs in all_runs_nested for run in repo_runs]
    runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    runs = runs[:100]

    online = sum(1 for r in runners if r["status"] == "online")
    busy = sum(1 for r in runners if r.get("busy"))
    completed = [r for r in runs if r.get("conclusion")]
    successes = sum(1 for r in completed if r["conclusion"] == "success")
    failures = sum(1 for r in completed if r["conclusion"] == "failure")

    org_open_issues, org_open_prs, queue_data, fleet_data = await asyncio.gather(
        _github_search_total_local(f"org:{ORG}+is:open+is:issue"),
        _github_search_total_local(f"org:{ORG}+is:open+is:pr"),
        _queue_impl(),
        _get_fleet_nodes_impl(),
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
        "repos_sampled": len(repos[:20]),
    }
    _cache_set("stats", result)
    return result


@router.get("/api/usage")
async def get_usage_monitoring(request: Request) -> dict:
    """Return normalized subscription and local tool usage summaries."""
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    cached = _cache_get("usage_monitoring", _USAGE_MONITORING_TTL)
    if cached is not None:
        return cached

    summary = _usage_monitoring.normalize_usage_summary(_usage_monitoring.load_usage_sources_config())
    _cache_set("usage_monitoring", summary)
    return summary
