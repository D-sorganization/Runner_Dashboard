"""Queue diagnostic routes for analyzing workflow run delays."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from cache_utils import cache_get, cache_set
from dashboard_config import ORG
from fastapi import APIRouter, HTTPException
from gh_utils import gh_api_admin
from queue_cleanup import is_routable
from runner_inventory import fetch_org_runners

from .queue import _queue_impl

log = logging.getLogger("dashboard.queue_diagnostics")
router = APIRouter()

_DIAGNOSE_CACHE_KEY = "diagnose"
_DIAGNOSE_CACHE_TTL = 60.0
_MAX_RUNS_TO_SAMPLE = 20
_MAX_JOBS_TO_RETURN = 50
_FLEET_LABEL_PREFIX = "d-sorg-fleet"


def _runner_labels(runner: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for label in runner.get("labels", []) or []:
        if isinstance(label, dict):
            name = label.get("name")
        else:
            name = str(label)
        if name:
            labels.append(str(name))
    return labels


def _repo_name(run: dict[str, Any]) -> str:
    repo = run.get("repository") or {}
    if isinstance(repo, dict) and repo.get("name"):
        return str(repo["name"])
    if run.get("repo"):
        return str(run["repo"])
    return ""


def _runner_pool_summary(runners: list[dict[str, Any]]) -> dict[str, int]:
    total = len(runners)
    online = sum(1 for runner in runners if runner.get("status") == "online")
    busy = sum(1 for runner in runners if runner.get("status") == "online" and bool(runner.get("busy")))
    offline = total - online
    return {
        "total": total,
        "online": online,
        "busy": busy,
        "idle": max(0, online - busy),
        "offline": offline,
    }


def _target_for_labels(labels: list[str]) -> str:
    lowered = {label.lower() for label in labels}
    has_self_hosted = "self-hosted" in lowered
    has_fleet = any(label == _FLEET_LABEL_PREFIX or label.startswith(f"{_FLEET_LABEL_PREFIX}-") for label in labels)
    if has_self_hosted and has_fleet:
        return "self-hosted (d-sorg-fleet)"
    if has_self_hosted:
        return "self-hosted (generic)"
    return "github-hosted"


def _runner_group_key(job: dict[str, Any]) -> str | None:
    group = job.get("runner_group_name")
    if group:
        return str(group)
    labels = [label for label in job.get("labels", []) or [] if label]
    fleet_labels = [
        label for label in labels if label == _FLEET_LABEL_PREFIX or label.startswith(f"{_FLEET_LABEL_PREFIX}-")
    ]
    if fleet_labels:
        return fleet_labels[0]
    if "self-hosted" in {label.lower() for label in labels}:
        return "self-hosted"
    return None


def _job_sample(
    *,
    run: dict[str, Any],
    job: dict[str, Any],
) -> dict[str, Any]:
    labels = [str(label) for label in job.get("labels", []) or [] if label]
    return {
        "repo": _repo_name(run),
        "run_id": run.get("id"),
        "workflow": run.get("name") or run.get("display_title") or "",
        "job": job.get("name") or f"run {run.get('id')}",
        "target": _target_for_labels(labels),
        "labels": labels,
        "runner_group_name": job.get("runner_group_name"),
        "html_url": job.get("html_url") or run.get("html_url") or "",
    }


async def _queued_jobs_for_run(run: dict[str, Any]) -> list[dict[str, Any]]:
    repo = _repo_name(run)
    run_id = run.get("id")
    if not repo or run_id is None:
        return []
    data = await gh_api_admin(f"/repos/{ORG}/{repo}/actions/runs/{run_id}/jobs?per_page=100")
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    return [job for job in jobs if isinstance(job, dict) and job.get("status") == "queued"]


async def _sample_queued_jobs(runs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    if not runs:
        return [], []
    sampled_runs = sorted(runs, key=lambda run: run.get("created_at", ""))[:_MAX_RUNS_TO_SAMPLE]
    results = await asyncio.gather(
        *[_queued_jobs_for_run(run) for run in sampled_runs],
        return_exceptions=True,
    )

    jobs: list[dict[str, Any]] = []
    errors: list[str] = []
    for run, result in zip(sampled_runs, results, strict=True):
        if isinstance(result, BaseException):
            errors.append(f"{_repo_name(run) or 'unknown'}#{run.get('id')}: {result}")
            if run.get("status") == "queued":
                jobs.append(
                    _job_sample(
                        run=run,
                        job={
                            "name": run.get("name") or run.get("display_title") or "queued workflow run",
                            "status": "queued",
                            "labels": [],
                        },
                    )
                )
            continue
        jobs.extend(_job_sample(run=run, job=job) for job in result)
    return jobs[:_MAX_JOBS_TO_RETURN], errors


def _label_breakdown(jobs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    for job in jobs:
        labels = [str(label) for label in job.get("labels", []) or [] if label]
        key = ", ".join(labels) if labels else "unknown"
        entry = counts.setdefault(key, {"count": 0, "target": job.get("target", "unknown"), "labels": labels})
        entry["count"] += 1
    return dict(sorted(counts.items(), key=lambda item: (-int(item[1]["count"]), item[0])))


def _misconfigured_jobs(jobs: list[dict[str, Any]], online_label_sets: list[frozenset[str]]) -> list[dict[str, Any]]:
    misconfigured: list[dict[str, Any]] = []
    all_online_labels = set().union(*online_label_sets) if online_label_sets else set()
    for job in jobs:
        labels = [str(label) for label in job.get("labels", []) or [] if label]
        target = job.get("target")
        if target == "github-hosted" or not labels:
            continue
        if is_routable(labels, online_label_sets):
            continue
        missing = sorted(label for label in labels if label not in all_online_labels)
        misconfigured.append(
            {
                "repo": job.get("repo"),
                "run_id": job.get("run_id"),
                "job": job.get("job"),
                "labels": labels,
                "missing_labels": missing,
                "reason": "no online runner has all requested labels",
            }
        )
    return misconfigured


def _runner_groups(jobs: list[dict[str, Any]], runners: list[dict[str, Any]]) -> list[dict[str, Any]]:
    waiting_by_group: dict[str, set[str]] = {}
    for job in jobs:
        key = _runner_group_key(job)
        if not key:
            continue
        waiting_by_group.setdefault(key, set()).add(str(job.get("repo") or "unknown"))

    if not waiting_by_group:
        return []

    runners_by_label: dict[str, list[str]] = {}
    for runner in runners:
        runner_name = str(runner.get("name") or "")
        for label in _runner_labels(runner):
            runners_by_label.setdefault(label, []).append(runner_name)

    groups: list[dict[str, Any]] = []
    for name in sorted(waiting_by_group):
        runner_names = sorted(set(runners_by_label.get(name, [])))
        groups.append(
            {
                "name": name,
                "visibility": "unknown",
                "restricted": False,
                "inherited": False,
                "runner_count": len(runner_names),
                "runner_names": runner_names[:8],
                "blocked_waiting_repos": [],
                "waiting_repos": sorted(waiting_by_group[name]),
            }
        )
    return groups


def _bottleneck(
    *,
    queued_jobs: int,
    runner_pool: dict[str, int],
    waiting_for_self_hosted: int,
    waiting_for_generic_self_hosted: int,
    waiting_for_github_hosted: int,
    misconfig_count: int,
    job_errors: list[str],
    runner_error: str | None,
) -> str:
    if queued_jobs == 0:
        return "No queued jobs found"
    if misconfig_count:
        return f"{misconfig_count} queued job(s) request labels no online runner can satisfy"
    if waiting_for_self_hosted and runner_pool.get("idle", 0) == 0:
        return "Self-hosted jobs are waiting and all online runners are busy"
    if waiting_for_generic_self_hosted and runner_pool.get("total", 0) > 0:
        return "Generic self-hosted jobs are waiting; prefer explicit d-sorg fleet labels"
    if waiting_for_github_hosted and not waiting_for_self_hosted:
        return "Queued jobs are waiting for GitHub-hosted runners"
    if runner_error:
        return "Queue sampled, but runner inventory is degraded"
    if job_errors:
        return "Queue sampled with partial job-detail failures"
    return "Queued jobs appear routable with current runner capacity"


@router.get("/api/queue/diagnose")
async def diagnose_queue() -> dict[str, Any]:
    """Explain why queued jobs are waiting using live queue and runner data."""
    cached = cache_get(_DIAGNOSE_CACHE_KEY, _DIAGNOSE_CACHE_TTL)
    if cached is not None:
        return cached

    runner_error: str | None = None
    try:
        queue_data = await _queue_impl()
    except Exception as exc:
        log.exception("queue diagnosis failed")
        raise HTTPException(status_code=502, detail=f"Queue diagnosis failed: {exc}") from exc

    try:
        runner_data = await fetch_org_runners(gh_api_admin, ORG)
    except Exception as exc:  # noqa: BLE001
        runner_error = str(exc)
        runner_data = {"total_count": 0, "runners": []}
        log.warning("queue diagnosis runner inventory failed: %s", exc)

    runners = runner_data.get("runners", []) if isinstance(runner_data, dict) else []
    if not isinstance(runners, list):
        runner_error = "runner inventory response did not contain a runner list"
        runners = []

    runner_pool = _runner_pool_summary([runner for runner in runners if isinstance(runner, dict)])
    active_runs = list(queue_data.get("queued", []) or []) + list(queue_data.get("in_progress", []) or [])
    queued_runs = [run for run in queue_data.get("queued", []) or [] if isinstance(run, dict)]
    sampled_jobs, job_errors = await _sample_queued_jobs([run for run in active_runs if isinstance(run, dict)])

    target_counts = Counter(str(job.get("target") or "unknown") for job in sampled_jobs)
    online_label_sets = [
        frozenset(_runner_labels(runner))
        for runner in runners
        if isinstance(runner, dict) and runner.get("status") == "online"
    ]
    misconfigured = _misconfigured_jobs(sampled_jobs, online_label_sets)
    waiting_for_fleet = target_counts["self-hosted (d-sorg-fleet)"]
    waiting_for_generic_self_hosted = target_counts["self-hosted (generic)"]
    waiting_for_github_hosted = target_counts["github-hosted"]
    waiting_for_self_hosted = waiting_for_fleet + waiting_for_generic_self_hosted

    queued_jobs_count = queue_data.get("queued_jobs_count", len(sampled_jobs))
    try:
        queued_jobs_for_bottleneck = max(len(sampled_jobs), int(queued_jobs_count))
    except (TypeError, ValueError):
        queued_jobs_for_bottleneck = len(sampled_jobs)

    result = {
        "runner_pool": runner_pool,
        "queued_runs_found": len(queued_runs),
        "queued_jobs_count": queued_jobs_count,
        "jobs_sampled": len(sampled_jobs),
        "waiting_for_fleet": waiting_for_fleet,
        "waiting_for_generic_self_hosted": waiting_for_generic_self_hosted,
        "waiting_for_self_hosted": waiting_for_self_hosted,
        "waiting_for_github_hosted": waiting_for_github_hosted,
        "runner_groups": _runner_groups(sampled_jobs, runners),
        "runner_groups_restricted": False,
        "pick_runner_misconfig": misconfigured,
        "label_breakdown": _label_breakdown(sampled_jobs),
        "bottleneck": _bottleneck(
            queued_jobs=queued_jobs_for_bottleneck,
            runner_pool=runner_pool,
            waiting_for_self_hosted=waiting_for_self_hosted,
            waiting_for_generic_self_hosted=waiting_for_generic_self_hosted,
            waiting_for_github_hosted=waiting_for_github_hosted,
            misconfig_count=len(misconfigured),
            job_errors=job_errors,
            runner_error=runner_error,
        ),
        "sampled_jobs": sampled_jobs,
        "errors": {
            "runner_inventory": runner_error,
            "job_details": job_errors,
        },
        "generated_at": datetime.now(UTC).isoformat(),
    }
    cache_set(_DIAGNOSE_CACHE_KEY, result)
    return result
