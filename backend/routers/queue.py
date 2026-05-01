"""Queue management routes.

Covers:
  - GET  /api/queue              – queued and in-progress workflow runs (org-wide sample)
  - POST /api/runs/{repo}/cancel/{run_id}      – cancel single workflow run
  - POST /api/runs/{repo}/rerun/{run_id}       – re-run failed jobs in workflow
  - POST /api/queue/cancel-workflow             – cancel all queued runs of a workflow
  - GET  /api/queue/diagnose                    – explain why queued jobs are waiting
"""

from __future__ import annotations

import asyncio
import json
import logging

from cache_utils import cache_delete, cache_get, cache_set
from dashboard_config import ORG
from error_models import bad_gateway, validation_error
from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, require_scope
from proxy_utils import proxy_to_hub, should_proxy_fleet_to_hub
from security import validate_repo_slug
from system_utils import run_cmd

log = logging.getLogger("dashboard.queue")
router = APIRouter(tags=["queue"])


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _empty_queue_result() -> dict:
    """Return the standard empty queue payload."""
    return {
        "queued": [],
        "in_progress": [],
        "total": 0,
        "queued_count": 0,
        "in_progress_count": 0,
    }


async def _get_recent_org_repos(limit: int = 30) -> list[dict]:
    """Fetch recently updated organization repositories."""
    code, stdout, _ = await run_cmd(
        [
            "gh",
            "api",
            f"/orgs/{ORG}/repos?per_page={limit}&sort=updated&direction=desc",
        ],
        timeout=20,
    )
    if code != 0:
        return []
    try:
        return json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return []


async def _fetch_repo_runs(
    repo_name: str,
    *,
    per_page: int = 10,
    status: str | None = None,
) -> list[dict]:
    """Fetch workflow runs for one repository and annotate repository name."""
    repo_name = validate_repo_slug(repo_name)
    status_part = f"&status={status}" if status else ""
    rc, out, _ = await run_cmd(
        [
            "gh",
            "api",
            f"/repos/{ORG}/{repo_name}/actions/runs?per_page={per_page}{status_part}",
        ],
        timeout=15,
    )
    if rc != 0:
        return []
    try:
        runs = json.loads(out).get("workflow_runs", [])
    except (json.JSONDecodeError, ValueError):
        return []
    for run in runs:
        if "repository" not in run or not run["repository"]:
            run["repository"] = {"name": repo_name}
    return runs


async def _queue_impl() -> dict:
    """Core queue aggregation, callable from the HTTP endpoint and internally."""
    cached = cache_get("queue", 120.0)
    if cached is not None:
        return cached

    repos = await _get_recent_org_repos(limit=20)
    if not repos:
        return _empty_queue_result()

    async def fetch_active_runs(repo_name: str) -> list[dict]:
        results: list[dict] = []
        for status in ("queued", "in_progress"):
            results.extend(await _fetch_repo_runs(repo_name, per_page=10, status=status))
        return results

    sample = repos[:15]
    all_runs_nested = await asyncio.gather(*[fetch_active_runs(r["name"]) for r in sample])
    all_runs: list[dict] = [run for sublist in all_runs_nested for run in sublist]

    queued = sorted(
        [r for r in all_runs if r.get("status") == "queued"],
        key=lambda r: r.get("created_at", ""),
    )
    in_progress = sorted(
        [r for r in all_runs if r.get("status") == "in_progress"],
        key=lambda r: r.get("run_started_at") or r.get("created_at", ""),
    )

    result = {
        "queued": queued,
        "in_progress": in_progress,
        "total": len(queued) + len(in_progress),
        "queued_count": len(queued),
        "in_progress_count": len(in_progress),
    }
    cache_set("queue", result)
    return result


# ─── Queue Routes ─────────────────────────────────────────────────────────────


@router.get("/api/queue")
async def get_queue(request: Request) -> dict:
    """Get queued and in-progress workflow runs across the org.

    GitHub has no org-level queue endpoint; we query the 15 most recently
    updated repos concurrently for both statuses and aggregate the results.
    """
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    return await _queue_impl()


@router.post("/api/runs/{repo}/cancel/{run_id}")
async def cancel_run(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("workflows.control")),  # noqa: B008
    repo: str,
    run_id: int,  # noqa: B008
) -> dict:
    """Cancel a single queued or in-progress workflow run."""
    repo = validate_repo_slug(repo)
    code, _, stderr = await run_cmd(
        [
            "gh",
            "api",
            "-X",
            "POST",
            f"/repos/{ORG}/{repo}/actions/runs/{run_id}/cancel",
        ],
        timeout=15,
    )
    if code != 0:
        raise HTTPException(
            status_code=502,
            detail=bad_gateway(f"Cancel failed: {stderr}").model_dump(exclude_none=True),
        )
    # Invalidate stale queue/runs caches so the next poll reflects the cancel.
    cache_delete("queue")
    cache_delete("diagnose")
    return {"cancelled": True, "run_id": run_id, "repo": repo}


@router.post("/api/runs/{repo}/rerun/{run_id}")
async def rerun_failed(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("workflows.control")),  # noqa: B008
    repo: str,
    run_id: int,  # noqa: B008
) -> dict:
    """Re-run failed jobs in a workflow run."""
    repo = validate_repo_slug(repo)
    code, _, stderr = await run_cmd(
        [
            "gh",
            "api",
            "-X",
            "POST",
            f"/repos/{ORG}/{repo}/actions/runs/{run_id}/rerun-failed-jobs",
        ],
        timeout=15,
    )
    if code != 0:
        raise HTTPException(
            status_code=502,
            detail=bad_gateway(f"Rerun failed: {stderr}").model_dump(exclude_none=True),
        )
    cache_delete("queue")
    return {"rerun": True, "run_id": run_id, "repo": repo}


@router.post("/api/queue/cancel-workflow")
async def cancel_workflow_runs(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("workflows.control")),  # noqa: B008
) -> dict:
    """Cancel all queued runs of a specific workflow across the org.

    Body: {"workflow_name": "ci-standard", "repo": "MyRepo"}  (repo optional)
    Useful for deprioritising a noisy workflow to free runners for
    higher-priority work.
    """
    body = await request.json()
    workflow_name: str = body.get("workflow_name", "")
    target_repo: str | None = body.get("repo")
    if target_repo is not None:
        target_repo = validate_repo_slug(target_repo)

    if not workflow_name:
        raise HTTPException(
            status_code=422,
            detail=validation_error("workflow_name is required").model_dump(exclude_none=True),
        )

    # Fetch current queue
    queue_data = await _queue_impl()
    runs_to_cancel = [
        r
        for r in queue_data["queued"]
        if r.get("name") == workflow_name
        and (target_repo is None or (r.get("repository") or {}).get("name") == target_repo)  # noqa: E501
    ]

    cancelled: list[dict] = []
    errors: list[str] = []
    for run in runs_to_cancel:
        repo = (run.get("repository") or {}).get("name", "")
        run_id = run["id"]
        if not repo:
            continue
        code, _, stderr = await run_cmd(
            [
                "gh",
                "api",
                "-X",
                "POST",
                f"/repos/{ORG}/{repo}/actions/runs/{run_id}/cancel",
            ],
            timeout=15,
        )
        if code == 0:
            cancelled.append({"repo": repo, "run_id": run_id})
        else:
            errors.append(f"{repo}#{run_id}: {stderr.strip()}")

    if cancelled:
        cache_delete("queue")
        cache_delete("diagnose")

    return {
        "cancelled_count": len(cancelled),
        "cancelled": cancelled,
        "errors": errors,
    }
