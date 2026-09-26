"""Agent outcome scorecard (WP-1.2, #1517): is staff output any good, per role/provider/repo?

``aggregate`` is pure: run records plus per-PR facts in, scorecard rows out. The only
GitHub reads are for PRs a run already recorded (``RunRecord.pr_number``, set by WP-1.1
verification); nothing sweeps the fleet. At most :data:`MAX_PRS` PRs are read per
request, :data:`CONCURRENCY` at a time, each cached for the dashboard TTL.

A rate whose denominator is zero is ``None`` (no data), never ``0.0``. A PR whose facts
could not be read counts in ``prs_unknown`` and in no rate.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import gh_client
from cache_utils import cache_get, cache_set
from dashboard_config import DEFAULT_CACHE_TTL
from staff.store import RunRecord
from staff.verification import ci_state
from staff.workspace import ORG

log = logging.getLogger("dashboard.staff.outcomes")

GROUP_BY: dict[str, Callable[[RunRecord], str]] = {
    "role": lambda r: r.role,
    "provider": lambda r: r.provider,
    "repo": lambda r: r.repo,
}
DEFAULT_WINDOW = timedelta(days=14)
FIX_WINDOW = timedelta(hours=48)
MAX_PRS = 50
CONCURRENCY = 4
# Verdicts that mean a run was checked; ``not_applicable`` and unchecked runs are not.
_CHECKED = frozenset({"verified", "failed", "unverified"})

PrKey = tuple[str, int]


@dataclass(frozen=True)
class PrFact:
    """What GitHub says about one PR. ``None`` in a flag means it could not be decided."""

    state: str  # merged | closed_unmerged | open
    ci_first_pass: bool | None = None
    followup_fix: bool | None = None


def default_since(now: datetime | None = None) -> str:
    """Midnight UTC, DEFAULT_WINDOW ago."""
    start = (now or datetime.now(UTC)) - DEFAULT_WINDOW
    return start.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def full_repo(repo: str) -> str:
    return repo if "/" in repo else f"{ORG}/{repo}"


def pr_key(rec: RunRecord) -> PrKey | None:
    """The PR a run recorded, or None (no PR, or no repo to find it in)."""
    if rec.pr_number is None or not rec.repo:
        return None
    return (full_repo(rec.repo), int(rec.pr_number))


def rate(numerator: float, denominator: float) -> float | None:
    """numerator / denominator, or None when there is nothing to divide by."""
    return round(numerator / denominator, 4) if denominator > 0 else None


def _row(key: str, runs: Sequence[RunRecord], facts: Mapping[PrKey, PrFact]) -> dict[str, Any]:
    keys = {k for k in (pr_key(r) for r in runs) if k is not None}
    known = [facts[k] for k in keys if k in facts]
    merged = sum(f.state == "merged" for f in known)
    closed = sum(f.state == "closed_unmerged" for f in known)
    ci_known = [f.ci_first_pass for f in known if f.ci_first_pass is not None]
    fix_known = [f.followup_fix for f in known if f.state == "merged" and f.followup_fix is not None]
    verified = sum(r.verification == "verified" for r in runs)
    cost = round(sum(r.cost_usd or 0.0 for r in runs), 4)
    return {
        "key": key,
        "runs": len(runs),
        "succeeded": sum(r.status == "succeeded" for r in runs),
        "verified": verified,
        "failed_verification": sum(r.verification == "failed" for r in runs),
        "cost_usd": cost,
        "prs": len(keys),
        "prs_unknown": len(keys) - len(known),
        "merged": merged,
        "closed_unmerged": closed,
        "open": sum(f.state == "open" for f in known),
        "ci_first_pass": sum(ci_known),
        "fix_within_48h": sum(fix_known),
        "merge_rate": rate(merged, merged + closed),
        "verified_rate": rate(verified, sum(r.verification in _CHECKED for r in runs)),
        "ci_first_pass_rate": rate(sum(ci_known), len(ci_known)),
        "fix_within_48h_rate": rate(sum(fix_known), len(fix_known)),
        "cost_per_merged_pr": rate(cost, merged),
    }


def aggregate(runs: Sequence[RunRecord], facts: Mapping[PrKey, PrFact], *, group_by: str) -> dict[str, Any]:
    """Scorecard rows per ``group_by`` value (sorted) plus a totals row.

    Pre: ``group_by`` is a key of GROUP_BY. Pure: no I/O.
    """
    if group_by not in GROUP_BY:
        raise ValueError(f"group_by must be one of {sorted(GROUP_BY)}")
    groups: dict[str, list[RunRecord]] = {}
    for rec in runs:
        groups.setdefault(GROUP_BY[group_by](rec), []).append(rec)
    return {
        "group_by": group_by,
        "rows": [_row(k, v, facts) for k, v in sorted(groups.items())],
        "totals": _row("totals", runs, facts),
    }


def linked_issue(rec: RunRecord) -> int | None:
    """The issue a run worked on, when its target was an issue."""
    if rec.target_kind != "issue":
        return None
    digits = rec.target_ref.lstrip("#")
    return int(digits) if digits.isdigit() else None


def prs_to_read(runs: Iterable[RunRecord]) -> tuple[dict[PrKey, int | None], bool]:
    """The distinct PRs to read (each with the issue it worked on), newest runs first.

    Returns (PR key → linked issue, truncated). Runs without a PR contribute nothing.
    """
    wanted: dict[PrKey, int | None] = {}
    for rec in sorted(runs, key=lambda r: r.created_at, reverse=True):
        key = pr_key(rec)
        if key is None or key in wanted:
            continue
        if len(wanted) >= MAX_PRS:
            return wanted, True
        wanted[key] = linked_issue(rec)
    return wanted, False


async def _followup_fix(repo: str, number: int, issue: int, merged_at: str) -> bool:
    """Whether another PR mentioning ``issue`` merged within FIX_WINDOW after this one."""
    start = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
    end = (start + FIX_WINDOW).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = f"repo:{repo} is:pr is:merged {issue} merged:{merged_at}..{end}"
    found = await gh_client.get(f"/search/issues?q={quote(q)}&per_page=10")
    return any(int(item.get("number", 0)) != number for item in found.get("items") or [])


async def read_pr_fact(repo: str, number: int, issue: int | None) -> PrFact:
    """PR state, CI on its first commit, and (merged, with a known issue) a follow-up fix.

    Raises gh_client.GhClientError when the PR itself cannot be read; a CI or follow-up
    lookup that fails leaves that flag None.
    """
    pr = await gh_client.get(f"/repos/{repo}/pulls/{number}")
    state = "merged" if pr.get("merged_at") else ("closed_unmerged" if pr.get("state") == "closed" else "open")
    ci: bool | None = None
    try:
        commits = await gh_client.get(f"/repos/{repo}/pulls/{number}/commits?per_page=1")
        if commits:
            checks = await gh_client.get(f"/repos/{repo}/commits/{commits[0]['sha']}/check-runs?per_page=100")
            verdict = ci_state(checks.get("check_runs") or [])
            ci = None if verdict == "pending" else verdict == "green"
    except gh_client.GhClientError as exc:
        log.info("outcomes: CI of %s#%s unknown: %s", repo, number, exc)
    fix: bool | None = None
    if state == "merged" and issue is not None:
        try:
            fix = await _followup_fix(repo, number, issue, str(pr["merged_at"]))
        except gh_client.GhClientError as exc:
            log.info("outcomes: follow-up of %s#%s unknown: %s", repo, number, exc)
    return PrFact(state=state, ci_first_pass=ci, followup_fix=fix)


async def read_pr_facts(wanted: Mapping[PrKey, int | None]) -> dict[PrKey, PrFact]:
    """Facts for each wanted PR, cached, CONCURRENCY at a time. Unreadable PRs are omitted."""
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(key: PrKey, issue: int | None) -> tuple[PrKey, PrFact | None]:
        cache_key = f"staff:outcomes:pr:{key[0]}#{key[1]}"
        cached = cache_get(cache_key, DEFAULT_CACHE_TTL)
        if isinstance(cached, PrFact):
            return key, cached
        async with gate:
            try:
                fact = await read_pr_fact(key[0], key[1], issue)
            except gh_client.GhClientError as exc:
                log.warning("outcomes: cannot read %s#%s: %s", key[0], key[1], exc)
                return key, None
        cache_set(cache_key, fact)
        return key, fact

    results = await asyncio.gather(*(one(k, i) for k, i in wanted.items()))
    return {k: f for k, f in results if f is not None}
