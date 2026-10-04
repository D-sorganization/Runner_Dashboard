"""Weekly agent usage report from RD-4 session metrics (USE-1, issue #1865).

RD-4 (#1858 / #1849) collects cost per merged PR, post-PR wake-ups and spend,
and startup context, but nobody reviews them. This module renders those
numbers into one Markdown comment per ISO week and posts it to a single issue
in Repository_Management. No LLM is involved: every figure comes from
:meth:`session_telemetry.SessionTelemetryStore.get_metrics_window`.

The job is idempotent. The issue is found by title and the ``usage-report``
label (created once if missing; pinning it is a one-time manual step because
GitHub only exposes pinning through GraphQL). Each comment starts with a
per-week marker, so re-running the job in the same week updates that comment
instead of adding another.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal, Protocol

log = logging.getLogger("dashboard.usage_report")

REPORT_ISSUE_TITLE: Final = "Weekly agent usage report"
REPORT_LABEL: Final = "usage-report"
DEFAULT_REPORT_REPOSITORY: Final = "D-sorganization/Repository_Management"
REPORT_ISSUE_BODY: Final = (
    "One comment per ISO week, rendered by Runner Dashboard from `/api/usage/session-metrics` "
    "(Runner_Dashboard#1865). Re-runs in the same week update that week's comment."
)
REPORT_ENABLED_ENV: Final = "USAGE_REPORT_WEEKLY_ENABLED"
REPORT_INTERVAL_SECONDS: Final = 6 * 3600
_WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

Action = Literal["created", "updated", "unchanged"]


# ─── Rendering ───────────────────────────────────────────────────────────────


def iso_week(now: datetime) -> str:
    """ISO week label, e.g. ``2026-W40``."""
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def week_marker(week: str) -> str:
    """Hidden marker that identifies the comment for ``week``."""
    if not _WEEK_RE.match(week):
        raise ValueError(f"week must look like 2026-W40; got {week!r}")
    return f"<!-- usage-report:week={week} -->"


def _num(section: Mapping[str, Any] | None, key: str) -> float | None:
    if not section or key not in section:
        return None
    return float(section[key])


def _fmt(value: float | None, kind: str) -> str:
    if value is None:
        return "—"
    if kind == "usd":
        return f"${value:,.2f}"
    return f"{value:,.0f}"


def _delta(current: float | None, previous: float | None, kind: str) -> str:
    if current is None or previous is None:
        return "n/a"
    diff = current - previous
    sign = "+" if diff >= 0 else "-"
    amount = f"${abs(diff):,.2f}" if kind == "usd" else f"{abs(diff):,.0f}"
    if previous == 0:
        return f"{sign}{amount}"
    return f"{sign}{amount} ({diff / previous:+.0%})"


def _row(label: str, current: float | None, previous: float | None, kind: str) -> str:
    return f"| {label} | {_fmt(current, kind)} | {_fmt(previous, kind)} | {_delta(current, previous, kind)} |"


def _section(metrics: Mapping[str, Any] | None, name: str) -> Mapping[str, Any] | None:
    if metrics is None:
        return None
    value = metrics.get(name)
    return value if isinstance(value, Mapping) else None


def _summary_rows(current: Mapping[str, Any], previous: Mapping[str, Any] | None) -> list[str]:
    spec = (
        ("Sessions", None, "total_sessions", "count"),
        ("Cost per merged PR (median)", "cost_per_merged_pr", "median_usd", "usd"),
        ("Cost per merged PR (p90)", "cost_per_merged_pr", "p90_usd", "usd"),
        ("Merged PRs measured", "cost_per_merged_pr", "count", "count"),
        ("Post-PR spend (total)", "cost_after_pr", "total_usd", "usd"),
        ("Post-PR spend (p90)", "cost_after_pr", "p90_usd", "usd"),
        ("Sessions with >3 post-PR wake-ups", "post_pr_wakeups", "sessions_gt_3_count", "count"),
        ("Sessions with any post-PR wake-up", "post_pr_wakeups", "sessions_gt_0_count", "count"),
    )
    rows = ["| Metric | This week | Last week | Change |", "| --- | --- | --- | --- |"]
    for label, section, key, kind in spec:
        if section is None:
            cur, prev = _num(current, key), _num(previous, key)
        else:
            cur, prev = _num(_section(current, section), key), _num(_section(previous, section), key)
        rows.append(_row(label, cur, prev, kind))
    return rows


def _context_rows(current: Mapping[str, Any], previous: Mapping[str, Any] | None) -> list[str]:
    cur_envs = _section(current, "startup_context_by_environment") or {}
    prev_envs = _section(previous, "startup_context_by_environment") or {}
    if not cur_envs:
        return ["_No sessions reported startup context this week._"]
    rows = [
        "| Environment | Sessions | Median tokens | p90 tokens | Median change |",
        "| --- | --- | --- | --- | --- |",
    ]
    for env in sorted(cur_envs):
        cur = cur_envs[env]
        prev = prev_envs.get(env)
        median = _num(cur, "median")
        rows.append(
            f"| {env} | {_fmt(_num(cur, 'count'), 'count')} | {_fmt(median, 'count')} | "
            f"{_fmt(_num(cur, 'p90'), 'count')} | {_delta(median, _num(prev, 'median'), 'count')} |"
        )
    return rows


def _queue_rows(merge_queue: Mapping[str, Any] | None) -> list[str]:
    if not merge_queue:
        return ["_Merge-queue wait time and timeouts: not reported (no metrics source is wired yet)._"]
    return [
        "| Merge queue | Value |",
        "| --- | --- |",
        f"| Queue wait (median) | {float(merge_queue['wait_median_min']):.1f} min |",
        f"| Queue wait (p90) | {float(merge_queue['wait_p90_min']):.1f} min |",
        f"| Queue timeouts | {int(merge_queue['timeouts'])} |",
    ]


def render_weekly_usage_report(
    *,
    week: str,
    current: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
    merge_queue: Mapping[str, Any] | None = None,
) -> str:
    """Render the weekly report as Markdown.

    Precondition: ``week`` is an ISO week label; ``current``/``previous`` are
    aggregates from ``summarize_sessions`` (``previous`` may be ``None``).
    Postcondition: the body starts with :func:`week_marker` and is a pure
    function of its inputs (deterministic).
    """
    marker = week_marker(week)
    lines = [
        marker,
        f"## Weekly agent usage report — {week}",
        "",
        "Rendered from Runner Dashboard session telemetry (RD-4); no LLM involved.",
        "",
        *_summary_rows(current, previous),
        "",
        "### Startup context per environment",
        "",
        *_context_rows(current, previous),
        "",
        "### Merge queue",
        "",
        *_queue_rows(merge_queue),
    ]
    return "\n".join(lines) + "\n"


def build_weekly_report(store: Any, *, now: datetime | None = None) -> tuple[str, str]:
    """Render this week's report from a telemetry store: ``(week, body)``."""
    moment = now or datetime.now(UTC)
    current = store.get_metrics_window(days=7, offset_days=0, now=moment)
    previous = store.get_metrics_window(days=7, offset_days=7, now=moment)
    week = iso_week(moment)
    return week, render_weekly_usage_report(week=week, current=current, previous=previous)


# ─── Posting ─────────────────────────────────────────────────────────────────


class IssueCommentClient(Protocol):
    """The GitHub issue operations the report job needs."""

    async def find_open_issue(self, repository: str, *, title: str, label: str) -> int | None: ...

    async def create_issue(self, repository: str, *, title: str, body: str, labels: list[str]) -> int: ...

    async def list_issue_comments(self, repository: str, number: int) -> list[dict[str, Any]]: ...

    async def create_comment(self, repository: str, number: int, body: str) -> int: ...

    async def update_comment(self, repository: str, comment_id: int, body: str) -> None: ...


@dataclass(frozen=True, slots=True)
class WeeklyReportResult:
    """Outcome of one posting run."""

    repository: str
    issue_number: int
    comment_id: int
    week: str
    action: Action

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "issue_number": self.issue_number,
            "comment_id": self.comment_id,
            "week": self.week,
            "action": self.action,
        }


async def post_weekly_usage_report(
    client: IssueCommentClient,
    *,
    week: str,
    body: str,
    repository: str = DEFAULT_REPORT_REPOSITORY,
) -> WeeklyReportResult:
    """Create or update this week's report comment on the report issue.

    Precondition: ``body`` starts with ``week_marker(week)``; ``repository`` is
    ``owner/name``.
    Postcondition: exactly one comment carries the week's marker.
    """
    marker = week_marker(week)
    if not body.startswith(marker):
        raise ValueError("body must start with the week marker")
    if not _REPO_RE.match(repository):
        raise ValueError(f"repository must be owner/name; got {repository!r}")

    issue = await client.find_open_issue(repository, title=REPORT_ISSUE_TITLE, label=REPORT_LABEL)
    if issue is None:
        issue = await client.create_issue(
            repository, title=REPORT_ISSUE_TITLE, body=REPORT_ISSUE_BODY, labels=[REPORT_LABEL]
        )
        log.info("usage report: created issue %s#%d", repository, issue)

    for comment in await client.list_issue_comments(repository, issue):
        if str(comment.get("body", "")).startswith(marker):
            comment_id = int(comment["id"])
            if comment.get("body") == body:
                return WeeklyReportResult(repository, issue, comment_id, week, "unchanged")
            await client.update_comment(repository, comment_id, body)
            log.info("usage report: updated %s comment %d for %s", repository, comment_id, week)
            return WeeklyReportResult(repository, issue, comment_id, week, "updated")

    comment_id = await client.create_comment(repository, issue, body)
    log.info("usage report: posted %s comment %d for %s", repository, comment_id, week)
    return WeeklyReportResult(repository, issue, comment_id, week, "created")


class GhIssueCommentClient:
    """:class:`IssueCommentClient` over the dashboard's REST client (``gh_client``)."""

    async def find_open_issue(self, repository: str, *, title: str, label: str) -> int | None:
        import gh_client  # noqa: PLC0415 - keeps this module importable without httpx config

        async for item in gh_client.paginate(f"/repos/{repository}/issues?state=open&labels={label}"):
            if "pull_request" not in item and item.get("title") == title:
                return int(item["number"])
        return None

    async def create_issue(self, repository: str, *, title: str, body: str, labels: list[str]) -> int:
        import gh_client  # noqa: PLC0415

        data = await gh_client.post(
            f"/repos/{repository}/issues", json={"title": title, "body": body, "labels": labels}
        )
        return int(data["number"])

    async def list_issue_comments(self, repository: str, number: int) -> list[dict[str, Any]]:
        import gh_client  # noqa: PLC0415

        return [item async for item in gh_client.paginate(f"/repos/{repository}/issues/{number}/comments")]

    async def create_comment(self, repository: str, number: int, body: str) -> int:
        import gh_client  # noqa: PLC0415

        data = await gh_client.post(f"/repos/{repository}/issues/{number}/comments", json={"body": body})
        return int(data["id"])

    async def update_comment(self, repository: str, comment_id: int, body: str) -> None:
        import gh_client  # noqa: PLC0415

        await gh_client.patch(f"/repos/{repository}/issues/comments/{comment_id}", json={"body": body})


# ─── Schedule ────────────────────────────────────────────────────────────────


async def _weekly_report_loop(store: Any, client: IssueCommentClient, repository: str) -> None:
    while True:
        try:
            week, body = build_weekly_report(store)
            await post_weekly_usage_report(client, week=week, body=body, repository=repository)
        except Exception as exc:  # justified: a reporting failure must never take down the dashboard
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            log.warning("usage report: run failed: %s", exc)
        await asyncio.sleep(REPORT_INTERVAL_SECONDS)


def start_weekly_usage_report_loop(store: Any) -> asyncio.Task[None] | None:
    """Start the refresh loop when this node is the designated reporter.

    Off by default: set ``USAGE_REPORT_WEEKLY_ENABLED=1`` on exactly one hub
    node so the fleet posts one report, not one per node. The loop refreshes
    the current week's comment every six hours (idempotent update).
    """
    if os.environ.get(REPORT_ENABLED_ENV, "").strip() not in {"1", "true", "yes"}:
        return None
    repository = os.environ.get("USAGE_REPORT_REPOSITORY", DEFAULT_REPORT_REPOSITORY).strip()
    if not _REPO_RE.match(repository):
        log.warning("usage report: invalid USAGE_REPORT_REPOSITORY %r; loop not started", repository)
        return None
    return asyncio.create_task(_weekly_report_loop(store, GhIssueCommentClient(), repository))
