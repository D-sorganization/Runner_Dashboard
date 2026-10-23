"""Weekly summary for board meetings: delivery rollups + staff time/cost (#2123)."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from delivery_metrics.report import build_metrics_report
from staff.conversation_models import ActionProposalRecord
from staff.store import RunRecord
from usage_report import iso_week


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _in_window(moment: datetime, since: datetime, until: datetime) -> bool:
    return since <= moment < until


def dieter_wait_seconds(
    proposals: Sequence[ActionProposalRecord],
    *,
    since: datetime,
    until: datetime,
) -> float:
    """Seconds proposals waited for a human decision in ``[since, until)``."""
    total = 0.0
    for prop in proposals:
        if prop.state not in {"approved", "denied", "done"} or not prop.decided_at:
            continue
        decided = _parse_ts(prop.decided_at)
        if not _in_window(decided, since, until):
            continue
        actor = (prop.decided_by or "").lower()
        if not actor or actor.startswith("silent") or actor.startswith("system"):
            continue
        created = _parse_ts(prop.created_at)
        total += max(0.0, (decided - created).total_seconds())
    return total


def _staff_window(runs: Sequence[RunRecord], *, since: datetime, until: datetime) -> dict[str, Any]:
    picked = [r for r in runs if _in_window(_parse_ts(r.created_at), since, until)]
    cost = round(sum(r.cost_usd or 0.0 for r in picked), 4)
    return {
        "runs": len(picked),
        "cost_usd": cost,
        "runs_with_pr": sum(1 for r in picked if r.pr_number is not None),
        "merged_artifacts": sum(1 for r in picked if r.artifact == "pr_merged"),
    }


def render_weekly_summary_markdown(body: Mapping[str, Any]) -> str:
    delivery = body["delivery"]["summary"]
    staff = body["staff"]
    lines = [
        f"# Weekly delivery summary ({body['iso_week']})",
        "",
        f"Window: {body['since']} → {body['until']}",
        "",
        "## Delivery",
        f"- PRs tracked: {delivery['prs']}",
        f"- Merged: {delivery['merged']}",
        f"- Median CI rounds: {delivery.get('median_ci_rounds', '—')}",
        "",
        "## Staff & Dieter time",
        f"- Staff runs: {staff['runs']} (${staff['cost_usd']:.2f})",
        f"- Dieter wait (proposal decisions): {_fmt_duration(staff['dieter_wait_seconds'])}",
        f"- Cost per merged PR (median): {_fmt_usd(staff.get('cost_per_merged_pr', {}))}",
    ]
    return "\n".join(lines)


def _fmt_duration(seconds: float) -> str:
    if seconds <= 0:
        return "0m"
    minutes = int(seconds // 60)
    return f"{minutes}m"


def _fmt_usd(block: Mapping[str, Any]) -> str:
    median = block.get("median_usd")
    return f"${median:.2f}" if isinstance(median, (int, float)) else "—"


def build_weekly_summary(
    conn: sqlite3.Connection,
    *,
    since: datetime,
    until: datetime,
    runs: Sequence[RunRecord],
    proposals: Sequence[ActionProposalRecord],
    session_metrics: Mapping[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Stable JSON for staff consumers (Bob, board). Pure after inputs are loaded."""
    moment = now or datetime.now(UTC)
    delivery_report = build_metrics_report(conn, since=since, until=until, now=moment)
    staff = _staff_window(runs, since=since, until=until)
    staff["dieter_wait_seconds"] = dieter_wait_seconds(proposals, since=since, until=until)
    staff["proposal_decisions"] = sum(
        1 for p in proposals if p.decided_at and _in_window(_parse_ts(p.decided_at), since, until)
    )
    staff["cost_per_merged_pr"] = (session_metrics or {}).get("cost_per_merged_pr", {})
    body: dict[str, Any] = {
        "iso_week": iso_week(until - timedelta(hours=1)),
        "since": delivery_report["since"],
        "until": delivery_report["until"],
        "delivery": {
            "summary": delivery_report["summary"],
            "weekly_trends": delivery_report["weekly_trends"],
        },
        "staff": staff,
    }
    body["markdown"] = render_weekly_summary_markdown(body)
    return body
