"""Runs and Workflows routes.

Covers:
  - GET  /api/runs                   – recent workflow runs (org-wide sample)
  - GET  /api/runs/enriched          – runs with job-placement enrichment
  - GET  /api/runs/{repo}            – runs for a single repository
  - GET  /api/scheduled-workflows    – cron-schedule inventory
  - GET  /api/workflows/list         – per-repo workflow catalogue
  - POST /api/workflows/dispatch     – manual workflow_dispatch trigger
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime as _dt_mod
import json
import logging
import os
import secrets
import tempfile
from pathlib import Path

import scheduled_workflows as scheduled_workflow_inventory
from cache_utils import cache_get, cache_set
from dashboard_config import ORG, REPO_ROOT, RUN_JOB_ENRICHMENT_LIMIT
from error_models import bad_gateway, validation_error
from fastapi import APIRouter, Depends, HTTPException, Request
from gh_utils import gh_api, gh_api_raw
from identity import Principal, require_scope
from input_validation import validate_workflow_inputs
from proxy_utils import proxy_to_hub, should_proxy_fleet_to_hub
from security import validate_repo_slug
from system_utils import run_cmd

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

log = logging.getLogger("dashboard.runs_workflows")
router = APIRouter(tags=["runs_workflows"])


# ─── Internal helpers ─────────────────────────────────────────────────────────


async def _get_recent_org_repos(limit: int = 30) -> list[dict]:
    """Return the most recently updated repositories for the org."""
    cached = cache_get(f"org_repos:{limit}", 300.0)
    if cached is not None:
        return cached
    try:
        data = await gh_api(f"/orgs/{ORG}/repos?sort=updated&per_page={min(limit, 100)}")
        repos = data if isinstance(data, list) else data.get("items", [])
        cache_set(f"org_repos:{limit}", repos)
        return repos
    except Exception:  # noqa: BLE001
        return []


async def _fetch_repo_runs(repo_name: str, per_page: int = 10, status: str | None = None) -> list[dict]:
    """Fetch workflow runs for a single repository."""
    repo_name = validate_repo_slug(repo_name)
    url = f"/repos/{ORG}/{repo_name}/actions/runs?per_page={per_page}"
    if status:
        url += f"&status={status}"
    try:
        data = await gh_api(url)
        return data.get("workflow_runs", [])
    except Exception:  # noqa: BLE001
        return []


async def _enrich_run_with_job_placement(run: dict) -> dict:
    """Add job-level runner placement data to a workflow run."""
    enriched = dict(run)
    repo = run.get("repository", {}).get("name") or run.get("repo")
    run_id = run.get("id")
    if not repo or not run_id:
        return enriched
    repo = validate_repo_slug(repo)
    try:
        data = await gh_api(f"/repos/{ORG}/{repo}/actions/runs/{run_id}/jobs")
        jobs = data.get("jobs", [])
        enriched["jobs"] = [
            {
                "id": j.get("id"),
                "name": j.get("name"),
                "status": j.get("status"),
                "conclusion": j.get("conclusion"),
                "runner_name": j.get("runner_name"),
                "runner_id": j.get("runner_id"),
                "started_at": j.get("started_at"),
                "completed_at": j.get("completed_at"),
            }
            for j in jobs
        ]
    except Exception:  # noqa: BLE001
        enriched["jobs"] = []
    return enriched


async def _scheduled_workflows_impl(
    *,
    include_archived: bool = False,
    repo_limit: int = 100,
) -> dict:
    """Collect the read-only scheduled workflow inventory."""
    cache_key = f"scheduled-workflows:{include_archived}:{repo_limit}"
    cached = cache_get(cache_key, 300.0)
    if cached is not None:
        return cached

    raw_timeout = os.environ.get("SCHEDULED_WORKFLOWS_TIMEOUT", "20")
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError):
        timeout = 20.0

    try:
        report = await asyncio.wait_for(
            scheduled_workflow_inventory.collect_inventory(
                ORG,
                gh_api,
                gh_api_raw,
                repo_limit=repo_limit,
                include_archived=include_archived,
            ),
            timeout=timeout,
        )
        payload = report.to_dict()
        payload["status"] = "ok"
        cache_set(cache_key, payload)
    except (TimeoutError, asyncio.TimeoutError):  # noqa: UP041
        payload = {
            "status": "degraded",
            "organization": ORG,
            "generated_at": datetime.now(UTC).isoformat(),
            "repository_count": 0,
            "scheduled_workflow_count": 0,
            "repositories": [],
            "dry_run_plan": {
                "mode": "dry_run",
                "write_actions_allowed": False,
                "confirmation_required": True,
                "audit_required": True,
                "steps": [],
            },
            "error": "Scheduled workflow inventory timed out.",
        }
    return payload


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.get("/api/runs")
async def get_runs(request: Request, per_page: int = 30) -> dict:
    """Get recent workflow runs across the org by sampling the most active repos.

    GitHub's REST API has no org-level /actions/runs endpoint; runs must be
    fetched per-repo.  We sample the 10 most recently updated repos and return
    up to ``per_page`` runs sorted newest-first.
    """
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    cache_key = f"runs:{per_page}"
    cached = cache_get(cache_key, 120.0)
    if cached is not None:
        return cached

    repos = await _get_recent_org_repos(limit=20)
    if not repos:
        return {"workflow_runs": [], "total_count": 0}

    runs_per_repo = max(3, per_page // max(len(repos[:10]), 1))
    sample = repos[:10]
    all_runs_nested = await asyncio.gather(*[_fetch_repo_runs(r["name"], per_page=runs_per_repo) for r in sample])
    all_runs: list[dict] = [run for sublist in all_runs_nested for run in sublist]

    all_runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    top_runs = all_runs[:per_page]

    result = {"workflow_runs": top_runs, "total_count": len(top_runs)}
    cache_set(f"runs:{per_page}", result)
    return result


@router.get("/api/runs/enriched")
async def get_enriched_runs(request: Request, per_page: int = 50) -> dict:
    """Return recent runs with dashboard-friendly enrichment fields."""
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    cache_key = f"runs-enriched:{per_page}"
    cached = cache_get(cache_key, 120.0)
    if cached is not None:
        return cached

    data = await get_runs(request, per_page=per_page)
    runs = data.get("workflow_runs", [])
    enrichable = runs[:RUN_JOB_ENRICHMENT_LIMIT]
    enriched = list(await asyncio.gather(*[_enrich_run_with_job_placement(run) for run in enrichable]))
    enriched.extend(dict(run) for run in runs[RUN_JOB_ENRICHMENT_LIMIT:])
    result = {"workflow_runs": enriched, "total_count": len(enriched)}
    cache_set(cache_key, result)
    return result


@router.get("/api/runs/{repo}")
async def get_repo_runs(request: Request, repo: str, per_page: int = 20):
    """Get recent workflow runs for a specific repo."""
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    repo = validate_repo_slug(repo)
    data = await gh_api(f"/repos/{ORG}/{repo}/actions/runs?per_page={per_page}")
    return data


@router.get("/api/scheduled-workflows")
async def get_scheduled_workflows(
    request: Request,
    include_archived: bool = False,
    repo_limit: int = 100,
):
    """Inventory GitHub Actions schedules across org repositories.

    This endpoint is read-only. It gathers workflow metadata, extracts cron
    expressions from workflow YAML where available, and attaches a dry-run plan
    that describes future changes without executing them.
    """
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    return await _scheduled_workflows_impl(
        include_archived=include_archived,
        repo_limit=repo_limit,
    )


@router.get("/api/workflows/list")
async def list_workflows() -> dict:
    """List all workflows per repository with trigger capabilities and latest run."""
    cached = cache_get("workflows_list", 120.0)
    if cached is not None:
        return cached

    repos = await _get_recent_org_repos(limit=30)

    async def get_repo_workflows(repo_name: str) -> list[dict]:
        code, out, _ = await run_cmd(
            ["gh", "api", f"/repos/{ORG}/{repo_name}/actions/workflows", "--paginate"],
            timeout=20,
            cwd=REPO_ROOT,
        )
        if code != 0:
            return []
        try:
            data = json.loads(out)
            workflows = data.get("workflows", [])
        except Exception:  # noqa: BLE001
            return []
        result = []
        for wf in workflows:
            wf_id = wf.get("id")
            triggers = []
            if wf.get("path"):
                code2, out2, _ = await run_cmd(
                    ["gh", "api", f"/repos/{ORG}/{repo_name}/contents/{wf['path']}"],
                    timeout=10,
                    cwd=REPO_ROOT,
                )
                if code2 == 0:
                    try:
                        content_data = json.loads(out2)
                        content = base64.b64decode(content_data.get("content", "")).decode("utf-8", errors="replace")
                        if "workflow_dispatch" in content:
                            triggers.append("manual")
                        if "schedule" in content:
                            triggers.append("schedule")
                        if "push" in content or "pull_request" in content:
                            triggers.append("push_pr")
                        if "workflow_run" in content:
                            triggers.append("workflow_run")
                    except Exception:  # noqa: BLE001
                        pass
            code3, out3, _ = await run_cmd(
                ["gh", "api", f"/repos/{ORG}/{repo_name}/actions/workflows/{wf_id}/runs?per_page=3"],
                timeout=10,
                cwd=REPO_ROOT,
            )
            latest_run = None
            recent_runs = []
            if code3 == 0:
                try:
                    runs_data = json.loads(out3)
                    all_runs = runs_data.get("workflow_runs", [])
                    if all_runs:
                        latest_run = {
                            "id": all_runs[0].get("id"),
                            "status": all_runs[0].get("status"),
                            "conclusion": all_runs[0].get("conclusion"),
                            "created_at": all_runs[0].get("created_at"),
                            "html_url": all_runs[0].get("html_url"),
                            "head_branch": all_runs[0].get("head_branch"),
                        }
                        recent_runs = [
                            {
                                "id": r.get("id"),
                                "status": r.get("status"),
                                "conclusion": r.get("conclusion"),
                                "created_at": r.get("created_at"),
                                "html_url": r.get("html_url"),
                            }
                            for r in all_runs[:3]
                        ]
                except Exception:  # noqa: BLE001
                    pass
            result.append(
                {
                    "id": wf_id,
                    "name": wf.get("name", ""),
                    "path": wf.get("path", ""),
                    "state": wf.get("state", ""),
                    "html_url": wf.get("html_url", ""),
                    "triggers": triggers,
                    "latest_run": latest_run,
                    "recent_runs": recent_runs,
                    "repository": repo_name,
                }
            )
        return result

    results = await asyncio.gather(*[get_repo_workflows(r["name"]) for r in repos[:20]])
    all_workflows: list[dict] = []
    for wf_list in results:
        all_workflows.extend(wf_list)

    result = {"workflows": all_workflows, "total": len(all_workflows)}
    cache_set("workflows_list", result)
    return result


@router.post("/api/workflows/dispatch")
async def dispatch_workflow(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("workflows.control")),  # noqa: B008
) -> dict:
    """Dispatch a workflow via workflow_dispatch."""
    body = await request.json()
    repo = str(body.get("repository", "")).strip()
    workflow_id = body.get("workflow_id")
    ref = str(body.get("ref", "main")).strip()
    # Validate inputs BEFORE any I/O — caps key count, value length, and rejects
    # non-string values to prevent oversized workflow_dispatch payloads (#411).
    inputs = validate_workflow_inputs(body.get("inputs"))
    correlation_id = request.headers.get("X-Correlation-Id", secrets.token_hex(8))
    inputs["correlation_id"] = correlation_id
    approved_by = principal.id

    if not repo or not workflow_id:
        raise HTTPException(
            status_code=422,
            detail=validation_error("repository and workflow_id are required").model_dump(exclude_none=True),
        )
    repo = validate_repo_slug(repo)

    endpoint = f"/repos/{ORG}/{repo}/actions/workflows/{workflow_id}/dispatches"
    payload = {"ref": ref, "inputs": inputs}
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        pf = f.name
    try:
        code, _, stderr = await run_cmd(
            ["gh", "api", endpoint, "--method", "POST", "--input", pf],
            timeout=30,
            cwd=REPO_ROOT,
        )
    finally:
        with contextlib.suppress(OSError):
            Path(pf).unlink()
    if code != 0:
        log.warning(
            "workflow_dispatch failed: repo=%s workflow_id=%s stderr=%s",
            repo,
            workflow_id,
            stderr.strip()[:300],
        )
        raise HTTPException(
            status_code=502,
            detail=bad_gateway("Workflow dispatch failed").model_dump(exclude_none=True),
        )

    log.info(
        "workflow_dispatch audit: repo=%s workflow_id=%s ref=%s approved_by=%s",
        repo,
        workflow_id,
        ref,
        approved_by,
    )
    return {
        "status": "dispatched",
        "repository": repo,
        "workflow_id": workflow_id,
        "ref": ref,
    }
