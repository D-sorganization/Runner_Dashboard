"""Bounded, resumable collection of GitHub Actions job timing metrics."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import gh_client
from job_metrics import (
    completed_run_is_unchanged,
    incomplete_run_count,
    load_collection_state,
    positive_attempt,
    save_collection_state,
    save_job_batch,
)
from job_metrics_payloads import positive_identifier, validated_job_payload

PAGE_SIZE = 100
DEFAULT_MAX_REPOS = 40
DEFAULT_MAX_RUNS = 100
DEFAULT_MAX_PAGES = 40
_INITIAL_CURSOR = {
    "repo_page": 1,
    "repo_offset": 0,
    "run_page": 1,
    "run_offset": 0,
    "job_page": 1,
    "job_run_id": 0,
    "job_parent_attempt": 0,
    "job_run_updated_at": "",
    "skipped_record_reasons": [],
}


def _bounded_int(name: str, value: Any, limit: int) -> int:
    if type(value) is not int or not 1 <= value <= limit:
        raise ValueError(f"{name} must be between 1 and {limit}")
    return value


def _query(path: str, **params: int | str) -> str:
    return f"{path}?{urlencode(params)}"


def _items(payload: Any, key: str) -> list[dict[str, Any]] | None:
    values = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
        return None
    return values


def _next_repo(cursor: dict[str, int], repos: list[dict[str, Any]], repo_page_full: bool) -> bool:
    cursor["repo_offset"] += 1
    cursor["run_page"] = 1
    cursor["run_offset"] = 0
    if cursor["repo_offset"] >= len(repos) and repo_page_full:
        cursor["repo_page"] += 1
        cursor["repo_offset"] = 0
        return True
    return False


def _reset_job_cursor(cursor: dict[str, Any]) -> None:
    cursor["job_page"] = 1
    cursor["job_run_id"] = 0
    cursor["job_parent_attempt"] = 0
    cursor["job_run_updated_at"] = ""


def _advance_run(cursor: dict[str, Any], runs: list[dict[str, Any]], run_page_full: bool) -> bool:
    cursor["run_offset"] += 1
    _reset_job_cursor(cursor)
    if cursor["run_offset"] >= len(runs) and run_page_full:
        cursor["run_page"] += 1
        cursor["run_offset"] = 0
        return True
    return False


def _resume_job_page(cursor: dict[str, Any], run: dict[str, Any], run_id: int) -> int:
    """Resume only when the saved cursor still identifies this exact run state."""
    attempt = positive_attempt(run.get("run_attempt")) or 0
    updated_at = str(run.get("updated_at") or "")
    identity = (int(cursor["job_run_id"]), int(cursor["job_parent_attempt"]), str(cursor["job_run_updated_at"]))
    current = (run_id, attempt, updated_at)
    if identity != current:
        _reset_job_cursor(cursor)
        cursor["job_run_id"] = run_id
        cursor["job_parent_attempt"] = attempt
        cursor["job_run_updated_at"] = updated_at
        return 1
    return max(1, int(cursor["job_page"]))


async def _fetch_jobs(
    org: str,
    repo: str,
    run_id: int,
    *,
    start_page: int,
    page_budget: int,
    page_count: int,
) -> tuple[list[dict[str, Any]], bool, int, int, str | None]:
    jobs: list[dict[str, Any]] = []
    page = start_page
    while page_count < page_budget:
        payload = await gh_client.get(
            _query(
                f"/repos/{org}/{repo}/actions/runs/{run_id}/jobs",
                per_page=PAGE_SIZE,
                page=page,
                filter="all",
            )
        )
        page_count += 1
        page_items = _items(payload, "jobs")
        if page_items is None:
            return jobs, False, page, page_count, "malformed_jobs_page"
        jobs.extend(page_items)
        if len(page_items) < PAGE_SIZE:
            return jobs, True, page + 1, page_count, None
        page += 1
    return jobs, False, page, page_count, "page_cap"


async def collect_job_metrics(
    org: str,
    *,
    max_repos: int = DEFAULT_MAX_REPOS,
    max_runs: int = DEFAULT_MAX_RUNS,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> dict[str, Any]:
    """Collect a bounded batch, persisting a cursor for the next manual pass.

    Caps apply to each invocation. A cursor remains on a partially read run so
    that the next invocation can finish its attempt before advancing.
    """
    _bounded_int("max_repos", max_repos, 500)
    _bounded_int("max_runs", max_runs, 2_000)
    _bounded_int("max_pages", max_pages, 200)
    cursor_raw = load_collection_state(org)
    invalid_cursor = not isinstance(cursor_raw, dict)
    if not isinstance(cursor_raw, dict):
        cursor_raw = {}
    cursor: dict[str, Any] = {key: cursor_raw.get(key, default) for key, default in _INITIAL_CURSOR.items()}
    minimums = {
        "repo_page": 1,
        "repo_offset": 0,
        "run_page": 1,
        "run_offset": 0,
        "job_page": 1,
        "job_run_id": 0,
        "job_parent_attempt": 0,
    }
    for key, minimum in minimums.items():
        value = cursor[key]
        if isinstance(value, bool):
            invalid_cursor = True
            cursor[key] = _INITIAL_CURSOR[key]
            continue
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, str) and value.strip().isdigit():
            parsed = int(value.strip())
        else:
            invalid_cursor = True
            cursor[key] = _INITIAL_CURSOR[key]
            continue
        if parsed < minimum:
            invalid_cursor = True
            cursor[key] = _INITIAL_CURSOR[key]
        else:
            cursor[key] = parsed
    if not isinstance(cursor["job_run_updated_at"], str):
        invalid_cursor = True
        cursor["job_run_updated_at"] = ""
    prior_skips = cursor["skipped_record_reasons"]
    allowed_skips = {"repository_missing_name", "workflow_run_invalid_id"}
    if not isinstance(prior_skips, list) or any(
        not isinstance(reason, str) or reason not in allowed_skips for reason in prior_skips
    ):
        invalid_cursor = True
        prior_skips = []
    skipped_reasons = set(prior_skips)
    observed = datetime.now(UTC).isoformat()
    page_count = repo_count = run_count = job_count = 0
    partial_reasons: set[str] = {"persisted_cursor_invalid"} if invalid_cursor else set()
    caps: set[str] = set()
    retry_after: int | None = None
    current_repos: list[dict[str, Any]] = []
    repos_page_loaded = False
    current_runs: list[dict[str, Any]] = []
    runs_repo: str | None = None
    runs_page_loaded = False
    complete_cycle = False

    try:
        while page_count < max_pages:
            if not repos_page_loaded:
                payload = await gh_client.get(
                    _query(
                        f"/orgs/{org}/repos",
                        per_page=PAGE_SIZE,
                        page=cursor["repo_page"],
                        sort="full_name",
                        direction="asc",
                    )
                )
                page_count += 1
                if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
                    partial_reasons.add("malformed_repositories_page")
                    break
                current_repos = payload
                repos_page_loaded = True
                if cursor["repo_offset"] >= len(current_repos):
                    if len(current_repos) < PAGE_SIZE:
                        cursor = dict(_INITIAL_CURSOR)
                        complete_cycle = True
                        break
                    cursor["repo_page"] += 1
                    cursor["repo_offset"] = 0
                    repos_page_loaded = False
                    continue
            if cursor["repo_offset"] >= len(current_repos):
                if len(current_repos) < PAGE_SIZE:
                    cursor = dict(_INITIAL_CURSOR)
                    complete_cycle = True
                    break
                cursor["repo_page"] += 1
                cursor["repo_offset"] = 0
                repos_page_loaded = False
                continue
            if repo_count >= max_repos:
                caps.add("repo_cap")
                break
            repo = str(current_repos[cursor["repo_offset"]].get("name") or "")
            if not repo:
                partial_reasons.add("repository_missing_name")
                skipped_reasons.add("repository_missing_name")
                if _next_repo(cursor, current_repos, len(current_repos) == PAGE_SIZE):
                    repos_page_loaded = False
                repo_count += 1
                continue
            if not runs_page_loaded or runs_repo != repo:
                if page_count >= max_pages:
                    caps.add("page_cap")
                    break
                runs_payload = await gh_client.get(
                    _query(
                        f"/repos/{org}/{repo}/actions/runs",
                        per_page=PAGE_SIZE,
                        page=cursor["run_page"],
                    )
                )
                page_count += 1
                current_runs = _items(runs_payload, "workflow_runs") or []
                if not isinstance(runs_payload, dict) or _items(runs_payload, "workflow_runs") is None:
                    partial_reasons.add("malformed_workflow_runs_page")
                    break
                runs_repo = repo
                runs_page_loaded = True
            if cursor["run_offset"] >= len(current_runs):
                if len(current_runs) < PAGE_SIZE:
                    if _next_repo(cursor, current_repos, len(current_repos) == PAGE_SIZE):
                        repos_page_loaded = False
                    repo_count += 1
                    runs_repo = None
                    runs_page_loaded = False
                else:
                    cursor["run_page"] += 1
                    cursor["run_offset"] = 0
                    runs_repo = None
                    runs_page_loaded = False
                continue
            if run_count >= max_runs:
                caps.add("run_cap")
                break
            run = current_runs[cursor["run_offset"]]
            run_id = positive_identifier(run.get("id"))
            if run_id is None:
                partial_reasons.add("workflow_run_invalid_id")
                skipped_reasons.add("workflow_run_invalid_id")
                if _advance_run(cursor, current_runs, len(current_runs) == PAGE_SIZE):
                    runs_repo = None
                    runs_page_loaded = False
                run_count += 1
                continue
            if completed_run_is_unchanged(org, repo, run):
                if _advance_run(cursor, current_runs, len(current_runs) == PAGE_SIZE):
                    runs_repo = None
                    runs_page_loaded = False
                run_count += 1
                continue
            if page_count >= max_pages:
                caps.add("page_cap")
                break
            start_job_page = _resume_job_page(cursor, run, run_id)
            try:
                jobs, job_complete, next_job_page, page_count, job_error = await _fetch_jobs(
                    org,
                    repo,
                    run_id,
                    start_page=start_job_page,
                    page_budget=max_pages,
                    page_count=page_count,
                )
            except gh_client.GhRateLimited as exc:
                save_job_batch(
                    org,
                    repo,
                    run,
                    [],
                    complete=False,
                    observed_at=observed,
                    run_updated_at=run.get("updated_at"),
                )
                raise exc
            valid_jobs = []
            run_reasons: set[str] = set()
            for job in jobs:
                if positive_attempt(job.get("run_attempt")) is None:
                    partial_reasons.add("job_run_attempt_missing_or_invalid")
                    run_reasons.add("job_run_attempt_missing_or_invalid")
                if positive_identifier(job.get("id")) is None:
                    partial_reasons.add("job_id_invalid")
                    run_reasons.add("job_id_invalid")
                    continue
                normalized_job = validated_job_payload(job, run_id)
                if normalized_job is None:
                    partial_reasons.add("job_payload_invalid")
                    run_reasons.add("job_payload_invalid")
                    continue
                valid_jobs.append(normalized_job)
            persistent_reasons = set(run_reasons)
            if job_error and job_error != "page_cap":
                persistent_reasons.add(job_error)
            save_job_batch(
                org,
                repo,
                run,
                valid_jobs,
                complete=job_complete and not run_reasons,
                observed_at=observed,
                run_updated_at=run.get("updated_at"),
                collection_reasons=sorted(persistent_reasons),
            )
            job_count += len(valid_jobs)
            if not job_complete:
                partial_reasons.add(job_error or "jobs_incomplete")
                if job_error == "page_cap":
                    caps.add("page_cap")
                    cursor["job_page"] = next_job_page
                    cursor["job_run_id"] = run_id
                    cursor["job_parent_attempt"] = positive_attempt(run.get("run_attempt")) or 0
                    cursor["job_run_updated_at"] = str(run.get("updated_at") or "")
                    break
                break
            if run_reasons:
                if _advance_run(cursor, current_runs, len(current_runs) == PAGE_SIZE):
                    runs_repo = None
                    runs_page_loaded = False
                run_count += 1
                continue
            if _advance_run(cursor, current_runs, len(current_runs) == PAGE_SIZE):
                runs_repo = None
                runs_page_loaded = False
            run_count += 1
    except gh_client.GhRateLimited as exc:
        partial_reasons.add("rate_limited")
        retry_after = max(0, int(exc.retry_after_seconds))
    except gh_client.GhAuthError:
        partial_reasons.add("github_auth_unavailable")
    except (gh_client.GhClientError, gh_client.GhServerError) as exc:
        partial_reasons.add(f"github_request_failed:{type(exc).__name__}")

    if page_count >= max_pages and not complete_cycle:
        caps.add("page_cap")
    partial_reasons.update(skipped_reasons)
    if not complete_cycle:
        cursor["skipped_record_reasons"] = sorted(skipped_reasons)
    pending_runs = incomplete_run_count(org)
    status = {
        "complete": complete_cycle and not partial_reasons and not caps and pending_runs == 0,
        "traversal_complete": complete_cycle,
        "incomplete_runs": pending_runs,
        "partial_reasons": sorted(partial_reasons),
        "caps": sorted(caps),
        "pages": page_count,
        "repositories": repo_count,
        "runs": run_count,
        "jobs": job_count,
        "retry_after_seconds": retry_after,
        "observed_at": observed,
    }
    save_collection_state(org, cursor, key_suffix="cursor")
    save_collection_state(org, status, key_suffix="status")
    return {"cursor": cursor, **status}
