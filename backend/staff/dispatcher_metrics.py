"""Small, deterministic aggregation for dispatcher provenance and fallback metrics."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any


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
