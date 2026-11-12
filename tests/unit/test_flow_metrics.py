from __future__ import annotations

from datetime import UTC, datetime

import pytest

from flow_metrics import (
    PR_AGE_RULE_HOURS,
    build_repo_row,
    percentile,
    summarize_cycle_times,
    summarize_open_pr_ages,
    weekly_issue_burn,
)


@pytest.mark.unit
def test_percentile_and_open_pr_age_summary() -> None:
    ages = [10.0, 20.0, 80.0, 100.0]
    summary = summarize_open_pr_ages(ages, rule_hours=72.0)
    assert summary["count"] == 4
    assert summary["p50_hours"] == pytest.approx(50.0)
    assert summary["over_rule_hours"] == 2
    assert summary["rule_hours"] == 72.0
    assert percentile([1.0, 2.0, 3.0], 0) == 1.0


@pytest.mark.unit
def test_weekly_burn_and_cycle_time_empty() -> None:
    assert weekly_issue_burn(3, 8)["net"] == 5
    assert summarize_cycle_times([])["sample_size"] == 0


@pytest.mark.unit
def test_build_repo_row_shape() -> None:
    row = build_repo_row(
        "D-sorganization/Runner_Dashboard",
        open_pr_ages=[80.0],
        cycle_hours=[12.0, 36.0],
        opened_issues=2,
        closed_issues=5,
    )
    assert row["repo"] == "Runner_Dashboard"
    assert row["open_pr_age"]["over_rule_hours"] == 1
    assert row["pr_cycle_time"]["sample_size"] == 2
    assert row["weekly_issue_burn"]["net"] == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_collect_flow_metrics_uses_injected_fetcher() -> None:
    from flow_metrics import collect_flow_metrics

    now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)

    async def fake_gh(args: list[str], timeout: int) -> tuple[int, str, str]:
        path = args[-1] if args and args[0] == "api" else ""
        if path.endswith("/pulls?state=open&per_page=100"):
            return 0, '[{"created_at":"2026-10-30T00:00:00Z"}]', ""
        if "state=closed" in path:
            return (
                0,
                '[{"created_at":"2026-10-28T00:00:00Z","merged_at":"2026-10-29T12:00:00Z"}]',
                "",
            )
        if "/search/issues" in path and "created%3A" in path:
            return 0, '{"total_count": 4}', ""
        if "/search/issues" in path and "closed%3A" in path:
            return 0, '{"total_count": 7}', ""
        return 1, "", f"unexpected args {args}"

    payload = await collect_flow_metrics(repo="Runner_Dashboard", run_gh=fake_gh, now=now)
    assert payload["available"] is True
    assert payload["pr_age_rule_hours"] == PR_AGE_RULE_HOURS
    assert len(payload["repos"]) == 1
    row = payload["repos"][0]
    assert row["weekly_issue_burn"]["net"] == 3
    assert row["open_pr_age"]["count"] == 1
