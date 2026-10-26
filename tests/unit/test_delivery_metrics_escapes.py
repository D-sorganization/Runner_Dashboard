"""Weighted escape rollups for delivery metrics (#2117 gap 1)."""

from __future__ import annotations

from datetime import UTC, datetime

from delivery_metrics.escapes import escape_events_for_pr, weighted_escapes
from delivery_metrics.models import CheckRunSnapshot, PrSnapshot
from delivery_metrics.rollup import aggregate_window

REPO = "D-sorganization/Runner_Dashboard"
NOW = datetime(2026, 10, 24, tzinfo=UTC)


def _pr(**overrides: object) -> PrSnapshot:
    base = {
        "repo_full": REPO,
        "number": 1,
        "title": "feat: example",
        "state": "closed",
        "merged": True,
        "draft": False,
        "head_sha": "abc123",
        "base_ref": "main",
        "author_login": "agent",
        "created_at": datetime(2026, 10, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 10, 2, tzinfo=UTC),
        "closed_at": datetime(2026, 10, 2, tzinfo=UTC),
        "merged_at": datetime(2026, 10, 2, tzinfo=UTC),
        "additions": 1,
        "deletions": 0,
        "changed_files": 1,
        "html_url": "https://github.com/D-sorganization/Runner_Dashboard/pull/1",
    }
    base.update(overrides)
    return PrSnapshot(**base)  # type: ignore[arg-type]


def _run(
    name: str,
    conclusion: str,
    check_run_id: int = 1,
    *,
    completed_at: datetime | None = None,
    head_sha: str = "abc123",
) -> CheckRunSnapshot:
    done = completed_at or NOW
    return CheckRunSnapshot(
        repo_full=REPO,
        head_sha=head_sha,
        check_run_id=check_run_id,
        name=name,
        status="completed",
        conclusion=conclusion,
        started_at=done,
        completed_at=done,
        details_url="https://example.invalid/check",
        app_slug="github-actions",
    )


def test_revert_title_emits_revert_escape() -> None:
    pr = _pr(title="Revert \"feat: bad\"", number=9)
    events = escape_events_for_pr(pr, ())
    kinds = {e["kind"] for e in events}
    assert "revert" in kinds
    assert events[0]["weight"] >= 10


def test_red_main_on_merged_pr_with_post_merge_main_ci_failure() -> None:
    merged_at = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    pr = _pr(number=10, merged_at=merged_at)
    runs = (
        _run("CI Standard / tests", "failure", 42, completed_at=merged_at),
        _run("CI Standard / tests", "success", 43, completed_at=datetime(2026, 10, 2, 13, 0, tzinfo=UTC)),
    )
    events = escape_events_for_pr(pr, runs)
    assert any(e["kind"] == "red_main" for e in events)
    red = next(e for e in events if e["kind"] == "red_main")
    assert red["check_run_ids"] == [42]
    assert red["number"] == 10


def test_red_main_ignores_pre_merge_ci_failure() -> None:
    merged_at = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    pr = _pr(number=13, merged_at=merged_at)
    runs = (_run("CI Standard / tests", "failure", 44, completed_at=datetime(2026, 10, 1, tzinfo=UTC)),)
    assert not any(e["kind"] == "red_main" for e in escape_events_for_pr(pr, runs))


def test_unmerged_revert_title_is_not_an_escape() -> None:
    pr = _pr(title="Revert \"feat\"", number=14, merged=False, state="open", merged_at=None)
    assert not any(e["kind"] == "revert" for e in escape_events_for_pr(pr, ()))


def test_failed_rollout_from_deploy_check_name() -> None:
    pr = _pr(number=11, merged=False, state="open")
    runs = (_run("auto_deploy smoke", "failure", 7),)
    events = escape_events_for_pr(pr, runs)
    assert any(e["kind"] == "failed_rollout" for e in events)


def test_weighted_escapes_sorted_heaviest_first() -> None:
    pulls = (
        _pr(number=1, title="Revert x", head_sha="h1"),
        _pr(number=2, head_sha="h2"),
    )
    merged_at = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    pulls = (
        _pr(number=1, title="Revert x", head_sha="h1", merged_at=merged_at),
        _pr(number=2, head_sha="h2", merged_at=merged_at),
    )
    checks = {
        "h1": (),
        "h2": (
            _run("CI Standard", "failure", 2, head_sha="h2", completed_at=merged_at),
        ),
    }
    weighted = weighted_escapes(pulls, checks)
    assert weighted[0]["kind"] == "red_main"
    assert weighted[-1]["kind"] == "revert"


def test_aggregate_window_includes_escapes_weighted() -> None:
    pr = _pr(number=12, title="Revert ship")
    body = aggregate_window((pr,), {"abc123": ()}, now=NOW)
    assert body["escapes_weighted"]
    assert body["escapes_weighted"][0]["kind"] == "revert"
