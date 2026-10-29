"""Small, deterministic aggregation for dispatcher provenance and fallback metrics."""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any


class DispatcherRateLimitError(RuntimeError):
    """The dispatcher fallback ledger exceeded its per-principal write rate."""


def dispatcher_origin(principal: str, principal_type: str = "") -> str:
    """Classify provenance from authenticated identity/type, never request fields."""
    normalized = principal.removeprefix("principal:")
    if normalized == "agent-grok":
        return "grok"
    if normalized == "scheduler":
        return "schedule"
    if normalized == "watch_tick":
        return "watch_tick"
    if principal_type == "human":
        return "console"
    return "other"


def record_dispatcher_fallback(
    database: str | Path,
    *,
    reason: str,
    task_class: str,
    est_grok_turns: int,
    created_at: str,
    principal: str,
    idempotency_key: str,
    rate_window_since: str,
) -> dict[str, Any]:
    """Persist a fallback once per key and enforce a 60 writes/minute principal cap."""
    conn = sqlite3.connect(database, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("BEGIN IMMEDIATE")
        try:
            count = conn.execute(
                "SELECT count(*) FROM dispatcher_fallbacks WHERE principal = ? AND created_at >= ?",
                (principal, rate_window_since),
            ).fetchone()[0]
            if int(count) >= 60:
                raise DispatcherRateLimitError(principal)
            conn.execute(
                """INSERT OR IGNORE INTO dispatcher_fallbacks
                (idempotency_key, principal, reason, task_class, est_grok_turns, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (idempotency_key, principal, reason, task_class, est_grok_turns, created_at),
            )
            row = conn.execute(
                """SELECT reason, task_class, est_grok_turns, created_at
                FROM dispatcher_fallbacks WHERE principal = ? AND idempotency_key = ?""",
                (principal, idempotency_key),
            ).fetchone()
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return dict(row)
    finally:
        conn.close()


def dispatcher_rows_since(database: str | Path, since: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read only the bounded columns needed to aggregate dispatcher metrics."""
    conn = sqlite3.connect(database, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        runs = conn.execute(
            """SELECT requested_by, dispatched_by, dispatch_origin, created_at, status, repo,
            verification, target_kind, target_ref, artifact, pr_number
            FROM runs WHERE created_at >= ? AND retry_of = ''""",
            (since,),
        ).fetchall()
        fallbacks = conn.execute(
            """SELECT reason, task_class, est_grok_turns, created_at
            FROM dispatcher_fallbacks WHERE created_at >= ?""",
            (since,),
        ).fetchall()
        return [dict(row) for row in runs], [dict(row) for row in fallbacks]
    finally:
        conn.close()


def aggregate_dispatcher_metrics(
    runs: list[dict[str, Any]],
    fallbacks: list[dict[str, Any]],
    *,
    days: int,
    today: date | None = None,
) -> dict[str, Any]:
    """Build daily measured counts and explicitly labelled turn estimates."""
    end = today or datetime.now(UTC).date()
    first = end - timedelta(days=days - 1)
    date_range = [first + timedelta(days=offset) for offset in range(days)]
    run_days: dict[str, list[dict[str, Any]]] = defaultdict(list)
    fallback_days: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        run_days[str(run["created_at"])[:10]].append(run)
    for fallback in fallbacks:
        fallback_days[str(fallback["created_at"])[:10]].append(fallback)

    daily: list[dict[str, Any]] = []
    for day in date_range:
        key = day.isoformat()
        day_runs = run_days[key]
        day_fallbacks = fallback_days[key]
        grok_runs = sum(1 for run in day_runs if run.get("dispatch_origin") == "grok")
        fallback_count = len(day_fallbacks)
        denominator = grok_runs + fallback_count
        successes = [run for run in day_runs if run.get("status") == "succeeded"]
        verified = sum(1 for run in successes if run.get("verification") == "verified")
        reason_counts = dict(Counter(str(row["reason"]) for row in day_fallbacks))
        turns = sum(int(row["est_grok_turns"]) for row in day_fallbacks)
        issue_prs: dict[str, set[str]] = defaultdict(set)
        for run in day_runs:
            if run.get("target_kind") == "issue" and run.get("pr_number"):
                issue = f"{run.get('repo', '')}#{run.get('target_ref', '')}"
                issue_prs[issue].add(str(run["pr_number"]))
        duplicates = {issue: len(prs) - 1 for issue, prs in issue_prs.items() if len(prs) > 1}
        daily.append(
            {
                "date": key,
                "grok_dispatches": grok_runs,
                "grok_fallbacks": fallback_count,
                "dispatch_ratio": grok_runs / denominator if denominator else 0.0,
                "fallbacks_by_reason": reason_counts,
                "verified_success_rate": verified / len(successes) if successes else 0.0,
                "false_success_count": sum(1 for run in successes if run.get("verification") != "verified"),
                "duplicate_prs_by_issue": duplicates,
                "estimated_grok_turns_avoided": {"value": turns, "estimate": True},
            }
        )
    return {"days": daily}
