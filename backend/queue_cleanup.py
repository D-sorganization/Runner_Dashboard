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
from dataclasses import asdict, dataclass
from datetime import timedelta
from enum import Enum

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
    """Check if the run targets main, release, tags, or scheduled maintenance."""
    if branch in ("main", "master", "release"):
        return True
    if event == "release" or (branch and (branch.startswith("v") or "/" in branch and "tags" in branch)):
        return True
    if event == "schedule" or (workflow and "maintenance" in workflow.lower()):
        return True
    return False


async def _queued_stale_for_repo(
    org: str,
    repo: str,
    min_age: timedelta,
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

    # Group structure: (workflow, pr_number) -> list of runs
    pr_groups: dict[tuple[str, int], list[dict]] = {}
    non_pr_runs: list[dict] = []

    for run in unique_runs:
        raw_ts = run.get("created_at", "")
        try:
            created = _dt.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        age = now - created
        if age < min_age:
            continue

        branch = run.get("head_branch", "?") or "?"
        event = run.get("event", "")
        workflow = run.get("name", "?")

        # Check if targets main, release, tags, or scheduled maintenance
        protected = is_protected_target(branch, event, workflow)

        # Determine if it has PR metadata
        pull_requests = run.get("pull_requests") or []
        is_pr_run = bool(pull_requests) or (event == "pull_request")

        if is_pr_run and (not protected or ALLOW_PROTECTED_PR_HEAD_STALE):
            if pull_requests:
                pr_number = pull_requests[0].get("number")
                if pr_number is not None:
                    group_key = (workflow, pr_number)
                    pr_groups.setdefault(group_key, []).append(run)
                    continue
            # If event == "pull_request" but pull_requests is empty/missing
            age_minutes = int(age.total_seconds() / 60)
            stale.append(
                StaleRun(
                    repo=repo,
                    run_id=run.get("id", 0),
                    workflow=workflow,
                    branch=branch,
                    created_at=raw_ts,
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
        # Sort runs in group by created_at descending (newest first)
        group_runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)

        if pr_number not in pr_cache:
            pr_cache[pr_number] = await fetch_pr_details(org, repo, pr_number)
        pr_details = pr_cache[pr_number]

        if pr_details is None:
            # Missing PR metadata
            for run in group_runs:
                raw_ts = run.get("created_at", "")
                created = _dt.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                age_minutes = int((now - created).total_seconds() / 60)
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
                created = _dt.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                age_minutes = int((now - created).total_seconds() / 60)
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
            created = _dt.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            age_minutes = int((now - created).total_seconds() / 60)
            status = run.get("status", "queued")
            run_sha = run.get("head_sha")

            is_newest = idx == 0
            matches_current = run_sha == current_sha

            if is_newest and matches_current:
                # Retained / active run
                continue

            if status == "queued":
                safe_to_cancel = True
            else:
                safe_to_cancel = age_minutes > IN_PROGRESS_STRICT_THRESHOLD_MINUTES

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
        created = _dt.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        age_minutes = int((now - created).total_seconds() / 60)
        branch = run.get("head_branch", "?") or "?"
        status = run.get("status", "queued")

        run_reason, safe_to_cancel = classify_stale_run(branch, age_minutes)
        if status == "in_progress":
            safe_to_cancel = False

        stale.append(
            StaleRun(
                repo=repo,
                run_id=run.get("id", 0),
                workflow=run.get("name", "?"),
                branch=branch,
                created_at=raw_ts,
                age_minutes=age_minutes,
                reason=run_reason,
                safe_to_cancel=safe_to_cancel,
                url=f"https://github.com/{org}/{repo}/actions/runs/{run.get('id', 0)}",
            )
        )

    return stale


async def find_stale_runs(
    org: str,
    min_age_minutes: int = DEFAULT_MIN_AGE_MINUTES,
    repo: str | None = None,
    reason: str | None = None,
) -> list[StaleRun]:
    """Scan every repo in *org* for queued runs older than *min_age_minutes*.

    Runs up to _SCAN_CONCURRENCY repo queries in parallel to stay fast
    without hammering the GitHub API.  Sorted oldest-first so the worst
    offenders appear at the top.
    """
    if repo:
        from security import validate_repo_slug

        validated_repo = validate_repo_slug(repo)
        min_age = timedelta(minutes=min_age_minutes)
        flat = await _queued_stale_for_repo(org, validated_repo, min_age)
    else:
        repos = await list_all_repos(org)
        min_age = timedelta(minutes=min_age_minutes)
        sem = asyncio.Semaphore(_SCAN_CONCURRENCY)

        async def bounded(r: str) -> list[StaleRun]:
            async with sem:
                return await _queued_stale_for_repo(org, r, min_age)

        nested = await asyncio.gather(*[bounded(r) for r in repos])
        flat = [run for runs in nested for run in runs]

    if reason:
        flat = [r for r in flat if r.reason.lower() == reason.lower()]

    return sorted(flat, key=lambda r: r.age_minutes, reverse=True)


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


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
    # 409 = run already completed -- no longer in queue, treat as success
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
) -> dict:
    """Find stale runs and optionally cancel them all.

    Returns a summary dict suitable for direct JSON serialisation.
    When *dry_run* is True the runs are listed but nothing is cancelled.
    """
    stale = await find_stale_runs(org, min_age_minutes, repo=repo, reason=reason)
    cancelled_count = 0
    errors: list[str] = []

    if not dry_run and stale:
        sem = asyncio.Semaphore(_CANCEL_CONCURRENCY)

        async def bounded_cancel(run: StaleRun) -> None:
            nonlocal cancelled_count
            async with sem:
                if await _cancel_one(org, run):
                    cancelled_count += 1
                else:
                    errors.append(f"{run.repo}#{run.run_id}: {run.cancel_error}")

        await asyncio.gather(*[bounded_cancel(r) for r in stale])

    return {
        "org": org,
        "min_age_minutes": min_age_minutes,
        "dry_run": dry_run,
        "stale_count": len(stale),
        "cancelled_count": cancelled_count if not dry_run else 0,
        "errors": errors,
        "runs": [r.as_dict() for r in stale],
    }
