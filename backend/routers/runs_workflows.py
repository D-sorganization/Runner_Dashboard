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
from typing import Any

import scheduled_workflows as scheduled_workflow_inventory
from cache_utils import cache_get, cache_get_swr, cache_set
from dashboard_config import ORG, REPO_ROOT, RUN_JOB_ENRICHMENT_LIMIT
from error_models import bad_gateway, validation_error
from fastapi import APIRouter, Depends, HTTPException, Request
from gh_utils import gh_api, gh_api_raw
from identity import Principal, require_fleet_peer, require_scope
from input_validation import validate_workflow_inputs
from models.github_payloads import GhJob, GhWorkflowRun
from proxy_utils import proxy_to_hub, should_proxy_fleet_to_hub
from security import validate_repo_slug
from system_utils import run_cmd
from workflow_analysis import infer_machine_from_runner_name, summarize_runs_by_workflow_and_machine

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
    except Exception as e:  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
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
    except Exception as e:  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        return []


_SLIM_REPO_FIELDS = ("id", "name", "full_name", "html_url", "private")
_SLIM_ACTOR_FIELDS = ("login", "id", "avatar_url")
_SLIM_COMMIT_FIELDS = ("id", "message", "timestamp")


def _pick(value: object, fields: tuple[str, ...]) -> dict | None:
    """Return only ``fields`` of a nested GitHub object (``None`` when absent)."""
    if not isinstance(value, dict):
        return None
    return {key: value[key] for key in fields if key in value}


def _slim_run(run: dict) -> dict:
    """Drop the parts of a GitHub run payload the dashboard never reads.

    GitHub embeds two full repository objects and a dozen REST ``*_url``
    links in every run (~15 KB each); the dashboard reads the run's own
    fields, ``repository.name``/``html_url`` and the actors' logins.
    """
    slim = {key: value for key, value in run.items() if key == "html_url" or not key.endswith("_url")}
    slim.pop("head_repository", None)
    for key, fields in (
        ("repository", _SLIM_REPO_FIELDS),
        ("actor", _SLIM_ACTOR_FIELDS),
        ("triggering_actor", _SLIM_ACTOR_FIELDS),
        ("head_commit", _SLIM_COMMIT_FIELDS),
    ):
        if key in slim:
            slim[key] = _pick(slim[key], fields)
    return slim


async def _enrich_run_with_job_placement(run: dict) -> dict:
    """Add job-level runner placement data to a workflow run.

    Uses ``GhWorkflowRun`` and ``GhJob`` typed models to extract fields,
    replacing nested ``.get()`` chains with typed attribute access.
    """
    enriched = dict(run)
    typed_run = GhWorkflowRun.model_validate(run)
    repo = typed_run.repository_name or run.get("repo")
    run_id = typed_run.id
    if not repo or not run_id:
        return enriched
    repo = validate_repo_slug(repo)
    try:
        data = await gh_api(f"/repos/{ORG}/{repo}/actions/runs/{run_id}/jobs")
        jobs = [
            {
                "id": job.id,
                "name": job.name,
                "status": job.status,
                "conclusion": job.conclusion,
                "runner_name": job.runner_name,
                "runner_id": job.runner_id,
                "started_at": job.started_at,
                "completed_at": job.completed_at,
            }
            for job in (GhJob.from_api_dict(j) for j in data.get("jobs", []))
        ]
        runner_names = [str(job["runner_name"]) for job in jobs if job.get("runner_name")]
        enriched["jobs"] = jobs
        enriched["runner_names"] = runner_names
        if runner_names:
            enriched["runner_name"] = runner_names[0]
            enriched["machine_name"] = infer_machine_from_runner_name(runner_names[0])
    except Exception as e:  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        enriched["jobs"] = []
    return enriched


async def _scheduled_workflows_impl(
    *,
    include_archived: bool = False,
    repo_limit: int = 100,
) -> dict:
    """Collect the read-only scheduled workflow inventory.

    A walk slower than ``SCHEDULED_WORKFLOWS_TIMEOUT`` answers ``degraded`` but
    keeps running, so the next request is served from cache (stale up to an hour).
    """
    cache_key = f"scheduled-workflows:{include_archived}:{repo_limit}"
    raw_timeout = os.environ.get("SCHEDULED_WORKFLOWS_TIMEOUT", "20")
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError):
        timeout = 20.0

    async def collect() -> dict:
        report = await scheduled_workflow_inventory.collect_inventory(
            ORG,
            gh_api,
            gh_api_raw,
            repo_limit=repo_limit,
            include_archived=include_archived,
        )
        payload = report.to_dict()
        payload["status"] = "ok"
        return payload

    def degraded() -> dict:
        return {
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
            "error": "Scheduled workflow inventory is still being collected; retry shortly.",
        }

    return await cache_get_swr(
        cache_key, collect, fresh_ttl=300.0, stale_ttl=3600.0, wait_timeout=timeout, on_timeout=degraded
    )


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.get("/api/runs", dependencies=[Depends(require_fleet_peer)])
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
    top_runs = [_slim_run(run) for run in all_runs[:per_page]]

    result = {"workflow_runs": top_runs, "total_count": len(top_runs)}
    cache_set(f"runs:{per_page}", result)
    return result


@router.get("/api/runs/enriched", dependencies=[Depends(require_fleet_peer)])
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
    slim = [_slim_run(run) for run in enriched]
    result = {"workflow_runs": slim, "total_count": len(slim)}
    cache_set(cache_key, result)
    return result


@router.get("/api/analysis/workflow-machines", dependencies=[Depends(require_fleet_peer)])
async def get_workflow_machine_analysis(request: Request, per_page: int = 100) -> dict:
    """Summarize recent workflow outcomes by machine and workflow."""
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    bounded_per_page = max(10, min(per_page, 200))
    data = await get_enriched_runs(request, per_page=bounded_per_page)
    runs = data.get("workflow_runs", [])
    return summarize_runs_by_workflow_and_machine(runs)


@router.get("/api/runs/{repo}", dependencies=[Depends(require_fleet_peer)])
async def get_repo_runs(request: Request, repo: str, per_page: int = 20):
    """Get recent workflow runs for a specific repo."""
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    repo = validate_repo_slug(repo)
    data = await gh_api(f"/repos/{ORG}/{repo}/actions/runs?per_page={per_page}")
    return data


@router.get("/api/scheduled-workflows", dependencies=[Depends(require_fleet_peer)])
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


_WORKFLOWS_LIST_KEY = "workflows_list"
_WORKFLOWS_LIST_FRESH_S = 120.0
_WORKFLOWS_LIST_STALE_S = 3600.0
_WORKFLOWS_LIST_WAIT_S = 20.0
_WORKFLOWS_LIST_REPOS = 20
_RECENT_RUNS_PER_WORKFLOW = 3
# Workflow-file triggers keyed by git blob sha: a file's content never changes
# under a sha, so each file is read once per edit instead of once per refresh.
_WORKFLOW_TRIGGER_CACHE: dict[str, list[str]] = {}


def _triggers_from_workflow_yaml(content: str) -> list[str]:
    """Return the trigger capabilities named in a workflow file."""
    triggers: list[str] = []
    if "workflow_dispatch" in content:
        triggers.append("manual")
    if "schedule" in content:
        triggers.append("schedule")
    if "push" in content or "pull_request" in content:
        triggers.append("push_pr")
    if "workflow_run" in content:
        triggers.append("workflow_run")
    return triggers


async def _workflow_triggers_by_path(repo_name: str) -> dict[str, list[str]]:
    """Map each ``.github/workflows`` path to its triggers, reading new blobs only."""
    try:
        listing = await gh_api(f"/repos/{ORG}/{repo_name}/contents/.github/workflows")
    except HTTPException:
        return {}
    triggers: dict[str, list[str]] = {}
    for entry in listing if isinstance(listing, list) else []:
        sha, path = entry.get("sha"), entry.get("path")
        if not sha or not path:
            continue
        if sha not in _WORKFLOW_TRIGGER_CACHE:
            try:
                blob = await gh_api(f"/repos/{ORG}/{repo_name}/git/blobs/{sha}")
                content = base64.b64decode(blob.get("content", "")).decode("utf-8", errors="replace")
            except (HTTPException, ValueError):
                continue
            _WORKFLOW_TRIGGER_CACHE[sha] = _triggers_from_workflow_yaml(content)
        triggers[path] = _WORKFLOW_TRIGGER_CACHE[sha]
    return triggers


def _run_summary(run: dict) -> dict:
    return {
        "id": run.get("id"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "created_at": run.get("created_at"),
        "html_url": run.get("html_url"),
    }


async def _repo_workflows(repo_name: str) -> list[dict]:
    """Workflows of one repo in a fixed number of GitHub calls (plus new blobs)."""
    try:
        workflows_data, runs_data = await asyncio.gather(
            gh_api(f"/repos/{ORG}/{repo_name}/actions/workflows?per_page=100"),
            gh_api(f"/repos/{ORG}/{repo_name}/actions/runs?per_page=100"),
        )
    except HTTPException:
        return []
    triggers_by_path = await _workflow_triggers_by_path(repo_name)

    runs_by_workflow: dict[Any, list[dict]] = {}
    for run in sorted(runs_data.get("workflow_runs", []), key=lambda r: r.get("created_at") or "", reverse=True):
        runs_by_workflow.setdefault(run.get("workflow_id"), []).append(run)

    result = []
    for wf in workflows_data.get("workflows", []):
        wf_runs = runs_by_workflow.get(wf.get("id"), [])[:_RECENT_RUNS_PER_WORKFLOW]
        latest_run = None
        if wf_runs:
            latest_run = {**_run_summary(wf_runs[0]), "head_branch": wf_runs[0].get("head_branch")}
        result.append(
            {
                "id": wf.get("id"),
                "name": wf.get("name", ""),
                "path": wf.get("path", ""),
                "state": wf.get("state", ""),
                "html_url": wf.get("html_url", ""),
                "triggers": triggers_by_path.get(wf.get("path", ""), []),
                "latest_run": latest_run,
                "recent_runs": [_run_summary(r) for r in wf_runs],
                "repository": repo_name,
            }
        )
    return result


async def _compute_workflows_list() -> dict:
    """Build the workflow catalogue for the most recently active repositories."""
    repos = await _get_recent_org_repos(limit=30)
    results = await asyncio.gather(*[_repo_workflows(r["name"]) for r in repos[:_WORKFLOWS_LIST_REPOS]])
    all_workflows = [wf for wf_list in results for wf in wf_list]
    return {"workflows": all_workflows, "total": len(all_workflows)}


@router.get("/api/workflows/list")
async def list_workflows() -> dict:
    """List all workflows per repository with trigger capabilities and latest run.

    Serves a catalogue up to an hour old while one background refresh runs; a
    cold cache answers ``status: "warming"`` after ``_WORKFLOWS_LIST_WAIT_S``
    instead of holding the browser connection for minutes.
    """
    return await cache_get_swr(
        _WORKFLOWS_LIST_KEY,
        _compute_workflows_list,
        fresh_ttl=_WORKFLOWS_LIST_FRESH_S,
        stale_ttl=_WORKFLOWS_LIST_STALE_S,
        wait_timeout=_WORKFLOWS_LIST_WAIT_S,
        on_timeout=lambda: {"workflows": [], "total": 0, "status": "warming"},
    )


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
