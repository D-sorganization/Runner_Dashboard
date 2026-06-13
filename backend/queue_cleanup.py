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
import contextlib
import datetime as _dt
import json
import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import timedelta
from enum import StrEnum

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


class StaleReason(StrEnum):
    SUPERSEDED_PR_HEAD = "superseded_pr_head"
    CLOSED_OR_DELETED_REF = "closed_or_deleted_ref"
    ABANDONED_AGENT = "abandoned-agent-run"
    STALE_FEATURE_BRANCH = "stale-feature-branch"
    OFFLINE_RUNNER_OR_LAG = "offline-runner-or-lag"
    STALE_MAIN_BRANCH = "stale-main-branch-queue"
    # A queued job whose required runs-on labels are not carried by ANY online
    # runner. Unlike the age/branch reasons, these will never start no matter
    # how long they wait (e.g. a removed/renamed runner label), so they are
    # always safe to cancel once past the age gate. See is_routable().
    UNROUTABLE_LABEL = "unroutable-label"
    UNKNOWN = "unknown"


# Policy configuration constants
ALLOW_PROTECTED_PR_HEAD_STALE: bool = False
IN_PROGRESS_STRICT_THRESHOLD_MINUTES: int = 120


# Branch-name shapes that identify an automated agent/worktree run (issue #934).
# Matched on SEGMENT/PREFIX boundaries, never as bare substrings: the old
# substring test flagged ordinary human branches (`fix/rerun-tests` matched
# "run-", `feat/dispatch-fix` matched "patch-", `feature/user-agent-header`
# matched "agent") and the reaper then auto-cancelled legitimate CI during
# backlogs.
#
# A branch is agent-shaped when its FIRST path segment is an agent namespace
# (`agent`, `codex`, `jules`, `worktree`) or it starts with a worktree/patch/run
# prefix token (`wt-`, `patch-`, `run-`) — i.e. the token is the start of a
# path segment, not buried mid-word.
_AGENT_BRANCH_NAMESPACES = frozenset({"agent", "codex", "jules", "worktree"})
_AGENT_BRANCH_PREFIXES = ("wt-", "patch-", "run-")

# Logins that corroborate "this is a bot/agent run". GitHub appends "[bot]" to
# GitHub-App actor logins; the named agents are our fleet bots.
_AGENT_ACTOR_LOGINS = frozenset({"codex", "jules", "dashboard-bot", "github-actions"})


def _is_agent_branch(branch: str) -> bool:
    """Return True when *branch* is shaped like an automated agent/worktree branch.

    Anchored to segment/prefix boundaries (issue #934) so ordinary human feature
    branches are never misclassified.
    """
    lowered = branch.lower()
    first_segment = lowered.split("/", 1)[0]
    if first_segment in _AGENT_BRANCH_NAMESPACES:
        return True
    return any(first_segment.startswith(prefix) for prefix in _AGENT_BRANCH_PREFIXES)


def _is_agent_actor(actor: str | None) -> bool:
    """Return True when *actor* corroborates a bot/agent-triggered run (#934)."""
    if not actor:
        return False
    login = actor.lower()
    return login.endswith("[bot]") or login in _AGENT_ACTOR_LOGINS


def classify_stale_run(branch: str, age_minutes: int, actor: str | None = None) -> tuple[str, bool]:
    """Determine the reason and safety status of a stale run.

    Agent-run classification (``safe_to_cancel=True``) requires BOTH an
    agent-shaped branch (segment/prefix anchored) AND a corroborating bot actor
    when the actor is known (issue #934). A human-actor run on an agent-named
    branch is treated as an ordinary feature branch and is NOT auto-cancellable
    on the agent reason. When the actor is unknown (``None``), branch shape alone
    decides — preserving prior behaviour for callers that cannot supply an actor.
    """
    if branch in ("main", "master", "release"):
        if age_minutes > 360:
            return StaleReason.OFFLINE_RUNNER_OR_LAG.value, True
        return StaleReason.STALE_MAIN_BRANCH.value, False

    if _is_agent_branch(branch):
        # Require corroboration when we know the actor: a human pushing to an
        # agent-shaped branch must not have their CI reaped.
        if actor is None or _is_agent_actor(actor):
            return StaleReason.ABANDONED_AGENT.value, True
        return StaleReason.STALE_FEATURE_BRANCH.value, True

    return StaleReason.STALE_FEATURE_BRANCH.value, True


def is_routable(required_labels: Iterable[str], online_label_sets: list[frozenset[str]]) -> bool:
    """Return True if some online runner can satisfy *required_labels*.

    A GitHub Actions job runs only on a runner whose label set is a SUPERSET of
    the job's `runs-on` labels. So the job is routable iff at least one online
    runner's labels ⊇ the required set.

    Empty *required_labels* (job metadata not yet populated) returns True — we
    do not have enough information to declare it stuck, so we never cancel it.
    """
    required = frozenset(label for label in required_labels if label)
    if not required:
        return True
    return any(required <= labels for labels in online_label_sets)


@dataclass
class StaleRun:
    repo: str
    run_id: int
    workflow: str
    branch: str
    created_at: str
    age_minutes: int
    html_url: str = ""
    event: str = ""
    head_sha: str = ""
    pull_request_number: int | None = None
    current_pr_head_sha: str = ""
    pr_head_superseded: bool = False
    supersession_reason: str = ""
    reason: str = "age_threshold"
    reason_detail: str = "queued run exceeded the configured age threshold"
    safe_to_cancel: bool = False
    cancelled: bool = False
    cancel_error: str = ""
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
        # Issue #939d: kill then reap, tolerating an already-exited process.
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(ProcessLookupError):
            await proc.wait()
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
# Runner-label routability
# ---------------------------------------------------------------------------


async def fetch_online_runner_label_sets(org: str) -> list[frozenset[str]]:
    """Return one label set per ONLINE org runner.

    Used to decide whether a queued job can ever be picked up. On failure (API
    error, no runners visible) returns [] — callers treat an empty inventory as
    "routability unknown" and skip cancellation, so a transient API hiccup never
    causes a false-positive cancel.
    """
    data = await _gh_json(
        "api",
        f"/orgs/{org}/actions/runners?per_page=100",
        default=None,
        timeout=30,
    )
    if not data or "runners" not in data:
        return []
    sets: list[frozenset[str]] = []
    for runner in data["runners"]:
        if runner.get("status") == "online":
            sets.append(frozenset(label["name"] for label in runner.get("labels", []) if label.get("name")))
    return sets


async def required_labels_for_run(org: str, repo: str, run_id: int) -> list[str]:
    """Return the runs-on labels of a run's first still-queued job (or its first
    job if none are queued). Empty when job metadata is not yet populated."""
    data = await _gh_json(
        "api",
        f"/repos/{org}/{repo}/actions/runs/{run_id}/jobs",
        default=None,
        timeout=20,
    )
    if not data or "jobs" not in data:
        return []
    jobs = data["jobs"]
    for job in jobs:
        if job.get("status") == "queued":
            return list(job.get("labels") or [])
    return list(jobs[0].get("labels") or []) if jobs else []


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
    online_label_sets: list[frozenset[str]] | None = None,
) -> list[StaleRun]:
    now = _get_now()
    if online_label_sets is None:
        online_label_sets = await fetch_online_runner_label_sets(org)

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

    # Pre-pass: queued runs whose required runs-on labels match NO online runner
    # will never start — regardless of branch or age — so flag them as
    # unroutable and exclude them from the branch/PR classification below to
    # avoid double-counting. Skipped entirely when the online-runner inventory
    # is unavailable (empty), so a transient API failure cannot trigger a cancel.
    unroutable_ids: set[int] = set()
    if online_label_sets:
        for run in unique_runs:
            if run.get("status") != "queued":
                continue
            raw_ts = run.get("created_at", "")
            try:
                created = _dt.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            age = now - created
            if age < min_age:
                continue
            run_id = run.get("id", 0)
            required = await required_labels_for_run(org, repo, run_id)
            if required and not is_routable(required, online_label_sets):
                stale.append(
                    StaleRun(
                        repo=repo,
                        run_id=run_id,
                        workflow=run.get("name", "?"),
                        branch=run.get("head_branch", "?") or "?",
                        created_at=raw_ts,
                        age_minutes=int(age.total_seconds() / 60),
                        reason=StaleReason.UNROUTABLE_LABEL.value,
                        reason_detail=(
                            f"queued job requires runs-on labels {sorted(set(required))} "
                            "but no online runner can satisfy them"
                        ),
                        safe_to_cancel=True,
                        url=f"https://github.com/{org}/{repo}/actions/runs/{run_id}",
                        html_url=run.get("html_url", ""),
                        event=run.get("event", ""),
                        head_sha=run.get("head_sha", ""),
                    )
                )
                unroutable_ids.add(run_id)

    for run in unique_runs:
        if run.get("id", 0) in unroutable_ids:
            continue
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
                    html_url=run.get("html_url", ""),
                    event=run.get("event", ""),
                    head_sha=run.get("head_sha", ""),
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
                        html_url=run.get("html_url", ""),
                        event=run.get("event", ""),
                        head_sha=run.get("head_sha", ""),
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
                        html_url=run.get("html_url", ""),
                        event=run.get("event", ""),
                        head_sha=run.get("head_sha", ""),
                        pull_request_number=pr_number,
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

        # Corroborating actor for agent classification (#934). GitHub run payloads
        # expose the triggering actor under `triggering_actor`/`actor` as a nested
        # object with a `login`; fall back gracefully when absent.
        actor_obj = run.get("triggering_actor") or run.get("actor") or {}
        actor_login = actor_obj.get("login") if isinstance(actor_obj, dict) else None

        run_reason, safe_to_cancel = classify_stale_run(branch, age_minutes, actor=actor_login)
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
                html_url=run.get("html_url", ""),
                event=run.get("event", ""),
                head_sha=run.get("head_sha", ""),
            )
        )

    return stale


def _single_pr_number_for_run(run: dict) -> int | None:
    """Return the run's single PR number, or None when the evidence is ambiguous."""
    pull_requests = run.get("pull_requests") or []
    if len(pull_requests) != 1:
        return None
    number = pull_requests[0].get("number")
    return number if isinstance(number, int) else None


async def classify_pr_head_supersession(org: str, repo: str, run: dict) -> dict:
    """Conservatively classify whether a queued PR run is superseded by a newer PR head.

    The classifier only returns ``pr_head_superseded=True`` when all evidence is
    exact: the workflow run has one PR number, the run has a head SHA, the PR is
    still open, GitHub returns the current PR head SHA, and those SHAs differ.
    Missing or ambiguous evidence is annotated but never treated as superseded.
    """
    result = {
        "pull_request_number": None,
        "current_pr_head_sha": "",
        "pr_head_superseded": False,
        "supersession_reason": "not-pr-run",
    }

    if run.get("event") not in {"pull_request", "pull_request_target"}:
        return result

    pr_number = _single_pr_number_for_run(run)
    result["pull_request_number"] = pr_number
    if pr_number is None:
        result["supersession_reason"] = "ambiguous-pr"
        return result

    run_head_sha = run.get("head_sha") or ""
    if not run_head_sha:
        result["supersession_reason"] = "missing-run-head-sha"
        return result

    pr = await _gh_json(
        "api",
        f"/repos/{org}/{repo}/pulls/{pr_number}",
        default={},
        timeout=15,
    )
    if not pr:
        result["supersession_reason"] = "pr-lookup-failed"
        return result

    if pr.get("state") != "open":
        result["supersession_reason"] = "pr-not-open"
        return result

    current_head_sha = ((pr.get("head") or {}).get("sha")) or ""
    result["current_pr_head_sha"] = current_head_sha
    if not current_head_sha:
        result["supersession_reason"] = "missing-current-pr-head-sha"
        return result

    if current_head_sha == run_head_sha:
        result["supersession_reason"] = "current-pr-head"
        return result

    result["pr_head_superseded"] = True
    result["supersession_reason"] = "pr-head-advanced"
    return result


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
    # Fetch the online-runner label inventory ONCE for the whole scan so every
    # repo shares it (used to flag jobs requesting labels no runner advertises).
    online_label_sets = await fetch_online_runner_label_sets(org)

    if repo:
        from security import validate_repo_slug

        validated_repo = validate_repo_slug(repo)
        min_age = timedelta(minutes=min_age_minutes)
        flat = await _queued_stale_for_repo(org, validated_repo, min_age, online_label_sets)
    else:
        repos = await list_all_repos(org)
        min_age = timedelta(minutes=min_age_minutes)
        sem = asyncio.Semaphore(_SCAN_CONCURRENCY)

        async def bounded(r: str) -> list[StaleRun]:
            async with sem:
                return await _queued_stale_for_repo(org, r, min_age, online_label_sets)

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
    *,
    dry_run: bool = False,
    superseded_only: bool = False,
    repo: str | None = None,
    workflow: str | None = None,
    reason: str | None = None,
    safe_only: bool = False,
    max_count: int | None = None,
) -> dict:
    """Find stale runs and optionally cancel them all.

    Returns a summary dict suitable for direct JSON serialisation.
    When *dry_run* is True the runs are listed but nothing is cancelled.
    """
    stale = await find_stale_runs(org, min_age_minutes, repo=repo, reason=reason)
    purge_candidates = [r for r in stale if r.pr_head_superseded] if superseded_only else stale
    if workflow:
        purge_candidates = [r for r in purge_candidates if r.workflow == workflow]
    if safe_only:
        purge_candidates = [r for r in purge_candidates if r.safe_to_cancel]
    if max_count is not None and max_count > 0:
        purge_candidates = purge_candidates[:max_count]
    cancelled_count = 0
    errors: list[str] = []

    if not dry_run and purge_candidates:
        sem = asyncio.Semaphore(_CANCEL_CONCURRENCY)

        async def bounded_cancel(run: StaleRun) -> None:
            nonlocal cancelled_count
            async with sem:
                if await _cancel_one(org, run):
                    cancelled_count += 1
                else:
                    errors.append(f"{run.repo}#{run.run_id}: {run.cancel_error}")

        await asyncio.gather(*[bounded_cancel(r) for r in purge_candidates])

    return {
        "org": org,
        "min_age_minutes": min_age_minutes,
        "dry_run": dry_run,
        "superseded_only": superseded_only,
        "stale_count": len(stale),
        "purge_candidate_count": len(purge_candidates),
        "cancelled_count": cancelled_count if not dry_run else 0,
        "errors": errors,
        "runs": [r.as_dict() for r in stale],
    }
