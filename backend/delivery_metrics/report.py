"""Compose DB reads and pure rollups (#2120)."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from collections.abc import Sequence

from delivery_metrics.attribution import (
    attach_attribution,
    build_attribution_index,
    dimension_groups,
)
from delivery_metrics.load import check_runs_for_heads, get_pull, list_pulls_between
from delivery_metrics.rollup import aggregate_window, build_pr_rollup, weekly_trends
from staff.store import RunRecord


def _checks_index(
    conn: sqlite3.Connection,
    pulls: tuple,
) -> dict[str, tuple]:
    by_repo: dict[str, set[str]] = defaultdict(set)
    for pr in pulls:
        if pr.head_sha:
            by_repo[pr.repo_full].add(pr.head_sha)
    out: dict[str, tuple] = {}
    for repo, heads in by_repo.items():
        for sha, runs in check_runs_for_heads(conn, repo, heads).items():
            out[sha] = runs
    return out


def _enrich_drill_down(
    pulls: tuple,
    pr_drill_down: list[dict[str, Any]],
    staff_runs: Sequence[RunRecord],
) -> list[dict[str, Any]]:
    merged_lookup = {(p.repo_full, p.number): p.merged for p in pulls}
    index = build_attribution_index(staff_runs)
    enriched: list[dict[str, Any]] = []
    for row in pr_drill_down:
        key = (str(row["repo_full"]), int(row["number"]))
        base = {**row, "merged": merged_lookup.get(key, False)}
        enriched.append(attach_attribution(base, index.get(key)))
    return enriched


def build_metrics_report(
    conn: sqlite3.Connection,
    *,
    since: datetime,
    until: datetime,
    repo_full: str | None = None,
    now: datetime | None = None,
    staff_runs: Sequence[RunRecord] = (),
) -> dict[str, Any]:
    """Summary + weekly trends for a window. Pure after load."""
    moment = now or datetime.now(UTC)
    pulls = list_pulls_between(conn, since=since, until=until, repo_full=repo_full)
    checks = _checks_index(conn, pulls)
    summary = aggregate_window(pulls, checks, now=moment)
    trends = weekly_trends(pulls, checks, now=moment)
    pr_drill_down = _enrich_drill_down(pulls, summary["pr_drill_down"], staff_runs)
    return {
        "since": since.isoformat().replace("+00:00", "Z"),
        "until": until.isoformat().replace("+00:00", "Z"),
        "repo_full": repo_full,
        "summary": {k: v for k, v in summary.items() if k != "pr_drill_down"},
        "weekly_trends": trends,
        "groupings": dimension_groups(pr_drill_down),
        "pr_drill_down": pr_drill_down,
    }


def pr_drill_down(
    conn: sqlite3.Connection,
    repo_full: str,
    number: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    pr = get_pull(conn, repo_full, number)
    if pr is None:
        return {}
    runs = check_runs_for_heads(conn, repo_full, {pr.head_sha}).get(pr.head_sha, ())
    rollup = build_pr_rollup(pr, runs, now=now or datetime.now(UTC))
    return {
        "pull": pr.to_dict(),
        "rollup": rollup.to_dict(),
        "check_runs": [r.to_dict() for r in runs],
    }
