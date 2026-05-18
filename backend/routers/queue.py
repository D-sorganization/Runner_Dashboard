"""Queue management routes.

Covers:
  - GET  /api/queue              – queued and in-progress workflow runs (org-wide sample)
  - GET  /api/queue/status       – same as /api/queue but with per-run timing breakdown
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
from models.github_payloads import GhWorkflowRun
from proxy_utils import proxy_to_hub, should_proxy_fleet_to_hub
from run_timing import annotate_runs_with_timing
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
    per_page: int = 100,
    status: str | None = None,
) -> list[dict]:
    """Fetch workflow runs for one repository and annotate repository name.

    Failures (non-zero exit, JSON parse error) raise instead of returning [].
    Callers that aggregate across many repos must use return_exceptions=True
    so one repo's transient failure cannot silently zero its contribution to
    the org-wide queue total — that was the root cause of the dashboard
    "queue drops to zero" flicker (see Runner_Dashboard#641).
    """
    repo_name = validate_repo_slug(repo_name)
    status_part = f"&status={status}" if status else ""
    rc, out, err = await run_cmd(
        [
            "gh",
            "api",
            f"/repos/{ORG}/{repo_name}/actions/runs?per_page={per_page}{status_part}",
        ],
        timeout=30,
    )
    if rc != 0:
        raise RuntimeError(f"gh api failed for {repo_name} (status={status}): rc={rc} {err[:200]!r}")
    runs = json.loads(out).get("workflow_runs", [])
    for run in runs:
        if "repository" not in run or not run["repository"]:
            run["repository"] = {"name": repo_name}
    return runs


# Cache TTL kept at 60s (down from 120s) so partial failures heal faster.
# `queue:stale` is a parallel key written whenever we have any fresh result;
# it is read with an effectively infinite TTL when every upstream fetch fails,
# so the dashboard never has to render an empty queue just because one batch
# of `gh api` calls timed out (the symptom reported in Runner_Dashboard#641).
_QUEUE_CACHE_TTL = 60.0
_QUEUE_CACHE_KEY = "queue"
_QUEUE_STALE_KEY = "queue:stale"
_QUEUE_REPO_LIMIT = 30  # was 15 — repos beyond this silently contributed 0
# Years, in seconds. cache_utils proactively deletes entries past TTL, so we
# need a value larger than the process's expected uptime, not literally inf.
_QUEUE_STALE_TTL = 60.0 * 60.0 * 24.0 * 365.0


async def _queue_impl() -> dict:
    """Core queue aggregation, callable from the HTTP endpoint and internally.

    Behavior:
    1. If the cache is fresh (<TTL), serve it.
    2. Otherwise fetch all `_QUEUE_REPO_LIMIT` repos concurrently with
       `return_exceptions=True`. Repos that succeed contribute their runs;
       repos that fail are logged at WARNING level but do NOT zero out the
       result. If ANY repo succeeded, we cache and return a fresh result.
    3. If every fetch failed, fall back to the last cached stale result
       rather than serving an empty queue — the dashboard rendering empty
       was the user-visible symptom we're fixing.
    """
    cached = cache_get(_QUEUE_CACHE_KEY, _QUEUE_CACHE_TTL)
    if cached is not None:
        return cached

    repos = await _get_recent_org_repos(limit=_QUEUE_REPO_LIMIT)
    if not repos:
        # No repos visible at all; try the stale cache before giving up.
        stale = cache_get(_QUEUE_STALE_KEY, _QUEUE_STALE_TTL)
        return stale if stale is not None else _empty_queue_result()

    async def fetch_active_runs(repo_name: str) -> list[dict]:
        results: list[dict] = []
        for status in ("queued", "in_progress"):
            results.extend(await _fetch_repo_runs(repo_name, status=status))
        return results

    sample = repos[:_QUEUE_REPO_LIMIT]
    fetched = await asyncio.gather(
        *[fetch_active_runs(r["name"]) for r in sample],
        return_exceptions=True,
    )

    all_runs: list[dict] = []
    failures: list[str] = []
    for repo, result in zip(sample, fetched, strict=True):
        if isinstance(result, BaseException):
            failures.append(f"{repo['name']}: {result!r}")
            continue
        all_runs.extend(result)

    if failures:
        log.warning(
            "queue aggregation: %d/%d repos failed: %s",
            len(failures),
            len(sample),
            "; ".join(failures[:5]),
        )

    # If every repo failed, prefer last-known-good over empty.
    if len(failures) == len(sample):
        stale = cache_get(_QUEUE_STALE_KEY, _QUEUE_STALE_TTL)
        if stale is not None:
            log.warning("queue aggregation: all repos failed; serving stale cache")
            return stale
        return _empty_queue_result()

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
        "stats": {
            "repos_sampled": len(sample),
            "repos_failed": len(failures),
        },
    }
    cache_set(_QUEUE_CACHE_KEY, result)
    cache_set(_QUEUE_STALE_KEY, result)
    return result


# ─── Queue Routes ─────────────────────────────────────────────────────────────


@router.get("/api/queue")
async def get_queue(request: Request) -> dict:
    """Get queued and in-progress workflow runs across the org.

    GitHub has no org-level queue endpoint; we query the top
    `_QUEUE_REPO_LIMIT` most-recently-updated repos concurrently for both
    statuses and aggregate. Partial failures are logged but do not zero out
    the result; if everything fails, we serve the last cached payload rather
    than empty (see Runner_Dashboard#641).
    """
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    return await _queue_impl()


@router.get("/api/queue/status")
async def get_queue_status(request: Request) -> dict:
    """Queue data with per-run queue-wait vs. execution-time breakdown.

    Identical to ``GET /api/queue`` but each run object includes a ``timing``
    sub-object::

        {
          "queue_wait_seconds": 45,   # seconds waiting for a runner
          "exec_seconds":       120,  # seconds the job code has been running
        }

    For queued runs (no runner yet), ``exec_seconds`` is 0 and
    ``queue_wait_seconds`` is the time since the run was created.

    This breakdown lets the dashboard display "Queue: 45s | Exec: 2m" without
    an extra GitHub API round-trip (the timestamps come from the run objects
    already fetched by ``/api/queue``).
    """
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    raw = await _queue_impl()
    return annotate_runs_with_timing(raw)


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

    # Fetch current queue — parse into typed view-models to avoid .get() chains
    queue_data = await _queue_impl()
    typed_runs = [GhWorkflowRun.model_validate(r) for r in queue_data.get("queued", [])]
    runs_to_cancel = [
        r for r in typed_runs if r.name == workflow_name and (target_repo is None or r.repository_name == target_repo)
    ]

    cancelled: list[dict] = []
    errors: list[str] = []
    for run in runs_to_cancel:
        repo = run.repository_name
        run_id = run.id
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
