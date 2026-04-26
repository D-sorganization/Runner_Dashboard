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

log = logging.getLogger(__name__)

UTC = _dt.UTC
DEFAULT_MIN_AGE_MINUTES: int = 60
_MAX_REPOS: int = 200
_MAX_RUNS_PER_REPO: int = 100
_SCAN_CONCURRENCY: int = 10  # concurrent repo queries during scan
_CANCEL_CONCURRENCY: int = 5  # concurrent cancel calls


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


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


async def _queued_stale_for_repo(
    org: str,
    repo: str,
    min_age: timedelta,
) -> list[StaleRun]:
    now = _dt.datetime.now(UTC)
    data = await _gh_json(
        "api",
        f"/repos/{org}/{repo}/actions/runs?status=queued&per_page={_MAX_RUNS_PER_REPO}",
        default={},
        timeout=20,
    )
    stale: list[StaleRun] = []
    for run in (data or {}).get("workflow_runs", []):
        raw_ts = run.get("created_at", "")
        try:
            created = _dt.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        age = now - created
        if age < min_age:
            continue
        stale.append(
            StaleRun(
                repo=repo,
                run_id=run.get("id", 0),
                workflow=run.get("name", "?"),
                branch=run.get("head_branch", "?"),
                created_at=raw_ts,
                age_minutes=int(age.total_seconds() / 60),
            )
        )
    return stale


async def find_stale_runs(
    org: str,
    min_age_minutes: int = DEFAULT_MIN_AGE_MINUTES,
) -> list[StaleRun]:
    """Scan every repo in *org* for queued runs older than *min_age_minutes*.

    Runs up to _SCAN_CONCURRENCY repo queries in parallel to stay fast
    without hammering the GitHub API.  Sorted oldest-first so the worst
    offenders appear at the top.
    """
    repos = await list_all_repos(org)
    min_age = timedelta(minutes=min_age_minutes)
    sem = asyncio.Semaphore(_SCAN_CONCURRENCY)

    async def bounded(repo: str) -> list[StaleRun]:
        async with sem:
            return await _queued_stale_for_repo(org, repo, min_age)

    nested = await asyncio.gather(*[bounded(r) for r in repos])
    flat = [run for runs in nested for run in runs]
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
    *,
    dry_run: bool = False,
) -> dict:
    """Find stale runs and optionally cancel them all.

    Returns a summary dict suitable for direct JSON serialisation.
    When *dry_run* is True the runs are listed but nothing is cancelled.
    """
    stale = await find_stale_runs(org, min_age_minutes)
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
