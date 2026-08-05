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
import logging
import os
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from cache_utils import cache_delete, cache_get, cache_set
from dashboard_config import ORG
from error_models import bad_gateway, validation_error
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from gh_utils import gh_api
from identity import Principal, require_fleet_peer, require_scope
from models.github_payloads import GhWorkflowRun
from proxy_utils import proxy_to_hub, should_proxy_fleet_to_hub
from queue_cleanup import DEFAULT_MIN_AGE_MINUTES, find_stale_runs, purge_stale_runs
from run_timing import annotate_runs_with_timing
from security import validate_repo_slug
from system_utils import run_cmd

log = logging.getLogger("dashboard.queue")
router = APIRouter(tags=["queue"])
_gh_api = gh_api


def _reason_counts(runs: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for run in runs:
        reason = str(run.get("reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _filter_stale_runs(
    runs: list[dict],
    *,
    repo: str | None = None,
    workflow: str | None = None,
    reason: str | None = None,
    safe_only: bool = False,
    max_count: int | None = None,
) -> list[dict]:
    filtered = runs
    if repo:
        repo = validate_repo_slug(repo)
        filtered = [run for run in filtered if run.get("repo") == repo]
    if workflow:
        filtered = [run for run in filtered if run.get("workflow") == workflow]
    if reason:
        filtered = [run for run in filtered if run.get("reason") == reason]
    if safe_only:
        filtered = [run for run in filtered if run.get("safe_to_cancel") is True]
    if max_count is not None and max_count > 0:
        filtered = filtered[:max_count]
    return filtered


async def _json_body_or_empty(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _empty_queue_result() -> dict:
    """Return the standard empty queue payload."""
    now = datetime.now(UTC).isoformat()
    return {
        "queued": [],
        "in_progress": [],
        "total": 0,
        "queued_count": 0,
        "queued_jobs_count": 0,
        "in_progress_count": 0,
        "generated_at": now,
        "served_at": now,
        "data_source": "unavailable",
        "stats": {
            "repos_sampled": 0,
            "repos_succeeded": 0,
            "repos_failed": 0,
            "failed_repositories": [],
            "job_detail_failures": 0,
            "budget_exhausted": False,
            "complete": False,
        },
    }


async def _get_recent_org_repos(limit: int = 30) -> list[dict]:
    """Fetch recently updated organization repositories."""
    data = await _gh_api(f"/orgs/{ORG}/repos?per_page={limit}&sort=updated&direction=desc")
    return data if isinstance(data, list) else data.get("items", [])


async def _fetch_repo_runs(
    repo_name: str,
    *,
    per_page: int = 100,
    status: str | None = None,
) -> list[dict]:
    """Fetch workflow runs for one repository and annotate repository name.

    GitHub API failures raise instead of returning [].
    Callers that aggregate across many repos must use return_exceptions=True
    so one repo's transient failure cannot silently zero its contribution to
    the org-wide queue total — that was the root cause of the dashboard
    "queue drops to zero" flicker (see Runner_Dashboard#641).
    """
    repo_name = validate_repo_slug(repo_name)
    status_part = f"&status={status}" if status else ""
    data = await _gh_api(f"/repos/{ORG}/{repo_name}/actions/runs?per_page={per_page}{status_part}")
    runs = data.get("workflow_runs", [])
    for run in runs:
        if "repository" not in run or not run["repository"]:
            run["repository"] = {"name": repo_name}
    return runs


async def _count_queued_jobs_for_run(run: dict) -> int:
    """Return the number of ``queued`` jobs inside a single workflow run.

    GitHub Actions queues at the JOB level, not the run level: a multi-job run
    flips its run-level status to ``in_progress`` the moment its first job
    starts, while sibling jobs stay ``queued``. Counting only run-level
    ``status == "queued"`` therefore undercounts true queue depth (those
    queued sibling jobs become invisible). This helper fetches the run's jobs
    and counts the ones still waiting for a runner.

    On any failure (missing repo/id, gh api error, parse error) it raises so
    the caller can fall back to the run-level assumption rather than silently
    contributing 0 — mirroring the partial-failure handling in ``_queue_impl``.
    """
    repo_name = (run.get("repository") or {}).get("name")
    run_id = run.get("id")
    if not repo_name or run_id is None:
        raise RuntimeError(f"run missing repo/id for job-level count: {run_id!r}")
    repo_name = validate_repo_slug(repo_name)
    cache_key = f"queue:jobs:{repo_name}:{run_id}"
    cached = cache_get(cache_key, _QUEUE_JOB_CACHE_TTL)
    if cached is not None:
        return int(cached)
    data = await _gh_api(f"/repos/{ORG}/{repo_name}/actions/runs/{run_id}/jobs?per_page=100")
    jobs = data.get("jobs", [])
    count = sum(1 for job in jobs if job.get("status") == "queued")
    cache_set(cache_key, count)
    return count


async def _count_queued_jobs(runs: list[dict], timeout: float) -> tuple[int, int, bool]:
    """Aggregate job-level queued depth across active runs.

    Fetches each run's jobs concurrently with ``return_exceptions=True`` so one
    run's transient failure cannot zero the total. For any run whose job fetch
    fails, we fall back to counting it as a single queued job if its run-level
    status is ``queued`` (preserving the legacy lower bound), otherwise 0.
    """
    if not runs:
        return 0, 0, False
    semaphore = asyncio.Semaphore(_QUEUE_JOB_CONCURRENCY)

    async def bounded_count(run: dict) -> int:
        async with semaphore:
            return await _count_queued_jobs_for_run(run)

    tasks = [asyncio.create_task(bounded_count(run)) for run in runs]
    _, pending = await asyncio.wait(tasks, timeout=max(0.0, timeout))
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    total = 0
    failures = 0
    for run, task in zip(runs, tasks, strict=True):
        if task.cancelled():
            failures += 1
            total += 1 if run.get("status") == "queued" else 0
            continue
        try:
            total += task.result()
        except Exception as exc:
            failures += 1
            log.warning("queued-jobs count failed for run %s: %r", run.get("id"), exc)
            total += 1 if run.get("status") == "queued" else 0
    return total, failures, bool(pending)


def _served_payload(payload: dict[Any, Any], source: str | None = None) -> dict[Any, Any]:
    """Return a response copy with request-time freshness metadata."""
    result = dict(payload)
    result["served_at"] = datetime.now(UTC).isoformat()
    if source is not None:
        result["data_source"] = source
    return result


# Cache TTL kept at 60s (down from 120s) so partial failures heal faster.
# `queue:stale` is a parallel key written whenever we have any fresh result;
# it is read with an effectively infinite TTL when every upstream fetch fails,
# so the dashboard never has to render an empty queue just because one batch
# of `gh api` calls timed out (the symptom reported in Runner_Dashboard#641).
_QUEUE_CACHE_TTL = 60.0
_QUEUE_CACHE_KEY = "queue"
_QUEUE_STALE_KEY = "queue:stale"
_QUEUE_REPO_LIMIT = 30  # was 15 — repos beyond this silently contributed 0
_QUEUE_REFRESH_BUDGET_SECONDS = float(os.environ.get("DASHBOARD_QUEUE_REFRESH_BUDGET_SECONDS", "8"))
_QUEUE_REPO_CONCURRENCY = int(os.environ.get("DASHBOARD_QUEUE_REPO_CONCURRENCY", "6"))
_QUEUE_JOB_CONCURRENCY = int(os.environ.get("DASHBOARD_QUEUE_JOB_CONCURRENCY", "6"))
_QUEUE_JOB_CACHE_TTL = float(os.environ.get("DASHBOARD_QUEUE_JOB_CACHE_TTL", "120"))
assert _QUEUE_REFRESH_BUDGET_SECONDS > 0
assert _QUEUE_REPO_CONCURRENCY > 0
assert _QUEUE_JOB_CONCURRENCY > 0
assert _QUEUE_JOB_CACHE_TTL > 0
# Years, in seconds. cache_utils proactively deletes entries past TTL, so we
# need a value larger than the process's expected uptime, not literally inf.
_QUEUE_STALE_TTL = 60.0 * 60.0 * 24.0 * 365.0


async def _collect_repo_runs(sample: list[dict], timeout: float) -> tuple[list[dict], list[str], bool]:
    """Fetch active runs with bounded concurrency and a shared deadline."""
    semaphore = asyncio.Semaphore(_QUEUE_REPO_CONCURRENCY)

    async def fetch(repo_name: str) -> list[dict]:
        async with semaphore:
            runs: list[dict] = []
            for status in ("queued", "in_progress"):
                runs.extend(await _fetch_repo_runs(repo_name, status=status))
            return runs

    tasks = [asyncio.create_task(fetch(repo["name"])) for repo in sample]
    _, pending = await asyncio.wait(tasks, timeout=max(0.0, timeout))
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    all_runs: list[dict] = []
    failures: list[str] = []
    for repo, task in zip(sample, tasks, strict=True):
        if task.cancelled():
            failures.append(repo["name"])
            continue
        try:
            all_runs.extend(task.result())
        except Exception as exc:
            failures.append(repo["name"])
            log.warning("queue aggregation failed for %s: %r", repo["name"], exc)
    return all_runs, failures, bool(pending)


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
        return _served_payload(cast(dict[Any, Any], cached), "cache")

    loop = asyncio.get_running_loop()
    deadline = loop.time() + _QUEUE_REFRESH_BUDGET_SECONDS
    try:
        repos = await asyncio.wait_for(
            _get_recent_org_repos(limit=_QUEUE_REPO_LIMIT),
            timeout=max(0.0, deadline - loop.time()),
        )
    except Exception as exc:
        log.warning("queue repository inventory failed within refresh budget: %r", exc)
        stale = cache_get(_QUEUE_STALE_KEY, _QUEUE_STALE_TTL)
        if stale is not None:
            return _served_payload(cast(dict[Any, Any], stale), "stale")
        unavailable = _empty_queue_result()
        unavailable["stats"]["budget_exhausted"] = isinstance(exc, TimeoutError)
        return unavailable
    if not repos:
        # No repos visible at all; try the stale cache before giving up.
        stale = cache_get(_QUEUE_STALE_KEY, _QUEUE_STALE_TTL)
        return _served_payload(cast(dict[Any, Any], stale), "stale") if stale is not None else _empty_queue_result()

    sample = repos[:_QUEUE_REPO_LIMIT]
    all_runs, failed_repositories, repo_budget_exhausted = await _collect_repo_runs(
        sample,
        deadline - loop.time(),
    )

    if failed_repositories:
        log.warning(
            "queue aggregation: %d/%d repos failed or exceeded budget: %s",
            len(failed_repositories),
            len(sample),
            "; ".join(failed_repositories[:5]),
        )

    # If every repo failed, prefer last-known-good over empty.
    if len(failed_repositories) == len(sample):
        stale = cache_get(_QUEUE_STALE_KEY, _QUEUE_STALE_TTL)
        if stale is not None:
            log.warning("queue aggregation: all repos failed; serving stale cache")
            return _served_payload(cast(dict[Any, Any], stale), "stale")
        unavailable = _empty_queue_result()
        unavailable["stats"].update(
            {
                "repos_sampled": len(sample),
                "repos_failed": len(failed_repositories),
                "failed_repositories": [repo["name"] for repo in sample],
                "budget_exhausted": repo_budget_exhausted,
            }
        )
        return unavailable

    queued = sorted(
        [r for r in all_runs if r.get("status") == "queued"],
        key=lambda r: r.get("created_at", ""),
    )
    in_progress = sorted(
        [r for r in all_runs if r.get("status") == "in_progress"],
        key=lambda r: r.get("run_started_at") or r.get("created_at", ""),
    )

    # Job-level queue depth: count `queued` jobs across BOTH queued runs and
    # in_progress runs (the latter can still have queued sibling jobs that the
    # run-level `queued_count` misses). This is the figure the operator cares
    # about — how many jobs are actually waiting for a runner.
    queued_jobs_count, job_detail_failures, job_budget_exhausted = await _count_queued_jobs(
        queued + in_progress,
        deadline - loop.time(),
    )
    generated_at = datetime.now(UTC).isoformat()
    budget_exhausted = repo_budget_exhausted or job_budget_exhausted
    complete = not failed_repositories and job_detail_failures == 0

    payload: dict[Any, Any] = {
        "queued": queued,
        "in_progress": in_progress,
        "total": len(queued) + len(in_progress),
        "queued_count": len(queued),
        "queued_jobs_count": queued_jobs_count,
        "in_progress_count": len(in_progress),
        "generated_at": generated_at,
        "served_at": generated_at,
        "data_source": "live" if complete else "partial",
        "stats": {
            "repos_sampled": len(sample),
            "repos_succeeded": len(sample) - len(failed_repositories),
            "repos_failed": len(failed_repositories),
            "failed_repositories": failed_repositories,
            "job_detail_failures": job_detail_failures,
            "budget_exhausted": budget_exhausted,
            "complete": complete,
        },
    }
    cache_set(_QUEUE_CACHE_KEY, payload)
    if complete:
        cache_set(_QUEUE_STALE_KEY, payload)
    return payload


# ─── Queue Routes ─────────────────────────────────────────────────────────────


@router.get("/api/queue", dependencies=[Depends(require_fleet_peer)])
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


@router.get("/api/queue/status", dependencies=[Depends(require_fleet_peer)])
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


@router.get("/api/queue/stale", dependencies=[Depends(require_fleet_peer)])
async def get_stale_queue_runs(
    request: Request,
    min_age_minutes: Annotated[int, Query(ge=1, le=60 * 24 * 14)] = DEFAULT_MIN_AGE_MINUTES,
    repo: str | None = None,
    workflow: str | None = None,
    reason: str | None = None,
    safe_only: bool = False,
    max_count: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> dict:
    """List queued workflow runs older than ``min_age_minutes`` across the org."""
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    # Validate repo slug if provided
    validated_repo = repo
    if repo:
        try:
            validated_repo = validate_repo_slug(repo)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        runs = await find_stale_runs(
            org=ORG,
            min_age_minutes=min_age_minutes,
            repo=validated_repo,
            reason=reason,
        )
    except Exception as exc:
        log.exception("Failed to fetch stale runs")
        raise HTTPException(
            status_code=502,
            detail=bad_gateway(f"Failed to fetch stale runs: {exc}").model_dump(exclude_none=True),
        ) from exc

    run_dicts = [run.as_dict() for run in runs]
    filtered_runs = _filter_stale_runs(
        run_dicts,
        repo=validated_repo,
        workflow=workflow,
        reason=reason,
        safe_only=safe_only,
        max_count=max_count,
    )
    return {
        "org": ORG,
        "min_age_minutes": min_age_minutes,
        "stale_count": len(runs),
        "superseded_count": sum(1 for run in runs if run.pr_head_superseded),
        "safe_count": sum(1 for run in run_dicts if run.get("safe_to_cancel") is True),
        "reason_counts": _reason_counts(run_dicts),
        "filtered_count": len(filtered_runs),
        "runs": filtered_runs,
    }


@router.post("/api/queue/purge-stale", dependencies=[Depends(require_fleet_peer)])
async def purge_stale_queue_runs(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("workflows.control")),  # noqa: B008
    min_age_minutes: Annotated[int, Query(ge=1, le=60 * 24 * 14)] = DEFAULT_MIN_AGE_MINUTES,
    dry_run: bool = True,
    superseded_only: bool = True,
    repo: str | None = None,
    workflow: str | None = None,
    reason: str | None = None,
    safe_only: bool = True,
    max_count: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> dict:
    """Cancel stale queued workflow runs.

    By default this mutating endpoint only purges stale PR runs whose PR head
    has advanced beyond the queued run's head SHA.
    """
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    body = await _json_body_or_empty(request)
    min_age_minutes = int(body.get("min_age_minutes", body.get("min_age", min_age_minutes)))

    # Safely decode dry_run
    body_dry_run = body.get("dry_run")
    if body_dry_run is not None:
        dry_run = body_dry_run if isinstance(body_dry_run, bool) else (str(body_dry_run).lower() != "false")
    else:
        dry_run = dry_run

    repo = body.get("repo", repo) or None
    workflow = body.get("workflow", body.get("workflow_name", workflow)) or None
    reason = body.get("reason", reason) or None

    body_safe_only = body.get("safe_only")
    if body_safe_only is not None:
        if isinstance(body_safe_only, bool):
            safe_only = body_safe_only
        else:
            safe_only = str(body_safe_only).lower() != "false"
    else:
        safe_only = safe_only

    max_count = body.get("max_count", body.get("max_cancel", max_count))
    max_count = int(max_count) if max_count else None

    body_superseded_only = body.get("superseded_only")
    if body_superseded_only is not None:
        if isinstance(body_superseded_only, bool):
            superseded_only = body_superseded_only
        else:
            superseded_only = str(body_superseded_only).lower() != "false"
    else:
        superseded_only = superseded_only

    if safe_only:
        superseded_only = True

    # Validate repo slug if provided
    validated_repo = repo
    if repo:
        try:
            validated_repo = validate_repo_slug(repo)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        result = await purge_stale_runs(
            ORG,
            min_age_minutes=min_age_minutes,
            dry_run=dry_run,
            superseded_only=superseded_only,
            repo=validated_repo,
            workflow=workflow,
            reason=reason,
            safe_only=safe_only,
            max_count=max_count,
        )
    except Exception as exc:
        log.exception("Failed to purge stale runs")
        raise HTTPException(
            status_code=502,
            detail=bad_gateway(f"Failed to purge stale runs: {exc}").model_dump(exclude_none=True),
        ) from exc

    result["reason_counts"] = _reason_counts(list(result.get("runs", [])))
    if not dry_run and result.get("cancelled_count", 0):
        cache_delete("queue")
        cache_delete("queue:stale")
        cache_delete("diagnose")
    return result


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
