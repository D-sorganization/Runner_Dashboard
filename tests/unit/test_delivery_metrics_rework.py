"""PR lead-time rework decomposition (#2117 gap 2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from delivery_metrics.models import CheckRunSnapshot, PrSnapshot
from delivery_metrics.rollup import build_pr_rollup, rework_seconds

REPO = "D-sorganization/Runner_Dashboard"
NOW = datetime(2026, 10, 24, tzinfo=UTC)


def _run(
    name: str,
    conclusion: str,
    *,
    started_at: datetime,
    completed_at: datetime,
    check_run_id: int = 1,
) -> CheckRunSnapshot:
    return CheckRunSnapshot(
        repo_full=REPO,
        head_sha="sha1",
        check_run_id=check_run_id,
        name=name,
        status="completed",
        conclusion=conclusion,
        started_at=started_at,
        completed_at=completed_at,
        details_url="https://example.invalid/check",
        app_slug="github-actions",
    )


def test_rework_seconds_none_without_check_runs() -> None:
    assert rework_seconds(()) is None


def test_rework_seconds_zero_when_no_failures() -> None:
    t0 = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=5)
    runs = (_run("tests", "success", started_at=t0, completed_at=t1),)
    assert rework_seconds(runs) == 0.0


def test_rework_seconds_sums_failure_to_retry_gap_per_check() -> None:
    fail_end = datetime(2026, 10, 1, 10, 30, tzinfo=UTC)
    retry_start = datetime(2026, 10, 1, 11, 0, tzinfo=UTC)
    runs = (
        _run(
            "tests",
            "failure",
            started_at=datetime(2026, 10, 1, 10, 0, tzinfo=UTC),
            completed_at=fail_end,
            check_run_id=1,
        ),
        _run(
            "tests",
            "success",
            started_at=retry_start,
            completed_at=retry_start + timedelta(minutes=4),
            check_run_id=2,
        ),
    )
    assert rework_seconds(runs) == 1800.0


def test_pr_rollup_includes_rework_seconds() -> None:
    t0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    pr = PrSnapshot(
        repo_full=REPO,
        number=99,
        title="feat",
        state="closed",
        merged=True,
        draft=False,
        head_sha="sha1",
        base_ref="main",
        author_login="agent",
        created_at=t0,
        updated_at=t0 + timedelta(hours=1),
        closed_at=t0 + timedelta(hours=1),
        merged_at=t0 + timedelta(hours=1),
        additions=1,
        deletions=0,
        changed_files=1,
        html_url="https://github.com/D-sorganization/Runner_Dashboard/pull/99",
    )
    fail_end = t0 + timedelta(minutes=20)
    retry_start = fail_end + timedelta(minutes=10)
    runs = (
        _run("tests", "failure", started_at=t0 + timedelta(minutes=5), completed_at=fail_end, check_run_id=1),
        _run("tests", "success", started_at=retry_start, completed_at=retry_start + timedelta(minutes=5), check_run_id=2),
    )
    rollup = build_pr_rollup(pr, runs, now=NOW)
    assert rollup.rework_seconds == 600.0
    assert rollup.to_dict()["rework_seconds"] == 600.0
