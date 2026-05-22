"""
queue_cleanup.py -- Stale-queue detection and bulk cancellation.

Exposes async helpers consumed by server.py's /api/queue/stale and
/api/queue/purge-stale endpoints.

Root cause of stale jobs
------------------------
The normal /api/queue endpoint samples only the 15 most-recently-updated
repos.  Queued runs in repos that have been quiet for days (or months) are
completely invisible to that view and accumulate indefinitely.  This module
scans the entire org so nothing is missed.

Common sources of stale runs:
  - Runner goes offline while jobs are in queue (reboot, network drop)
  - Runner label mismatch (no runner registered for that label any more)
  - Abandoned agent worktree runs that pushed a branch but exited before
    the queue was explicitly cancelled
  - GitHub Actions own queuing lag on heavily-loaded orgs
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import timedelta
from enum import Enum
from pathlib import Path

from dashboard_config.timeouts import Concurrency

log = logging.getLogger(__name__)

UTC = getattr(_dt, "UTC", _dt.timezone.utc)  # noqa: UP017
DEFAULT_MIN_AGE_MINUTES: int = 60


def _get_now() -> _dt.datetime:
    return _dt.datetime.now(UTC)


_MAX_REPOS: int = 200
_MAX_RUNS_PER_REPO: int = 100
_SCAN_CONCURRENCY: int = Concurrency.QUEUE_SCAN  # concurrent repo queries during scan (capped per #393)
_CANCEL_CONCURRENCY: int = Concurrency.QUEUE_CANCEL  # concurrent cancel calls


# ---------------------------------------------------------------------------
# Data and Policies
# ---------------------------------------------------------------------------


class StaleReason(str, Enum):
    SUPERSEDED_PR_HEAD = "superseded_pr_head"
    CLOSED_OR_DELETED_REF = "closed_or_deleted_ref"
    ABANDONED_AGENT = "abandoned-agent-run"
    STALE_FEATURE_BRANCH = "stale-feature-branch"
    OFFLINE_RUNNER_OR_LAG = "offline-runner-or-lag"
    STALE_MAIN_BRANCH = "stale-main-branch-queue"
    UNSATISFIABLE_LABELS = "unsatisfiable_labels"
    UNKNOWN = "unknown"


# Policy configuration constants
ALLOW_PROTECTED_PR_HEAD_STALE: bool = False
IN_PROGRESS_STRICT_THRESHOLD_MINUTES: int = 120


def classify_stale_run(branch: str, age_minutes: int) -> tuple[str, bool]:
    """Determine the reason and safety status of a stale run based on branch and age."""
    if branch in ("main", "master", "release"):
        if age_minutes > 360:
            return StaleReason.OFFLINE_RUNNER_OR_LAG.value, True
        else:
            return StaleReason.STALE_MAIN_BRANCH.value, False
    elif any(x in branch.lower() for x in ("agent", "worktree", "wt-", "patch-", "run-")):
        return StaleReason.ABANDONED_AGENT.value, True
    else:
        return StaleReason.STALE_FEATURE_BRANCH.value, True


@dataclass
class StaleRun:
    repo: str
    run_id: int
    workflow: str
    branch: str
    created_at: str
    age_minutes: int
    cancelled: bool = False
    cancel_error: str = ""
    reason: str = ""
    safe_to_cancel: bool = True
    url: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# gh CLI helpers
# ---------------------------------------------------------------------------


async def _gh(*args: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run a gh CLI subcommand asynchronously; return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "gh",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        raw_out, raw_err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        return 1, "", "timeout"
    return (
        proc.returncode or 0,
        raw_out.decode("utf-8", errors="replace"),
        raw_err.decode("utf-8", errors="replace"),
    )


async def _gh_json(*args: str, default=None, timeout: int = 30):
    """Run a gh CLI command and return parsed JSON, or *default* on failure."""
    code, stdout, stderr = await _gh(*args, timeout=timeout)
    if code != 0:
        log.debug("gh error [%s]: %s", " ".join(args[:3]), stderr.strip()[:200])
        return default
    if not stdout.strip():
        return default
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return default


def parse_concatenated_json(s: str) -> list[dict]:
    """Safely parse concatenated JSON documents (e.g. from gh api --paginate)."""
    decoder = json.JSONDecoder()
    s = s.strip()
    results = []
    pos = 0
    while pos < len(s):
        # Skip leading whitespace
        while pos < len(s) and s[pos].isspace():
            pos += 1
        if pos >= len(s):
            break
        try:
            doc, idx = decoder.raw_decode(s, pos)
            results.append(doc)
            pos = idx
        except json.JSONDecodeError:
            break
    return results


async def get_online_runners(org: str) -> list[dict]:
    """Fetch online self-hosted runners from GitHub.
    Returns a list of runners, each with a lowercased list of labels.
    """
    code, stdout, stderr = await _gh(
        "api",
        "--paginate",
        f"/orgs/{org}/actions/runners",
        timeout=30,
    )
    if code != 0:
        log.warning("Failed to fetch runners: %s", stderr)
        return []

    docs = parse_concatenated_json(stdout)
    runners = []
    for doc in docs:
        for runner in doc.get("runners", []):
            if runner.get("status") == "online":
                labels = [lbl["name"].lower() for lbl in runner.get("labels", []) if "name" in lbl]
                runners.append({"name": runner.get("name"), "labels": labels})
    return runners


def is_hosted_label(label: str) -> bool:
    """Check if a label belongs to GitHub-hosted runners."""
    label = label.lower()
    return (
        label.startswith("ubuntu-")
        or label.startswith("windows-")
        or label.startswith("macos-")
        or label in ("ubuntu", "windows", "macos")
    )


def is_job_unsatisfiable(job_labels: list[str], online_runners: list[dict]) -> bool:
    """Check if a self-hosted job's labels are not a subset of any online runner's labels."""
    if any(is_hosted_label(lbl) for lbl in job_labels):
        return False
    if not job_labels:
        return False
    job_labels_set = set(job_labels)
    for runner in online_runners:
        if job_labels_set.issubset(runner["labels"]):
            return False
    return True


# ---------------------------------------------------------------------------
# Repo discovery
# ---------------------------------------------------------------------------


async def list_all_repos(org: str) -> list[str]:
    """Return every non-archived repo name in *org* (up to _MAX_REPOS)."""
    data = await _gh_json(
        "repo",
        "list",
        org,
        "--limit",
        str(_MAX_REPOS),
        "--json",
        "name,isArchived",
        default=[],
        timeout=45,
    )
    return [r["name"] for r in (data or []) if not r.get("isArchived") and r.get("name")]


# ---------------------------------------------------------------------------
# Stale-run detection
# ---------------------------------------------------------------------------


async def branch_exists(org: str, repo: str, branch: str) -> bool:
    """Check if a branch exists on remote using gh api."""
    code, _, _ = await _gh("api", f"/repos/{org}/{repo}/branches/{branch}", timeout=10)
    return code == 0


async def fetch_pr_details(org: str, repo: str, pr_number: int) -> dict | None:
    """Fetch details of a PR using gh api."""
    return await _gh_json(
        "api",
        f"/repos/{org}/{repo}/pulls/{pr_number}",
        default=None,
        timeout=15,
    )


def is_protected_target(branch: str, event: str, workflow: str) -> bool:
    """Check if the run targets main, master, release, tags, or scheduled/deploy/release workflows."""
    if not branch:
        branch = ""
    if not workflow:
        workflow = ""
    workflow_lower = workflow.lower()

    if branch in ("main", "master", "release"):
        return True
    if event == "release" or branch.startswith("v") or ("tags/" in branch) or ("/tags" in branch):
        return True
    if (
        event == "schedule"
        or "maintenance" in workflow_lower
        or "deploy" in workflow_lower
        or "release" in workflow_lower
    ):
        return True
    return False


def is_forbidden_by_rule_6(branch: str, event: str, workflow: str, is_current: bool) -> bool:
    """Rule 6: Do not cancel current-head required checks, releases, tag workflows, or deploys."""
    if not branch:
        branch = ""
    if not workflow:
        workflow = ""
    workflow_lower = workflow.lower()

    # Releases: event is release, or workflow name contains "release"
    if event == "release" or "release" in workflow_lower:
        return True

    # Tag workflows: branch starts with v, or contains tags/ or /tags, or event is release
    if branch.startswith("v") or ("tags/" in branch) or ("/tags" in branch):
        return True

    # Deploys: workflow name contains "deploy"
    if "deploy" in workflow_lower:
        return True

    # Current-head required checks:
    # If it is a current-head run (is_current is True) AND it's a push or pull_request event
    # (meaning it is a check run triggered by code changes, which could be required).
    if is_current and event in ("pull_request", "push"):
        return True

    return False


async def _queued_stale_for_repo(
    org: str,
    repo: str,
    min_age: timedelta,
    online_runners: list[dict] | None = None,
) -> list[StaleRun]:
    now = _get_now()

    # Fetch queued and in_progress runs
    queued_data = await _gh_json(
        "api",
        f"/repos/{org}/{repo}/actions/runs?status=queued&per_page={_MAX_RUNS_PER_REPO}",
        default={},
        timeout=20,
    )
    in_progress_data = await _gh_json(
        "api",
        f"/repos/{org}/{repo}/actions/runs?status=in_progress&per_page={_MAX_RUNS_PER_REPO}",
        default={},
        timeout=20,
    )

    runs = []
    if queued_data and "workflow_runs" in queued_data:
        runs.extend(queued_data["workflow_runs"])
    if in_progress_data and "workflow_runs" in in_progress_data:
        runs.extend(in_progress_data["workflow_runs"])

    # Deduplicate runs by id
    seen_ids = set()
    unique_runs = []
    for r in runs:
        rid = r.get("id")
        if rid is not None and rid not in seen_ids:
            seen_ids.add(rid)
            unique_runs.append(r)

    stale: list[StaleRun] = []
    pr_cache: dict[int, dict | None] = {}
    branch_sha_cache: dict[tuple[str, str], str | None] = {}

    async def get_branch_head_sha(b: str) -> str | None:
        key = (repo, b)
        if key in branch_sha_cache:
            return branch_sha_cache[key]
        data = await _gh_json(
            "api",
            f"/repos/{org}/{repo}/branches/{b}",
            default=None,
            timeout=10,
        )
        sha = data.get("commit", {}).get("sha") if data else None
        branch_sha_cache[key] = sha
        return sha

    # Fetch jobs for queued runs in parallel to check for unsatisfiable labels
    queued_runs = [r for r in unique_runs if r.get("status") == "queued"]
    run_jobs_map: dict[int, list[dict]] = {}

    if online_runners is not None and queued_runs:
        job_sem = asyncio.Semaphore(10)

        async def fetch_jobs(r_id: int) -> list[dict]:
            async with job_sem:
                data = await _gh_json(
                    "api",
                    f"/repos/{org}/{repo}/actions/runs/{r_id}/jobs?filter=latest",
                    default={},
                    timeout=15,
                )
                return data.get("jobs", [])

        jobs_results = await asyncio.gather(*[fetch_jobs(r["id"]) for r in queued_runs])
        run_jobs_map = {r["id"]: jobs for r, jobs in zip(queued_runs, jobs_results, strict=True)}

    async def fetch_pr_details_cached(pr_num: int) -> dict | None:
        if pr_num not in pr_cache:
            pr_cache[pr_num] = await fetch_pr_details(org, repo, pr_num)
        return pr_cache[pr_num]

    async def check_current_head(run_obj: dict) -> bool:
        branch = run_obj.get("head_branch") or "?"
        event = run_obj.get("event", "")
        pull_requests = run_obj.get("pull_requests") or []
        is_pr = bool(pull_requests) or (event == "pull_request")

        current_sha = None
        if is_pr:
            if pull_requests:
                pr_number = pull_requests[0].get("number")
                if pr_number is not None:
                    pr_details = await fetch_pr_details_cached(pr_number)
                    if pr_details:
                        current_sha = pr_details.get("head", {}).get("sha") or pr_details.get("headRefOid")
        else:
            if branch and branch != "?":
                current_sha = await get_branch_head_sha(branch)

        if not current_sha:
            return False
        return run_obj.get("head_sha") == current_sha

    # Filter runs by age
    runs_to_process = []
    for run in unique_runs:
        raw_ts = run.get("created_at", "")
        try:
            created = _dt.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        age = now - created
        if age < min_age:
            continue
        runs_to_process.append((run, created, age))

    # Check unsatisfiable labels first
    unsat_runs = set()
    if online_runners is not None:
        for run, _, age in runs_to_process:
            r_id = run.get("id", 0)
            status = run.get("status")
            if status == "queued" and r_id in run_jobs_map:
                jobs = run_jobs_map[r_id]
                is_unsat = False
                for job in jobs:
                    job_labels = [lbl.lower() for lbl in job.get("labels", [])]
                    if is_job_unsatisfiable(job_labels, online_runners):
                        is_unsat = True
                        break

                if is_unsat:
                    branch = run.get("head_branch", "?") or "?"
                    event = run.get("event", "")
                    workflow = run.get("name", "?")
                    protected = is_protected_target(branch, event, workflow)
                    is_current = await check_current_head(run)
                    safe_to_cancel = not (protected or is_current)
                    if is_forbidden_by_rule_6(branch, event, workflow, is_current):
                        safe_to_cancel = False
                    age_minutes = int(age.total_seconds() / 60)

                    stale.append(
                        StaleRun(
                            repo=repo,
                            run_id=r_id,
                            workflow=workflow,
                            branch=branch,
                            created_at=run.get("created_at", ""),
                            age_minutes=age_minutes,
                            reason=StaleReason.UNSATISFIABLE_LABELS.value,
                            safe_to_cancel=safe_to_cancel,
                            url=f"https://github.com/{org}/{repo}/actions/runs/{r_id}",
                        )
                    )
                    unsat_runs.add(r_id)

    # Process remaining runs for PR or branch stale classification
    remaining_runs = [(run, created, age) for run, created, age in runs_to_process if run.get("id") not in unsat_runs]

    pr_groups: dict[tuple[str, int], list[dict]] = {}
    non_pr_runs: list[dict] = []

    for run, _, age in remaining_runs:
        branch = run.get("head_branch", "?") or "?"
        event = run.get("event", "")
        workflow = run.get("name", "?")

        protected = is_protected_target(branch, event, workflow)
        pull_requests = run.get("pull_requests") or []
        is_pr_run = bool(pull_requests) or (event == "pull_request")

        if is_pr_run and (not protected or ALLOW_PROTECTED_PR_HEAD_STALE):
            if pull_requests:
                pr_number = pull_requests[0].get("number")
                if pr_number is not None:
                    group_key = (workflow, pr_number)
                    pr_groups.setdefault(group_key, []).append(run)
                    continue

            age_minutes = int(age.total_seconds() / 60)
            stale.append(
                StaleRun(
                    repo=repo,
                    run_id=run.get("id", 0),
                    workflow=workflow,
                    branch=branch,
                    created_at=run.get("created_at", ""),
                    age_minutes=age_minutes,
                    reason=StaleReason.UNKNOWN.value,
                    safe_to_cancel=False,
                    url=f"https://github.com/{org}/{repo}/actions/runs/{run.get('id', 0)}",
                )
            )
        else:
            non_pr_runs.append(run)

    # Process PR groups
    for (workflow, pr_number), group_runs in pr_groups.items():
        group_runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)

        pr_details = await fetch_pr_details_cached(pr_number)
        if pr_details is None:
            for run in group_runs:
                raw_ts = run.get("created_at", "")
                try:
                    created = _dt.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                    age_minutes = int((now - created).total_seconds() / 60)
                except Exception:
                    age_minutes = 0
                stale.append(
                    StaleRun(
                        repo=repo,
                        run_id=run.get("id", 0),
                        workflow=workflow,
                        branch=run.get("head_branch", "?") or "?",
                        created_at=raw_ts,
                        age_minutes=age_minutes,
                        reason=StaleReason.UNKNOWN.value,
                        safe_to_cancel=False,
                        url=f"https://github.com/{org}/{repo}/actions/runs/{run.get('id', 0)}",
                    )
                )
            continue

        is_closed = pr_details.get("state") == "closed" or pr_details.get("merged") is True

        ref_exists = True
        if not is_closed:
            head_branch = pr_details.get("head", {}).get("ref") or group_runs[0].get("head_branch")
            if head_branch:
                ref_exists = await branch_exists(org, repo, head_branch)

        if is_closed or not ref_exists:
            for run in group_runs:
                raw_ts = run.get("created_at", "")
                try:
                    created = _dt.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                    age_minutes = int((now - created).total_seconds() / 60)
                except Exception:
                    age_minutes = 0
                status = run.get("status", "queued")
                stale.append(
                    StaleRun(
                        repo=repo,
                        run_id=run.get("id", 0),
                        workflow=workflow,
                        branch=run.get("head_branch", "?") or "?",
                        created_at=raw_ts,
                        age_minutes=age_minutes,
                        reason=StaleReason.CLOSED_OR_DELETED_REF.value,
                        safe_to_cancel=(status == "queued"),
                        url=f"https://github.com/{org}/{repo}/actions/runs/{run.get('id', 0)}",
                    )
                )
            continue

        current_sha = pr_details.get("head", {}).get("sha") or pr_details.get("headRefOid")

        for idx, run in enumerate(group_runs):
            raw_ts = run.get("created_at", "")
            try:
                created = _dt.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                age_minutes = int((now - created).total_seconds() / 60)
            except Exception:
                age_minutes = 0
            status = run.get("status", "queued")
            run_sha = run.get("head_sha")

            is_newest = idx == 0
            matches_current = run_sha == current_sha

            if is_newest and matches_current:
                continue

            if status == "queued":
                safe_to_cancel = True
            else:
                safe_to_cancel = age_minutes > IN_PROGRESS_STRICT_THRESHOLD_MINUTES

            branch = run.get("head_branch", "?") or "?"
            event = run.get("event", "")
            if is_forbidden_by_rule_6(branch, event, workflow, is_current=False):
                safe_to_cancel = False

            stale.append(
                StaleRun(
                    repo=repo,
                    run_id=run.get("id", 0),
                    workflow=workflow,
                    branch=run.get("head_branch", "?") or "?",
                    created_at=raw_ts,
                    age_minutes=age_minutes,
                    reason=StaleReason.SUPERSEDED_PR_HEAD.value,
                    safe_to_cancel=safe_to_cancel,
                    url=f"https://github.com/{org}/{repo}/actions/runs/{run.get('id', 0)}",
                )
            )

    # Process non-PR runs using fallback
    for run in non_pr_runs:
        raw_ts = run.get("created_at", "")
        try:
            created = _dt.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            age_minutes = int((now - created).total_seconds() / 60)
        except Exception:
            age_minutes = 0
        branch = run.get("head_branch", "?") or "?"
        event = run.get("event", "")
        workflow = run.get("name", "?")
        status = run.get("status", "queued")

        run_reason, safe_to_cancel = classify_stale_run(branch, age_minutes)
        is_current = await check_current_head(run)
        if is_forbidden_by_rule_6(branch, event, workflow, is_current):
            safe_to_cancel = False
        if status == "in_progress":
            safe_to_cancel = False

        stale.append(
            StaleRun(
                repo=repo,
                run_id=run.get("id", 0),
                workflow=workflow,
                branch=branch,
                created_at=raw_ts,
                age_minutes=age_minutes,
                reason=run_reason,
                safe_to_cancel=safe_to_cancel,
                url=f"https://github.com/{org}/{repo}/actions/runs/{run.get('id', 0)}",
            )
        )

    import prometheus_metrics as pm

    for run in stale:
        log.info(
            "Stale run classified: repo=%s, run_id=%d, workflow=%s, branch=%s, reason=%s, safe_to_cancel=%s",
            run.repo,
            run.run_id,
            run.workflow,
            run.branch,
            run.reason,
            str(run.safe_to_cancel),
        )
        pm.record_stale_candidate(reason=run.reason)

    return stale


async def find_stale_runs(
    org: str,
    min_age_minutes: int = DEFAULT_MIN_AGE_MINUTES,
    repo: str | None = None,
    reason: str | None = None,
    *,
    safe_to_cancel_only: bool = False,
) -> list[StaleRun]:
    """Scan every repo in *org* for queued runs older than *min_age_minutes*.

    Runs up to _SCAN_CONCURRENCY repo queries in parallel to stay fast
    without hammering the GitHub API.  Sorted oldest-first so the worst
    offenders appear at the top.
    """
    online_runners = await get_online_runners(org)

    if repo:
        from security import validate_repo_slug

        validated_repo = validate_repo_slug(repo)
        min_age = timedelta(minutes=min_age_minutes)
        flat = await _queued_stale_for_repo(org, validated_repo, min_age, online_runners)
    else:
        repos = await list_all_repos(org)
        min_age = timedelta(minutes=min_age_minutes)
        sem = asyncio.Semaphore(_SCAN_CONCURRENCY)

        async def bounded(r: str) -> list[StaleRun]:
            async with sem:
                return await _queued_stale_for_repo(org, r, min_age, online_runners)

        nested = await asyncio.gather(*[bounded(r) for r in repos])
        flat = [run for runs in nested for run in runs]

    if reason:
        flat = [r for r in flat if r.reason.lower() == reason.lower()]

    if safe_to_cancel_only:
        flat = [r for r in flat if r.safe_to_cancel]

    # Calculate queue age stats from the found stale runs
    import prometheus_metrics as pm

    if flat:
        ages = [r.age_minutes * 60 for r in flat]
        oldest = max(ages)
        pm.update_stale_queue_age(oldest)
        # Calculate percentiles
        sorted_ages = sorted(ages)
        n = len(sorted_ages)

        def pct(p):
            idx = int(round(p * (n - 1)))
            return sorted_ages[idx]

        pm.update_stale_queue_age_percentiles(
            {"0.5": float(pct(0.5)), "0.9": float(pct(0.9)), "0.95": float(pct(0.95)), "0.99": float(pct(0.99))}
        )
    else:
        pm.update_stale_queue_age(0)
        pm.update_stale_queue_age_percentiles({"0.5": 0.0, "0.9": 0.0, "0.95": 0.0, "0.99": 0.0})

    return sorted(flat, key=lambda r: r.age_minutes, reverse=True)


async def _cancel_one(org: str, run: StaleRun) -> bool:
    """POST the GitHub cancel endpoint for one run.  Returns True on success."""
    code, _, stderr = await _gh(
        "api",
        "--method",
        "POST",
        f"/repos/{org}/{run.repo}/actions/runs/{run.run_id}/cancel",
        timeout=15,
    )
    if code == 0:
        run.cancelled = True
        return True
    if "409" in stderr or "Cannot cancel" in stderr or "already" in stderr.lower():
        run.cancelled = True
        run.cancel_error = "already-finished"
        return True
    run.cancel_error = stderr.strip()[:200]
    return False


async def purge_stale_runs(
    org: str,
    min_age_minutes: int = DEFAULT_MIN_AGE_MINUTES,
    repo: str | None = None,
    reason: str | None = None,
    *,
    dry_run: bool = False,
    max_cancel: int | None = None,
    safe_to_cancel_only: bool = False,
) -> dict:
    """Find stale runs and optionally cancel them all.

    Returns a summary dict suitable for direct JSON serialisation.
    When *dry_run* is True the runs are listed but nothing is cancelled.
    """
    stale = await find_stale_runs(
        org,
        min_age_minutes,
        repo=repo,
        reason=reason,
        safe_to_cancel_only=safe_to_cancel_only,
    )

    if max_cancel is not None and max_cancel > 0:
        stale_to_process = stale[:max_cancel]
    else:
        stale_to_process = stale

    cancelled_count = 0
    errors: list[str] = []

    import prometheus_metrics as pm

    if dry_run:
        for run in stale_to_process:
            log.info(
                "Cancellation decision: repo=%s, run_id=%d, workflow=%s, branch=%s, "
                "reason=%s, safe_to_cancel=%s, dry_run=True, cancelled=False, error=",
                run.repo,
                run.run_id,
                run.workflow,
                run.branch,
                run.reason,
                str(run.safe_to_cancel),
            )

    if not dry_run and stale_to_process:
        sem = asyncio.Semaphore(_CANCEL_CONCURRENCY)

        async def bounded_cancel(run: StaleRun) -> None:
            nonlocal cancelled_count
            async with sem:
                if await _cancel_one(org, run):
                    cancelled_count += 1
                    pm.record_cancelled_stale_run(reason=run.reason)
                    log.info(
                        "Cancellation decision: repo=%s, run_id=%d, workflow=%s, branch=%s, "
                        "reason=%s, safe_to_cancel=%s, dry_run=False, cancelled=True, error=",
                        run.repo,
                        run.run_id,
                        run.workflow,
                        run.branch,
                        run.reason,
                        str(run.safe_to_cancel),
                    )
                else:
                    errors.append(f"{run.repo}#{run.run_id}: {run.cancel_error}")
                    pm.record_stale_queue_error(repo=run.repo, reason=run.reason)
                    log.error(
                        "Cancellation decision: repo=%s, run_id=%d, workflow=%s, branch=%s, "
                        "reason=%s, safe_to_cancel=%s, dry_run=False, cancelled=False, error=%s",
                        run.repo,
                        run.run_id,
                        run.workflow,
                        run.branch,
                        run.reason,
                        str(run.safe_to_cancel),
                        run.cancel_error,
                    )

        await asyncio.gather(*[bounded_cancel(r) for r in stale_to_process])

    from time_utils import utc_now_iso

    audit_record = {
        "timestamp": utc_now_iso(),
        "dry_run": dry_run,
        "stale_count": len(stale),
        "processed_count": len(stale_to_process),
        "cancelled_count": cancelled_count if not dry_run else 0,
        "errors": errors,
        "runs": [r.as_dict() for r in stale_to_process],
    }
    append_cleanup_audit(audit_record)

    # Conditional push notification
    stale_threshold = 5
    has_errors = len(errors) > 0
    if len(stale) >= stale_threshold or has_errors:
        try:
            from push import send_push

            payload = {
                "title": "Stale Queue Cleanup Report",
                "body": (
                    f"Stale candidates: {len(stale)}. "
                    f"Cancelled: {cancelled_count if not dry_run else 0}. "
                    f"Errors: {len(errors)}."
                ),
                "stale_count": len(stale),
                "errors_count": len(errors),
            }
            await send_push("queue.stale", payload)
        except Exception:
            log.exception("Failed to send queue.stale push notification")

    return audit_record


# ---------------------------------------------------------------------------
# Audit Trail Persistence
# ---------------------------------------------------------------------------

_audit_lock = threading.Lock()
_AUDIT_FILE = Path(__file__).resolve().parent.parent / "data" / "queue_cleanup_audit.ndjson"


def append_cleanup_audit(record: dict) -> None:
    """Append a cleanup run record to the audit log."""
    _AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _audit_lock:
        with open(_AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


def load_cleanup_audit(limit: int = 50) -> list[dict]:
    """Retrieve the recent cleanup audit trail."""
    if not _AUDIT_FILE.exists():
        return []
    records = []
    with _audit_lock:
        with open(_AUDIT_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        pass
    return records[-limit:][::-1]


def get_last_cleanup_result() -> dict | None:
    """Retrieve the last cleanup audit entry."""
    records = load_cleanup_audit(limit=1)
    return records[0] if records else None
