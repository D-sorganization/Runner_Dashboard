"""Review-flow metrics for the hub review-pool decision gate (#4250, epic #4251).

Honest scope: the panel's acceptance criteria assume a per-head-SHA verdict record
(#4249) that does not exist yet. This module only computes what is measurable from
data that already exists today:

  - queue depth: green, non-draft, unapproved open PRs (from ``gh pr list --json``)
  - verdict mix: approve/changes/escalate, parsed from ``RunRecord.outcome`` for
    ``code-reviewer`` runs (already produced by ``staff.review.parse_outcome``)
  - review runs by machine and provider, reusing ``dispatcher_metrics.dispatcher_rows_since``
  - provider availability, from ``provider_auth_preflight.provider_auth_cache_snapshot()``
  - a running count of providers skipped at dispatch time for a failed auth preflight

Deferred (needs #4249's per-head-SHA verdict contract, not guessed here):
  - review latency p50/p90, "green at head" -> verdict
  - reviewer disagreement rate, when two reviewers judged the same head
"""

from __future__ import annotations

import asyncio
import re
import threading
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from flow_metrics import RunGh, _default_run_gh, _gh_json
from staff.claim_guard import full_repo
from staff.dispatcher_metrics import dispatcher_rows_since
from staff.provider_auth_preflight import provider_auth_cache_snapshot
from staff.rate_limit import rate_limit_metrics_snapshot
from staff.review import REVIEWER_ROLE, review_scope_repos

_QUEUE_PR_FIELDS = "number,isDraft,reviewDecision,statusCheckRollup"
_QUEUE_LOOKBACK_LIMIT = 100
METRICS_WINDOW_DAYS = 7

# Mirrors the vocabulary ``staff.review.parse_review_verdict`` already normalises
# a run's ``outcome`` column to (``"review <verdict> #<pr>[ (same-provider)]"``);
# this does not re-parse raw comment text, only the already-parsed outcome string.
_OUTCOME_VERDICT_RE = re.compile(r"^review\s+(approve|changes|escalate)\b", re.IGNORECASE)

VERDICTS: tuple[str, ...] = ("approve", "changes", "escalate")

_skip_lock = threading.Lock()
_skip_counts: dict[str, int] = defaultdict(int)


def is_green_checks(status_check_rollup: Sequence[Any] | None) -> bool:
    """True when every check in ``gh pr list --json statusCheckRollup`` passed.

    A PR with no checks at all is not "green at head" (nothing to measure yet).
    """
    rollup = list(status_check_rollup or [])
    if not rollup:
        return False
    ok_conclusions = {"SUCCESS", "NEUTRAL", "SKIPPED"}
    for item in rollup:
        if not isinstance(item, dict):
            return False
        conclusion = str(item.get("conclusion") or "").upper()
        state = str(item.get("state") or "").upper()
        if conclusion and conclusion not in ok_conclusions:
            return False
        if not conclusion and state not in ("SUCCESS",):
            return False
    return True


def is_queue_candidate(pr: dict[str, Any]) -> bool:
    """Green, non-draft, unapproved: the queue-depth definition from the issue."""
    if bool(pr.get("isDraft")):
        return False
    if str(pr.get("reviewDecision") or "").upper() == "APPROVED":
        return False
    return is_green_checks(pr.get("statusCheckRollup"))


def queue_depth(prs: Sequence[dict[str, Any]]) -> int:
    """Count of green, non-draft, unapproved PRs in ``prs``."""
    return sum(1 for pr in prs if is_queue_candidate(pr))


def verdict_mix(runs: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Approve/changes/escalate counts from ``code-reviewer`` runs' already-parsed outcome."""
    mix = {verdict: 0 for verdict in VERDICTS}
    for run in runs:
        if str(run.get("role") or "") != REVIEWER_ROLE:
            continue
        match = _OUTCOME_VERDICT_RE.match(str(run.get("outcome") or ""))
        if match:
            mix[match.group(1).lower()] += 1
    return mix


def review_runs_by_machine_provider(runs: Sequence[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Count of ``code-reviewer`` runs grouped by machine, then provider."""
    by_machine: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for run in runs:
        if str(run.get("role") or "") != REVIEWER_ROLE:
            continue
        machine = str(run.get("machine") or "unknown")
        provider = str(run.get("provider") or "unknown")
        by_machine[machine][provider] += 1
    return {machine: dict(providers) for machine, providers in by_machine.items()}


def unhealthy_providers(snapshot: dict[str, Any]) -> frozenset[str]:
    """Provider ids whose auth preflight is not ``ok`` in a ``provider_auth_cache_snapshot()``."""
    providers = snapshot.get("providers") or {}
    return frozenset(pid for pid, row in providers.items() if isinstance(row, dict) and row.get("status") != "ok")


def provider_availability_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Flat rows for the endpoint: one per probed provider, with its latest status."""
    providers = snapshot.get("providers") or {}
    return [
        {"provider": pid, "status": row.get("status", "unknown"), "detail": row.get("detail", "")}
        for pid, row in providers.items()
        if isinstance(row, dict)
    ]


def record_provider_skip(provider: str) -> None:
    """Record that review dispatch fail-fast skipped ``provider`` (auth preflight failed)."""
    with _skip_lock:
        _skip_counts[str(provider)] += 1


def provider_skip_counts_snapshot() -> dict[str, int]:
    """Snapshot of providers skipped at dispatch time since process start or last reset."""
    with _skip_lock:
        return dict(_skip_counts)


def reset_provider_skip_counts() -> None:
    """Test-only reset of the in-process skip counter."""
    with _skip_lock:
        _skip_counts.clear()


def select_available_reviewer(
    candidates: Sequence[str],
    unhealthy: frozenset[str],
) -> tuple[list[str], list[str]]:
    """Split ``candidates`` into (healthy, skipped) by auth-preflight status.

    Pre: ``candidates`` is the role's configured provider list (never empty for a
    loaded role). Post: every skipped provider is recorded via ``record_provider_skip``;
    when every candidate is unhealthy, ``healthy`` falls back to all of ``candidates``
    so dispatch is never silently blocked by this fail-fast check alone.
    """
    skipped = [p for p in candidates if p in unhealthy]
    for provider in skipped:
        record_provider_skip(provider)
    healthy = [p for p in candidates if p not in unhealthy]
    return (healthy or list(candidates)), skipped


def summary_lines(metrics: dict[str, Any]) -> dict[str, str]:
    """One line each for queue depth, p90 latency and throttled time (Barb / morning brief)."""
    depth = metrics.get("queue_depth", 0)
    throttled = (metrics.get("rate_limit") or {}).get("throttled_seconds", 0.0)
    count_429 = (metrics.get("rate_limit") or {}).get("count_429", 0)
    return {
        "queue_depth": f"{depth} green, non-draft, unapproved PR(s) queued for review.",
        "review_latency_p90": "Deferred: needs the #4249 per-head-SHA verdict contract.",
        "throttled_time": f"{count_429} dispatch 429(s), {throttled:.0f}s throttled since process start.",
    }


def build_review_metrics_payload(
    *,
    queue_prs_by_repo: dict[str, Sequence[dict[str, Any]]],
    review_runs: Sequence[dict[str, Any]],
    provider_auth_snapshot: dict[str, Any],
    rate_limit_snapshot: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the full ``GET /api/v1/staff/review/metrics`` response from fetched inputs."""
    depth_by_repo = {repo: queue_depth(prs) for repo, prs in queue_prs_by_repo.items()}
    total_depth = sum(depth_by_repo.values())
    payload: dict[str, Any] = {
        "generated_at": (now or datetime.now(UTC)).isoformat().replace("+00:00", "Z"),
        "queue_depth": total_depth,
        "queue_depth_by_repo": depth_by_repo,
        "verdict_mix": verdict_mix(review_runs),
        "review_runs_by_machine_provider": review_runs_by_machine_provider(review_runs),
        "provider_availability": provider_availability_rows(provider_auth_snapshot),
        "provider_skips": provider_skip_counts_snapshot(),
        "rate_limit": dict(rate_limit_snapshot),
        "deferred": {
            "review_latency_p50_p90": "Needs the per-head-SHA verdict contract from #4249; not implemented here.",
            "reviewer_disagreement_rate": "Needs the per-head-SHA verdict contract from #4249; not implemented here.",
        },
    }
    payload["summary"] = summary_lines(payload)
    return payload


async def _fetch_queue_prs(run_gh: RunGh, repo_full: str) -> tuple[list[dict[str, Any]], str | None]:
    """One bounded ``gh pr list`` call per repo; the fields queue_depth() needs, nothing else."""
    payload, err = await _gh_json(
        run_gh,
        [
            "pr",
            "list",
            "--repo",
            repo_full,
            "--state",
            "open",
            "--json",
            _QUEUE_PR_FIELDS,
            "--limit",
            str(_QUEUE_LOOKBACK_LIMIT),
        ],
    )
    if err:
        return [], err
    if not isinstance(payload, list):
        return [], "unexpected pr list response"
    return [pr for pr in payload if isinstance(pr, dict)], None


async def collect_review_metrics(
    *,
    repo: str | None = None,
    database: str | Path,
    run_gh: RunGh | None = None,
    days: int = METRICS_WINDOW_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch and aggregate everything ``GET /api/v1/staff/review/metrics`` needs.

    Pre: ``database`` is a path to the staff runs store. Post: never raises on a GitHub
    or auth-preflight failure; such repos are omitted from ``queue_depth_by_repo`` and
    ``errors`` names them, so one bad call never blanks the whole payload.
    """
    ref = now or datetime.now(UTC)
    runner = run_gh or _default_run_gh
    repos = [repo] if repo else sorted(review_scope_repos())

    results = await asyncio.gather(*(_fetch_queue_prs(runner, full_repo(r)) for r in repos))
    queue_prs_by_repo: dict[str, Sequence[dict[str, Any]]] = {}
    errors: list[str] = []
    for repo_name, (prs, err) in zip(repos, results, strict=True):
        if err:
            errors.append(f"{repo_name}: {err}")
            continue
        queue_prs_by_repo[repo_name] = prs

    since = (ref - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    review_runs, _fallbacks = dispatcher_rows_since(database, since)

    payload = build_review_metrics_payload(
        queue_prs_by_repo=queue_prs_by_repo,
        review_runs=review_runs,
        provider_auth_snapshot=provider_auth_cache_snapshot(),
        rate_limit_snapshot=rate_limit_metrics_snapshot(),
        now=ref,
    )
    if errors:
        payload["errors"] = errors
    return payload
