"""Daily per-model scorecard aggregation and the PR facts it reads (#4564, scope item 2).

The scorecard is a pure fold over the same run records and PR facts the #1517 outcome
scorecard already reads, so these tests fabricate both and never reach GitHub — except
for ``read_pr_fact``, which is mocked the way ``test_staff_outcomes_aggregation`` mocks it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import gh_client
import pytest
from staff.outcomes import PrFact, read_pr_fact
from staff.scorecard import daily_scorecard
from staff.store import RunRecord

from staff import scorecard

REPO = "D-sorganization/Tools"


def run(run_id: str, **kw: Any) -> RunRecord:
    base: dict[str, Any] = {
        "id": run_id,
        "role": "night-watch",
        "provider": "claude",
        "model": "claude-opus-5-5",
        "machine": "Node",
        "repo": "Tools",
        "target_kind": "issue",
        "target_ref": "42",
        "prompt": "p",
        "status": "succeeded",
        "created_at": "2026-11-18T10:00:00Z",
        "cost_usd": 1.0,
    }
    return RunRecord(**{**base, **kw})


# ── bucketing ──────────────────────────────────────────────────────────────


def test_no_runs_gives_no_rows_and_no_rates() -> None:
    body = daily_scorecard([], {})
    assert body["rows"] == []
    assert body["models"] == []
    totals = body["totals"]
    assert totals["runs"] == 0 and totals["prs"] == 0
    for key in ("ci_failure_rate", "merge_seconds_median", "findings_per_merged_pr"):
        assert totals[key] is None, key


def test_rows_are_one_per_day_and_model_and_sorted_by_day_then_model() -> None:
    runs = [
        run("a", created_at="2026-11-18T10:00:00Z", model="zeta-1"),
        run("b", created_at="2026-11-18T10:00:00Z", model="alpha-1"),
        run("c", created_at="2026-11-17T10:00:00Z", model="alpha-1"),
    ]
    rows = daily_scorecard(runs, {})["rows"]
    assert [(r["date"], r["model"]) for r in rows] == [
        ("2026-11-17", "alpha-1"),
        ("2026-11-18", "alpha-1"),
        ("2026-11-18", "zeta-1"),
    ]


def test_a_run_is_bucketed_by_its_utc_day_not_the_local_one() -> None:
    # 23:59Z is still the same UTC day for the row that 00:00Z the next day is not.
    runs = [run("late", created_at="2026-11-18T23:59:59Z"), run("next", created_at="2026-11-19T00:00:00Z")]
    assert [r["date"] for r in daily_scorecard(runs, {})["rows"]] == ["2026-11-18", "2026-11-19"]


def test_runs_without_a_model_or_with_a_fallback_only_model_land_in_unknown() -> None:
    runs = [run("a", model=None), run("b", model="  "), run("c", model="")]
    rows = daily_scorecard(runs, {})["rows"]
    assert [r["model"] for r in rows] == [scorecard.UNKNOWN_MODEL]
    assert rows[0]["runs"] == 3


def test_a_naive_timestamp_is_still_bucketed() -> None:
    rows = daily_scorecard([run("a", created_at="2026-11-18T10:00:00")], {})["rows"]
    assert rows[0]["date"] == "2026-11-18"


def test_an_unparseable_timestamp_is_dropped_not_guessed() -> None:
    rows = daily_scorecard([run("a", created_at="yesterday"), run("b", created_at="")], {})["rows"]
    assert rows == []


# ── metrics per bucket ─────────────────────────────────────────────────────


def test_run_and_cost_counts_come_from_the_runs_alone() -> None:
    runs = [
        run("a", status="succeeded", verification="verified", cost_usd=2.0),
        run("b", status="failed", verification="failed", cost_usd=0.5),
        run("c", status="cancelled", verification="", cost_usd=0.25),
    ]
    row = daily_scorecard(runs, {})["rows"][0]
    assert (row["runs"], row["succeeded"], row["verified"], row["failed_verification"]) == (3, 1, 1, 1)
    assert row["cost_usd"] == 2.75


def test_ci_failure_rate_is_failed_over_decidable_and_none_without_data() -> None:
    runs = [run("a", pr_number=1), run("b", pr_number=2), run("c", pr_number=3), run("d", pr_number=4)]
    facts = {
        (REPO, 1): PrFact("merged", ci_first_pass=True),
        (REPO, 2): PrFact("merged", ci_first_pass=False),
        (REPO, 3): PrFact("merged", ci_first_pass=False),
        (REPO, 4): PrFact("open", ci_first_pass=None),  # pending CI is not a failure
    }
    row = daily_scorecard(runs, facts)["rows"][0]
    assert (row["ci_known"], row["ci_failed"]) == (3, 2)
    assert row["ci_failure_rate"] == round(2 / 3, 4)


def test_ci_failure_rate_is_none_when_no_pr_has_a_decidable_ci_result() -> None:
    row = daily_scorecard([run("a", pr_number=1)], {(REPO, 1): PrFact("open", ci_first_pass=None)})["rows"][0]
    assert row["ci_known"] == 0 and row["ci_failure_rate"] is None


def test_merge_time_is_the_median_of_merged_prs_and_none_without_any() -> None:
    runs = [run(str(i), pr_number=i) for i in (1, 2, 3)]
    facts = {
        (REPO, 1): PrFact("merged", merge_seconds=60.0),
        (REPO, 2): PrFact("merged", merge_seconds=120.0),
        (REPO, 3): PrFact("merged", merge_seconds=600.0),
    }
    row = daily_scorecard(runs, facts)["rows"][0]
    assert row["merge_seconds_known"] == 3
    assert row["merge_seconds_median"] == 120.0
    empty = daily_scorecard([run("a", pr_number=1)], {(REPO, 1): PrFact("open")})["rows"][0]
    assert empty["merge_seconds_known"] == 0 and empty["merge_seconds_median"] is None


def test_unmeasured_merge_times_are_excluded_from_the_median() -> None:
    runs = [run("a", pr_number=1), run("b", pr_number=2)]
    facts = {(REPO, 1): PrFact("merged", merge_seconds=None), (REPO, 2): PrFact("merged", merge_seconds=300.0)}
    row = daily_scorecard(runs, facts)["rows"][0]
    assert row["merged"] == 2 and row["merge_seconds_known"] == 1
    assert row["merge_seconds_median"] == 300.0


def test_review_findings_sum_and_divide_by_merged_prs_only() -> None:
    runs = [run("a", pr_number=1), run("b", pr_number=2)]
    facts = {
        (REPO, 1): PrFact("merged", review_findings=4),
        (REPO, 2): PrFact("closed_unmerged", review_findings=3),
    }
    row = daily_scorecard(runs, facts)["rows"][0]
    assert row["review_findings"] == 7
    assert row["findings_per_merged_pr"] == 4.0


def test_findings_are_none_when_no_pr_reported_any() -> None:
    runs = [run("a", pr_number=1)]
    facts = {(REPO, 1): PrFact("merged", merge_seconds=10.0, review_findings=None)}
    row = daily_scorecard(runs, facts)["rows"][0]
    assert row["review_findings"] is None
    assert row["findings_per_merged_pr"] is None


def test_an_unreadable_pr_is_unknown_not_a_failure() -> None:
    row = daily_scorecard([run("a", pr_number=1), run("b", pr_number=2)], {(REPO, 1): PrFact("merged")})["rows"][0]
    assert row["prs"] == 2 and row["prs_unknown"] == 1
    assert row["merged"] == 1


def test_a_pr_shared_by_two_runs_in_one_bucket_counts_once() -> None:
    runs = [run("a", pr_number=7, cost_usd=1.0), run("b", pr_number=7, cost_usd=3.0)]
    row = daily_scorecard(runs, {(REPO, 7): PrFact("merged", merge_seconds=90.0, review_findings=2)})["rows"][0]
    assert (row["runs"], row["prs"], row["merged"]) == (2, 1, 1)
    assert row["review_findings"] == 2


# ── window rollups ─────────────────────────────────────────────────────────


def test_models_rollup_is_per_model_across_the_window() -> None:
    runs = [
        run("a", model="alpha-1", created_at="2026-11-17T10:00:00Z"),
        run("b", model="alpha-1", created_at="2026-11-18T10:00:00Z"),
        run("c", model="zeta-1", created_at="2026-11-18T10:00:00Z"),
    ]
    models = daily_scorecard(runs, {})["models"]
    assert [m["model"] for m in models] == ["alpha-1", "zeta-1"]
    assert models[0]["runs"] == 2 and models[0]["days"] == 2
    assert models[1]["runs"] == 1 and models[1]["days"] == 1


def test_totals_cover_every_bucket() -> None:
    runs = [
        run("a", model="alpha-1", pr_number=1, cost_usd=1.0),
        run("b", model="zeta-1", created_at="2026-11-17T10:00:00Z", pr_number=2, cost_usd=2.0),
    ]
    facts = {(REPO, 1): PrFact("merged", merge_seconds=60.0), (REPO, 2): PrFact("closed_unmerged")}
    totals = daily_scorecard(runs, facts)["totals"]
    assert (totals["runs"], totals["cost_usd"], totals["prs"], totals["merged"]) == (2, 3.0, 2, 1)


def test_model_filter_keeps_only_that_model_in_rows_and_rollups() -> None:
    runs = [run("a", model="alpha-1", pr_number=1), run("b", model="zeta-1", pr_number=2)]
    body = daily_scorecard(runs, {(REPO, 1): PrFact("merged"), (REPO, 2): PrFact("merged")}, model="alpha-1")
    assert [r["model"] for r in body["rows"]] == ["alpha-1"]
    assert [m["model"] for m in body["models"]] == ["alpha-1"]
    assert body["totals"]["runs"] == 1


# ── the pure function stays pure ───────────────────────────────────────────


def test_daily_scorecard_does_not_reach_out_to_github() -> None:
    # A fact table that omits every PR must still fold, which proves no I/O happens here.
    assert daily_scorecard([run("a", pr_number=1)], {})["rows"][0]["prs_unknown"] == 1


# ── read_pr_fact now also carries merge time and review findings ───────────


class FakeGitHub:
    def __init__(self, table: dict[str, Any]) -> None:
        self.table = table

    async def get(self, path: str) -> Any:
        for prefix, answer in self.table.items():
            if path.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise gh_client.GhNotFound(f"unmocked {path}")


def _gh(monkeypatch: pytest.MonkeyPatch, table: dict[str, Any]) -> None:
    fake = FakeGitHub(table)
    monkeypatch.setattr(gh_client, "get", fake.get)


GREEN = {"check_runs": [{"status": "completed", "conclusion": "success"}]}


def test_merge_seconds_and_findings_come_from_the_pr_we_already_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _gh(
        monkeypatch,
        {
            f"/repos/{REPO}/pulls/9/commits": [{"sha": "first"}],
            f"/repos/{REPO}/pulls/9": {
                "state": "closed",
                "created_at": "2026-11-18T10:00:00Z",
                "merged_at": "2026-11-18T11:30:00Z",
                "review_comments": 4,
            },
            f"/repos/{REPO}/commits/first/check-runs": GREEN,
        },
    )
    fact = asyncio.run(read_pr_fact(REPO, 9, None))
    assert fact.state == "merged"
    assert fact.merge_seconds == 5400.0
    assert fact.review_findings == 4
    assert fact.ci_first_pass is True


def test_an_unmerged_pr_has_no_merge_time_and_a_missing_findings_count_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _gh(
        monkeypatch,
        {
            f"/repos/{REPO}/pulls/4/commits": [{"sha": "s"}],
            f"/repos/{REPO}/pulls/4": {"state": "open", "created_at": "2026-11-18T10:00:00Z", "merged_at": None},
            f"/repos/{REPO}/commits/s/check-runs": {"check_runs": []},
        },
    )
    fact = asyncio.run(read_pr_fact(REPO, 4, None))
    assert fact.merge_seconds is None
    assert fact.review_findings is None  # GitHub did not report a count, so it is no data, not zero


def test_a_merged_pr_with_no_review_comments_reports_zero_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _gh(
        monkeypatch,
        {
            f"/repos/{REPO}/pulls/6/commits": [{"sha": "s"}],
            f"/repos/{REPO}/pulls/6": {
                "state": "closed",
                "created_at": "2026-11-18T10:00:00Z",
                "merged_at": "2026-11-18T10:20:00Z",
                "review_comments": 0,
            },
            f"/repos/{REPO}/commits/s/check-runs": {"check_runs": []},
        },
    )
    fact = asyncio.run(read_pr_fact(REPO, 6, None))
    assert fact.merge_seconds == 1200.0
    assert fact.review_findings == 0


def test_a_pr_without_a_readable_created_at_has_no_merge_time(monkeypatch: pytest.MonkeyPatch) -> None:
    _gh(
        monkeypatch,
        {
            f"/repos/{REPO}/pulls/5/commits": [{"sha": "s"}],
            f"/repos/{REPO}/pulls/5": {"state": "closed", "merged_at": "2026-11-18T11:00:00Z", "review_comments": 2},
            f"/repos/{REPO}/commits/s/check-runs": {"check_runs": []},
        },
    )
    assert asyncio.run(read_pr_fact(REPO, 5, None)).merge_seconds is None


def test_window_is_fourteen_utc_days_to_the_second() -> None:
    since, until = scorecard.window(datetime(2026, 11, 19, 17, 30, 45, tzinfo=UTC))
    assert since == "2026-11-05T00:00:00Z"
    assert until == "2026-11-19T17:30:45Z"


def test_window_spans_the_requested_number_of_days() -> None:
    since, _ = scorecard.window(datetime(2026, 11, 19, 17, 30, 45, tzinfo=UTC), days=3)
    assert since == "2026-11-16T00:00:00Z"
