"""Outcome scorecard aggregation and PR reads (WP-1.2, #1517)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import gh_client
import pytest
from staff import outcomes
from staff.outcomes import PrFact, aggregate, prs_to_read, rate, read_pr_fact, read_pr_facts
from staff.store import RunRecord

REPO = "D-sorganization/Tools"


def run(run_id: str, **kw: Any) -> RunRecord:
    base: dict[str, Any] = {
        "id": run_id,
        "role": "night-watch",
        "provider": "claude",
        "model": None,
        "machine": "Node",
        "repo": "Tools",
        "target_kind": "issue",
        "target_ref": "42",
        "prompt": "p",
        "status": "succeeded",
        "created_at": "2026-09-20T10:00:00Z",
        "cost_usd": 1.0,
    }
    return RunRecord(**{**base, **kw})


# ── rate ───────────────────────────────────────────────────────────────────


def test_rate_is_none_without_a_denominator() -> None:
    assert rate(0, 0) is None
    assert rate(3, 0) is None
    assert rate(1, 4) == 0.25


# ── aggregate ──────────────────────────────────────────────────────────────


def test_zero_runs_gives_an_empty_scorecard_with_no_rates() -> None:
    body = aggregate([], {}, group_by="role")
    assert body["rows"] == []
    totals = body["totals"]
    assert totals["runs"] == 0 and totals["prs"] == 0
    for key in ("merge_rate", "verified_rate", "ci_first_pass_rate", "fix_within_48h_rate", "cost_per_merged_pr"):
        assert totals[key] is None, key


def test_runs_without_a_pr_count_cost_and_verification_but_no_pr_rates() -> None:
    runs = [run("a", verification="failed", cost_usd=2.0), run("b", verification="not_applicable")]
    totals = aggregate(runs, {}, group_by="role")["totals"]
    assert totals["runs"] == 2 and totals["cost_usd"] == 3.0
    assert totals["failed_verification"] == 1
    assert totals["verified_rate"] == 0.0  # one checked run, not verified
    assert totals["prs"] == 0 and totals["merge_rate"] is None and totals["cost_per_merged_pr"] is None


def test_rates_from_pr_facts() -> None:
    runs = [
        run("a", pr_number=1, verification="verified", cost_usd=3.0),
        run("b", pr_number=2, verification="failed", cost_usd=1.0),
        run("c", pr_number=3, verification="unverified", cost_usd=2.0),
    ]
    facts = {
        (REPO, 1): PrFact("merged", ci_first_pass=True, followup_fix=False),
        (REPO, 2): PrFact("closed_unmerged", ci_first_pass=False),
        (REPO, 3): PrFact("open", ci_first_pass=None),
    }
    t = aggregate(runs, facts, group_by="role")["totals"]
    assert (t["merged"], t["closed_unmerged"], t["open"]) == (1, 1, 1)
    assert t["merge_rate"] == 0.5
    assert t["verified_rate"] == round(1 / 3, 4)
    assert t["ci_first_pass_rate"] == 0.5  # pending CI is not counted
    assert t["fix_within_48h_rate"] == 0.0
    assert t["cost_per_merged_pr"] == 6.0


def test_unreadable_prs_are_counted_as_unknown_not_as_failures() -> None:
    runs = [run("a", pr_number=1), run("b", pr_number=2)]
    t = aggregate(runs, {(REPO, 1): PrFact("merged")}, group_by="role")["totals"]
    assert t["prs"] == 2 and t["prs_unknown"] == 1
    assert t["merge_rate"] == 1.0


def test_a_pr_shared_by_retries_counts_once() -> None:
    runs = [run("a", pr_number=7), run("b", pr_number=7)]
    t = aggregate(runs, {(REPO, 7): PrFact("merged")}, group_by="role")["totals"]
    assert t["prs"] == 1 and t["merged"] == 1


@pytest.mark.parametrize("group_by", ["role", "provider", "repo"])
def test_rows_are_grouped_and_sorted(group_by: str) -> None:
    runs = [
        run("a", role="z-role", provider="codex", repo="Zeta"),
        run("b", role="a-role", provider="claude", repo="Alpha"),
        run("c", role="a-role", provider="claude", repo="Alpha"),
    ]
    rows = aggregate(runs, {}, group_by=group_by)["rows"]
    assert [r["runs"] for r in rows] == [2, 1]
    assert rows[0]["key"] < rows[1]["key"]


def test_unknown_group_by_violates_the_contract() -> None:
    with pytest.raises(AssertionError):
        aggregate([], {}, group_by="machine")


# ── prs_to_read ────────────────────────────────────────────────────────────


def test_runs_without_a_pr_or_repo_are_never_read() -> None:
    wanted, truncated = prs_to_read([run("a"), run("b", pr_number=5, repo="")])
    assert wanted == {} and truncated is False


def test_prs_to_read_keeps_the_linked_issue_and_caps_the_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outcomes, "MAX_PRS", 2)
    runs = [
        run("a", pr_number=1, created_at="2026-09-20T01:00:00Z", target_ref="#10"),
        run("b", pr_number=2, created_at="2026-09-20T02:00:00Z", target_kind="prompt", target_ref=""),
        run("c", pr_number=3, created_at="2026-09-20T03:00:00Z"),
    ]
    wanted, truncated = prs_to_read(runs)
    assert truncated is True
    assert wanted == {(REPO, 3): 42, (REPO, 2): None}  # newest first


# ── read_pr_fact / read_pr_facts ───────────────────────────────────────────


class FakeGitHub:
    """Answers gh_client.get from a path-prefix table and records every call."""

    def __init__(self, table: dict[str, Any]) -> None:
        self.table, self.calls = table, []

    async def get(self, path: str) -> Any:
        self.calls.append(path)
        for prefix, answer in self.table.items():
            if path.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise gh_client.GhNotFound(f"unmocked {path}")


def _gh(monkeypatch: pytest.MonkeyPatch, table: dict[str, Any]) -> FakeGitHub:
    fake = FakeGitHub(table)
    monkeypatch.setattr(gh_client, "get", fake.get)
    return fake


GREEN = {"check_runs": [{"status": "completed", "conclusion": "success"}]}
RED = {"check_runs": [{"status": "completed", "conclusion": "failure"}]}
MERGED = {"state": "closed", "merged_at": "2026-09-20T12:00:00Z"}


def test_merged_pr_with_green_first_commit_and_a_followup_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _gh(
        monkeypatch,
        {
            f"/repos/{REPO}/pulls/9/commits": [{"sha": "first"}],
            f"/repos/{REPO}/pulls/9": MERGED,
            f"/repos/{REPO}/commits/first/check-runs": GREEN,
            "/search/issues": {"items": [{"number": 9}, {"number": 11}]},
        },
    )
    fact = asyncio.run(read_pr_fact(REPO, 9, 42))
    assert fact == PrFact("merged", ci_first_pass=True, followup_fix=True)
    search = next(c for c in fake.calls if c.startswith("/search/issues"))
    assert "42" in search and "merged%3A2026-09-20T12%3A00%3A00Z..2026-09-22T12%3A00%3A00Z" in search


def test_only_the_pr_itself_in_search_is_no_followup(monkeypatch: pytest.MonkeyPatch) -> None:
    _gh(
        monkeypatch,
        {
            f"/repos/{REPO}/pulls/9/commits": [{"sha": "first"}],
            f"/repos/{REPO}/pulls/9": MERGED,
            f"/repos/{REPO}/commits/first/check-runs": RED,
            "/search/issues": {"items": [{"number": 9}]},
        },
    )
    assert asyncio.run(read_pr_fact(REPO, 9, 42)) == PrFact("merged", ci_first_pass=False, followup_fix=False)


def test_open_pr_without_issue_skips_the_followup_search(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _gh(
        monkeypatch,
        {
            f"/repos/{REPO}/pulls/4/commits": [{"sha": "s"}],
            f"/repos/{REPO}/pulls/4": {"state": "open", "merged_at": None},
            f"/repos/{REPO}/commits/s/check-runs": {"check_runs": [{"status": "in_progress", "conclusion": None}]},
        },
    )
    assert asyncio.run(read_pr_fact(REPO, 4, None)) == PrFact("open", ci_first_pass=None, followup_fix=None)
    assert not any(c.startswith("/search") for c in fake.calls)


def test_a_failed_ci_lookup_leaves_the_flag_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _gh(
        monkeypatch,
        {
            f"/repos/{REPO}/pulls/4/commits": gh_client.GhServerError(502, "commits"),
            f"/repos/{REPO}/pulls/4": {"state": "closed", "merged_at": None},
        },
    )
    assert asyncio.run(read_pr_fact(REPO, 4, 42)) == PrFact("closed_unmerged")


def test_unreadable_pr_is_omitted_and_facts_are_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _gh(
        monkeypatch,
        {
            f"/repos/{REPO}/pulls/1/commits": [],
            f"/repos/{REPO}/pulls/1": {"state": "open", "merged_at": None},
            f"/repos/{REPO}/pulls/2": gh_client.GhNotFound("gone"),
        },
    )
    wanted = {(REPO, 1): None, (REPO, 2): None}
    assert asyncio.run(read_pr_facts(wanted)) == {(REPO, 1): PrFact("open")}
    first = len(fake.calls)
    asyncio.run(read_pr_facts(wanted))
    assert [c for c in fake.calls[first:] if "/pulls/1" in c] == []  # served from cache


def test_default_since_is_midnight_fourteen_days_back() -> None:
    assert outcomes.default_since(datetime(2026, 9, 25, 17, 30, tzinfo=UTC)) == "2026-09-11T00:00:00Z"
