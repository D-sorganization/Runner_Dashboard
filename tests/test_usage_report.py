"""Weekly agent usage report: renderer and idempotent posting job (USE-1, issue #1865)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from session_telemetry import SessionTelemetryStore
from usage_report import (
    REPORT_ISSUE_TITLE,
    REPORT_LABEL,
    build_weekly_report,
    iso_week,
    post_weekly_usage_report,
    render_weekly_usage_report,
    week_marker,
)

# ─── Fixtures ────────────────────────────────────────────────────────────────

CURRENT: dict[str, Any] = {
    "total_sessions": 12,
    "cost_per_merged_pr": {"count": 6, "median_usd": 3.5, "p90_usd": 9.0},
    "cost_after_pr": {"count": 5, "total_usd": 4.0, "median_usd": 0.5, "p90_usd": 2.0, "mean_usd": 0.8},
    "post_pr_wakeups": {
        "total_pr_sessions": 5,
        "sessions_gt_3_count": 1,
        "sessions_gt_3_ids": ["s1"],
        "sessions_gt_0_count": 2,
        "sessions_gt_0_ratio": 0.4,
    },
    "startup_context_by_environment": {
        "cloud": {"count": 8, "median": 40000, "p90": 52000, "mean": 41000.0},
        "local": {"count": 4, "median": 30000, "p90": 31000, "mean": 30000.0},
    },
}
PREVIOUS: dict[str, Any] = {
    "total_sessions": 10,
    "cost_per_merged_pr": {"count": 4, "median_usd": 5.0, "p90_usd": 12.0},
    "cost_after_pr": {"count": 4, "total_usd": 8.0, "median_usd": 1.0, "p90_usd": 4.0, "mean_usd": 2.0},
    "post_pr_wakeups": {
        "total_pr_sessions": 4,
        "sessions_gt_3_count": 2,
        "sessions_gt_3_ids": ["a", "b"],
        "sessions_gt_0_count": 3,
        "sessions_gt_0_ratio": 0.75,
    },
    "startup_context_by_environment": {"cloud": {"count": 6, "median": 50000, "p90": 60000, "mean": 50000.0}},
}


def test_iso_week_label() -> None:
    assert iso_week(datetime(2026, 10, 4, tzinfo=UTC)) == "2026-W40"
    assert iso_week(datetime(2027, 1, 1, tzinfo=UTC)) == "2026-W53"


def test_renderer_covers_every_section_with_deltas() -> None:
    body = render_weekly_usage_report(week="2026-W40", current=CURRENT, previous=PREVIOUS)
    assert body.startswith(week_marker("2026-W40"))
    assert "## Weekly agent usage report — 2026-W40" in body
    # cost per merged PR, median and p90, with week-over-week deltas
    assert "| Cost per merged PR (median) | $3.50 | $5.00 | -$1.50 (-30%) |" in body
    assert "| Cost per merged PR (p90) | $9.00 | $12.00 | -$3.00 (-25%) |" in body
    # post-PR wake-ups and spend
    assert "| Post-PR spend (total) | $4.00 | $8.00 | -$4.00 (-50%) |" in body
    assert "| Sessions with >3 post-PR wake-ups | 1 | 2 | -1 (-50%) |" in body
    # startup context per environment (an environment new this week has no delta)
    assert "| cloud | 8 | 40,000 | 52,000 | -10,000 (-20%) |" in body
    assert "| local | 4 | 30,000 | 31,000 | n/a |" in body
    # merge-queue metrics are not fabricated when no source reports them
    assert "Merge queue" in body
    assert "not reported" in body


def test_renderer_without_previous_week_marks_deltas_unavailable() -> None:
    body = render_weekly_usage_report(week="2026-W40", current=CURRENT, previous=None)
    assert "| Cost per merged PR (median) | $3.50 | — | n/a |" in body


def test_renderer_reports_merge_queue_when_provided() -> None:
    queue = {"wait_median_min": 12.0, "wait_p90_min": 40.5, "timeouts": 2}
    body = render_weekly_usage_report(week="2026-W40", current=CURRENT, previous=None, merge_queue=queue)
    assert "| Queue wait (median) | 12.0 min |" in body
    assert "| Queue wait (p90) | 40.5 min |" in body
    assert "| Queue timeouts | 2 |" in body


def test_renderer_is_deterministic() -> None:
    first = render_weekly_usage_report(week="2026-W40", current=CURRENT, previous=PREVIOUS)
    assert first == render_weekly_usage_report(
        week="2026-W40", current=copy.deepcopy(CURRENT), previous=copy.deepcopy(PREVIOUS)
    )


def test_renderer_rejects_bad_week() -> None:
    with pytest.raises(ValueError, match="week"):
        render_weekly_usage_report(week="last week", current=CURRENT, previous=None)


# ─── Building the two windows from telemetry ─────────────────────────────────


def test_build_weekly_report_splits_current_and_previous_week(tmp_path: Path) -> None:
    store = SessionTelemetryStore(data_file=tmp_path / "t.json", retention_days=30)
    now = datetime(2026, 10, 4, 12, tzinfo=UTC)
    store.record_session(
        {"session_id": "new", "cost_usd": 2.0, "pr_merged": True, "recorded_at": (now - timedelta(days=1)).isoformat()}
    )
    store.record_session(
        {"session_id": "old", "cost_usd": 8.0, "pr_merged": True, "recorded_at": (now - timedelta(days=9)).isoformat()}
    )
    week, body = build_weekly_report(store, now=now)
    assert week == "2026-W40"
    assert "| Cost per merged PR (median) | $2.00 | $8.00 | -$6.00 (-75%) |" in body


# ─── Idempotent posting job ──────────────────────────────────────────────────


class FakeIssues:
    def __init__(self) -> None:
        self.issues: list[dict[str, Any]] = []
        self.comments: dict[int, list[dict[str, Any]]] = {}
        self.calls: list[str] = []
        self._next_id = 100

    async def find_open_issue(self, repository: str, *, title: str, label: str) -> int | None:
        self.calls.append("find")
        for issue in self.issues:
            if issue["title"] == title and label in issue["labels"]:
                return int(issue["number"])
        return None

    async def create_issue(self, repository: str, *, title: str, body: str, labels: list[str]) -> int:
        self.calls.append("create_issue")
        number = len(self.issues) + 1
        self.issues.append({"number": number, "title": title, "labels": labels, "body": body})
        self.comments[number] = []
        return number

    async def list_issue_comments(self, repository: str, number: int) -> list[dict[str, Any]]:
        self.calls.append("list")
        return list(self.comments[number])

    async def create_comment(self, repository: str, number: int, body: str) -> int:
        self.calls.append("create_comment")
        self._next_id += 1
        self.comments[number].append({"id": self._next_id, "body": body})
        return self._next_id

    async def update_comment(self, repository: str, comment_id: int, body: str) -> None:
        self.calls.append("update_comment")
        for comments in self.comments.values():
            for comment in comments:
                if comment["id"] == comment_id:
                    comment["body"] = body


async def test_job_creates_issue_and_comment_once() -> None:
    fake = FakeIssues()
    body = render_weekly_usage_report(week="2026-W40", current=CURRENT, previous=PREVIOUS)
    result = await post_weekly_usage_report(fake, week="2026-W40", body=body)
    assert result.action == "created"
    assert fake.issues[0]["title"] == REPORT_ISSUE_TITLE
    assert fake.issues[0]["labels"] == [REPORT_LABEL]
    assert len(fake.comments[result.issue_number]) == 1


async def test_job_updates_the_same_comment_within_a_week() -> None:
    fake = FakeIssues()
    first = render_weekly_usage_report(week="2026-W40", current=CURRENT, previous=None)
    second = render_weekly_usage_report(week="2026-W40", current=CURRENT, previous=PREVIOUS)
    r1 = await post_weekly_usage_report(fake, week="2026-W40", body=first)
    r2 = await post_weekly_usage_report(fake, week="2026-W40", body=second)
    r3 = await post_weekly_usage_report(fake, week="2026-W40", body=second)
    assert (r1.action, r2.action, r3.action) == ("created", "updated", "unchanged")
    assert r1.comment_id == r2.comment_id == r3.comment_id
    assert fake.calls.count("create_issue") == 1
    assert fake.calls.count("create_comment") == 1
    assert fake.comments[r1.issue_number][0]["body"] == second


async def test_job_adds_one_comment_per_new_week() -> None:
    fake = FakeIssues()
    await post_weekly_usage_report(
        fake, week="2026-W40", body=render_weekly_usage_report(week="2026-W40", current=CURRENT, previous=None)
    )
    result = await post_weekly_usage_report(
        fake, week="2026-W41", body=render_weekly_usage_report(week="2026-W41", current=CURRENT, previous=None)
    )
    assert result.action == "created"
    assert len(fake.comments[result.issue_number]) == 2


async def test_job_refuses_body_without_its_week_marker() -> None:
    with pytest.raises(ValueError, match="marker"):
        await post_weekly_usage_report(FakeIssues(), week="2026-W40", body="no marker")
