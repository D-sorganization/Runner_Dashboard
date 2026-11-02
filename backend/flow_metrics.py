"""Per-repository flow metrics for PD-4 / board priority #6 (#3818).

Computes from live GitHub data (via ``gh``):
  - open PR age distribution and count over the 72-hour rule threshold
  - PR cycle time (created → merged) for recently merged PRs
  - weekly net issue burn-down (issues closed minus opened in the last 7 days)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from cache_utils import Cache
from coordination.board import fleet_repos
from staff.claim_guard import full_repo

log = logging.getLogger("dashboard.flow_metrics")

PR_AGE_RULE_HOURS = 72.0
WEEKLY_DAYS = 7
CYCLE_LOOKBACK_DAYS = 14
MERGED_PR_PAGE = 100
_GH_TIMEOUT = 30

_cache = Cache(name="flow_metrics", deepcopy_on_set=True)
_CACHE_TTL = 120.0

RunGh = Callable[[list[str], int], Awaitable[tuple[int, str, str]]]


async def _default_run_gh(args: list[str], timeout: int = _GH_TIMEOUT) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return 127, "", "gh CLI not found"
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")
    except (TimeoutError, asyncio.TimeoutError):  # noqa: UP041
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(ProcessLookupError):
            await proc.wait()
        return -1, "", "gh command timed out"


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, AttributeError):
        return None


def _age_hours(created_at: str, *, now: datetime | None = None) -> float:
    created = _parse_ts(created_at)
    if created is None:
        return 0.0
    ref = now or datetime.now(tz=UTC)
    return round((ref - created).total_seconds() / 3600.0, 1)


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    rank = (len(ordered) - 1) * (pct / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return round(ordered[low] * (1.0 - weight) + ordered[high] * weight, 1)


def flag_aging_open_prs(pulls: Sequence[Mapping[str, Any]], *, rule_hours: float = PR_AGE_RULE_HOURS) -> list[dict[str, Any]]:
    """Open PRs in ``pulls`` older than ``rule_hours`` (for dashboard flags)."""
    flagged: list[dict[str, Any]] = []
    for pr in pulls:
        if not isinstance(pr, dict):
            continue
        age = open_pr_age_hours(str(pr.get("created_at") or ""))
        if age <= rule_hours:
            continue
        flagged.append(
            {
                "number": pr.get("number"),
                "url": str(pr.get("html_url") or ""),
                "age_hours": age,
                "rule_hours": rule_hours,
                "action": "ci_diagnosis_or_iterate_or_close",
            }
        )
    flagged.sort(key=lambda row: float(row["age_hours"]), reverse=True)
    return flagged


def summarize_open_pr_ages(ages: Sequence[float], *, rule_hours: float = PR_AGE_RULE_HOURS) -> dict[str, Any]:
    """Aggregate open-PR age hours for one repository."""
    if not ages:
        return {
            "count": 0,
            "p50_hours": 0.0,
            "p90_hours": 0.0,
            "max_hours": 0.0,
            "over_rule_hours": 0,
            "rule_hours": rule_hours,
        }
    return {
        "count": len(ages),
        "p50_hours": percentile(ages, 50),
        "p90_hours": percentile(ages, 90),
        "max_hours": round(max(ages), 1),
        "over_rule_hours": sum(1 for age in ages if age > rule_hours),
        "rule_hours": rule_hours,
    }


def summarize_cycle_times(hours: Sequence[float]) -> dict[str, Any]:
    if not hours:
        return {"sample_size": 0, "p50_hours": 0.0, "p90_hours": 0.0}
    return {
        "sample_size": len(hours),
        "p50_hours": percentile(hours, 50),
        "p90_hours": percentile(hours, 90),
    }


def weekly_issue_burn(opened: int, closed: int) -> dict[str, Any]:
    return {
        "window_days": WEEKLY_DAYS,
        "opened": opened,
        "closed": closed,
        "net": closed - opened,
    }


def build_repo_row(
    repo: str,
    *,
    open_pr_ages: Sequence[float],
    cycle_hours: Sequence[float],
    opened_issues: int,
    closed_issues: int,
    aging_prs: Sequence[dict[str, Any]] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    short = repo.rsplit("/", 1)[-1]
    row: dict[str, Any] = {
        "repo": short,
        "repo_full": repo,
        "open_pr_age": summarize_open_pr_ages(open_pr_ages),
        "pr_cycle_time": summarize_cycle_times(cycle_hours),
        "weekly_issue_burn": weekly_issue_burn(opened_issues, closed_issues),
        "aging_prs": list(aging_prs or ()),
    }
    if error:
        row["error"] = error
    return row


async def _gh_json(run_gh: RunGh, args: list[str]) -> tuple[Any | None, str | None]:
    code, stdout, stderr = await run_gh(args, _GH_TIMEOUT)
    if code != 0:
        return None, (stderr or stdout or f"gh exit {code}").strip()[:300]
    try:
        return json.loads(stdout), None
    except json.JSONDecodeError as exc:
        return None, f"JSON decode error: {exc}"


def _week_range(now: datetime | None = None) -> tuple[str, str]:
    end = (now or datetime.now(tz=UTC)).date()
    start = end - timedelta(days=WEEKLY_DAYS - 1)
    return start.isoformat(), end.isoformat()


async def _search_issue_count(run_gh: RunGh, repo_full: str, qualifier: str) -> tuple[int, str | None]:
    start, end = _week_range()
    query = quote(f"repo:{repo_full} is:issue {qualifier}:{start}..{end}", safe="")
    path = f"/search/issues?q={query}&per_page=1"
    payload, err = await _gh_json(run_gh, ["api", path])
    if err:
        return 0, err
    if isinstance(payload, dict) and isinstance(payload.get("total_count"), int):
        return int(payload["total_count"]), None
    return 0, "unexpected search response"


def open_pr_age_hours(created_at: str, *, now: datetime | None = None) -> float:
    """Hours since ``created_at`` (public helper for other modules)."""
    return _age_hours(created_at, now=now)


async def _fetch_open_pr_ages(
    run_gh: RunGh, repo_full: str, *, now: datetime | None = None
) -> tuple[list[float], list[dict[str, Any]], str | None]:
    payload, err = await _gh_json(run_gh, ["api", f"/repos/{repo_full}/pulls?state=open&per_page=100"])
    if err:
        return [], [], err
    if not isinstance(payload, list):
        return [], [], "unexpected pulls response"
    pulls = [pr for pr in payload if isinstance(pr, dict)]
    ages = [_age_hours(str(pr.get("created_at") or ""), now=now) for pr in pulls]
    return ages, flag_aging_open_prs(pulls), None


async def _fetch_cycle_hours(
    run_gh: RunGh, repo_full: str, *, now: datetime | None = None
) -> tuple[list[float], str | None]:
    payload, err = await _gh_json(
        run_gh,
        ["api", f"/repos/{repo_full}/pulls?state=closed&sort=updated&direction=desc&per_page={MERGED_PR_PAGE}"],
    )
    if err:
        return [], err
    if not isinstance(payload, list):
        return [], "unexpected closed pulls response"
    ref = now or datetime.now(tz=UTC)
    cutoff = ref - timedelta(days=CYCLE_LOOKBACK_DAYS)
    hours: list[float] = []
    for pr in payload:
        if not isinstance(pr, dict) or not pr.get("merged_at"):
            continue
        merged = _parse_ts(str(pr["merged_at"]))
        created = _parse_ts(str(pr.get("created_at") or ""))
        if merged is None or created is None or merged < cutoff:
            continue
        hours.append(round((merged - created).total_seconds() / 3600.0, 1))
    return hours, None


async def collect_repo_metrics(
    repo_short: str,
    *,
    run_gh: RunGh | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch and aggregate metrics for one fleet repository (short name)."""
    runner = run_gh or _default_run_gh
    repo_full = full_repo(repo_short)
    open_ages, aging_prs, open_err = await _fetch_open_pr_ages(runner, repo_full, now=now)
    cycle_hours, cycle_err = await _fetch_cycle_hours(runner, repo_full, now=now)
    opened, opened_err = await _search_issue_count(runner, repo_full, "created")
    closed, closed_err = await _search_issue_count(runner, repo_full, "closed")
    errors = [e for e in (open_err, cycle_err, opened_err, closed_err) if e]
    return build_repo_row(
        repo_full,
        open_pr_ages=open_ages,
        cycle_hours=cycle_hours,
        opened_issues=opened,
        closed_issues=closed,
        aging_prs=aging_prs,
        error="; ".join(errors) if errors else None,
    )


async def collect_flow_metrics(
    *,
    repo: str | None = None,
    run_gh: RunGh | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate flow metrics across fleet repos (optional single-repo filter)."""
    ref = now or datetime.now(tz=UTC)
    repos = [repo] if repo else fleet_repos()
    cache_key = f"flow|{','.join(sorted(repos))}|{ref.date().isoformat()}"
    cached = _cache.get(cache_key, _CACHE_TTL)
    if cached is not None:
        return cached

    tasks = [collect_repo_metrics(one, run_gh=run_gh, now=ref) for one in repos]
    rows = await asyncio.gather(*tasks)
    complete = all(not row.get("error") for row in rows)
    payload = {
        "available": True,
        "complete": complete,
        "repos": rows,
        "generated_at": ref.isoformat().replace("+00:00", "Z"),
        "pr_age_rule_hours": PR_AGE_RULE_HOURS,
    }
    _cache.set(cache_key, payload)
    return payload
