"""Unit tests for Bob's wave-scoring (RD#3642): pure logic + CLI."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from staff.bob_forecast_score import (
    ForecastItem,
    MergedPr,
    build_merged_prs_query,
    parse_forecast_table,
    parse_merged_prs_response,
    score_forecast,
    summarize,
)

from scripts.bob_forecast_score import fetch_merged_prs_payload, main, run_bob_forecast_score

RD = "D-sorganization/Runner_Dashboard"
UD = "D-sorganization/UpstreamDrift"

FORECAST_MD = """
| lane | item |
| --- | --- |
| claude-h | RD #100 Job Board S1 |
| claude-a | RD #101 mobile route e2e, then UD #200 grip audit |
| codex-c | not a tracked item |
"""


def _ts(hour: int) -> datetime:
    return datetime(2026, 10, 31, hour, 0, tzinfo=UTC)


def test_parse_forecast_table_extracts_one_item_per_issue_ref() -> None:
    items = parse_forecast_table(FORECAST_MD, default_repo=RD)
    assert items == [
        ForecastItem(lane="claude-h", repo=RD, issue_number=100, raw_text="RD #100 Job Board S1"),
        ForecastItem(
            lane="claude-a",
            repo=RD,
            issue_number=101,
            raw_text="RD #101 mobile route e2e, then UD #200 grip audit",
        ),
        ForecastItem(
            lane="claude-a",
            repo=UD,
            issue_number=200,
            raw_text="RD #101 mobile route e2e, then UD #200 grip audit",
        ),
    ]


def test_parse_forecast_table_bare_ref_uses_default_repo() -> None:
    items = parse_forecast_table("| agy-c | #42 python audit |", default_repo=UD)
    assert items == [ForecastItem(lane="agy-c", repo=UD, issue_number=42, raw_text="#42 python audit")]


def test_score_forecast_rejects_empty_window() -> None:
    with pytest.raises(ValueError, match="window_start"):
        score_forecast([], [], window_start=_ts(10), window_end=_ts(10), now=_ts(10))


def test_score_forecast_hit_miss_pending_and_repo_scoping() -> None:
    items = [
        ForecastItem(lane="a", repo=RD, issue_number=1, raw_text="#1"),  # merged in window -> hit
        ForecastItem(lane="a", repo=RD, issue_number=2, raw_text="#2"),  # no merge, window closed -> miss
        ForecastItem(lane="b", repo=RD, issue_number=3, raw_text="#3"),  # merged, but outside window
        ForecastItem(lane="c", repo=UD, issue_number=1, raw_text="UD #1"),  # same number, other repo
    ]
    merged = [
        MergedPr(repo=RD, number=50, merged_at=_ts(11), closes_issues=(1,)),
        MergedPr(repo=RD, number=51, merged_at=_ts(5), closes_issues=(3,)),  # before window
    ]
    verdicts = score_forecast(items, merged, window_start=_ts(10), window_end=_ts(12), now=_ts(13))
    by_key = {(v.item.repo, v.item.issue_number): v for v in verdicts}
    assert by_key[(RD, 1)].verdict == "hit"
    assert by_key[(RD, 1)].merged_pr_number == 50
    assert by_key[(RD, 2)].verdict == "miss"
    assert by_key[(RD, 3)].verdict == "miss"  # merged outside the window counts as a miss, not a hit
    assert by_key[(UD, 1)].verdict == "miss"  # a hit for RD#1 must not count for UD#1


def test_score_forecast_pending_when_window_not_yet_closed() -> None:
    items = [ForecastItem(lane="a", repo=RD, issue_number=9, raw_text="#9")]
    verdicts = score_forecast(items, [], window_start=_ts(10), window_end=_ts(12), now=_ts(11))
    assert verdicts[0].verdict == "pending"


def test_summarize_counts_and_hit_rate_excludes_pending() -> None:
    verdicts = score_forecast(
        [
            ForecastItem(lane="a", repo=RD, issue_number=1, raw_text="#1"),
            ForecastItem(lane="a", repo=RD, issue_number=2, raw_text="#2"),
            ForecastItem(lane="b", repo=RD, issue_number=3, raw_text="#3"),
        ],
        [MergedPr(repo=RD, number=10, merged_at=_ts(11), closes_issues=(1,))],
        window_start=_ts(10),
        window_end=_ts(12),
        now=_ts(11),  # window still open -> #2, #3 are pending
    )
    summary = summarize(verdicts)
    assert summary == {
        "total": 3,
        "hits": 1,
        "misses": 0,
        "pending": 2,
        "hit_rate": 1.0,
        "by_lane": {"a": {"hit": 1, "miss": 0, "pending": 1}, "b": {"hit": 0, "miss": 0, "pending": 1}},
    }


def test_summarize_hit_rate_none_when_nothing_scored_yet() -> None:
    verdicts = score_forecast(
        [ForecastItem(lane="a", repo=RD, issue_number=1, raw_text="#1")],
        [],
        window_start=_ts(10),
        window_end=_ts(12),
        now=_ts(11),
    )
    assert summarize(verdicts)["hit_rate"] is None


def test_build_merged_prs_query_aliases_every_repo_with_window() -> None:
    query = build_merged_prs_query([RD, UD], since=_ts(10), until=_ts(12))
    expected_window = "2026-10-31T10:00:00+00:00..2026-10-31T12:00:00+00:00"
    assert f's0: search(query: "repo:{RD} is:pr is:merged merged:{expected_window}"' in query
    assert f's1: search(query: "repo:{UD} is:pr is:merged' in query


def test_build_merged_prs_query_rejects_empty_repos() -> None:
    with pytest.raises(ValueError, match="empty"):
        build_merged_prs_query([], since=_ts(10), until=_ts(12))


def _pr_node(number=1, merged_at="2026-10-31T11:00:00Z", closes=()):
    return {
        "number": number,
        "mergedAt": merged_at,
        "closingIssuesReferences": {"nodes": [{"number": n} for n in closes]},
    }


def test_parse_merged_prs_response_normalizes_each_repo() -> None:
    payload = {"data": {"s0": {"nodes": [_pr_node(number=50, closes=(1, 2))]}}}
    prs = parse_merged_prs_response(payload, [RD])
    assert prs == [MergedPr(repo=RD, number=50, merged_at=_ts(11), closes_issues=(1, 2))]


def test_parse_merged_prs_response_rejects_missing_data() -> None:
    with pytest.raises(ValueError, match="missing GraphQL data"):
        parse_merged_prs_response({}, [RD])


def test_parse_merged_prs_response_rejects_malformed_node() -> None:
    payload = {"data": {"s0": {"nodes": [{}]}}}
    with pytest.raises(ValueError, match="malformed PR node"):
        parse_merged_prs_response(payload, [RD])


class _FakeRun:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr

    def __call__(self, cmd, **kwargs):
        return self


def test_fetch_merged_prs_payload_raises_on_gh_failure() -> None:
    run = _FakeRun("", returncode=1, stderr="boom")
    with pytest.raises(RuntimeError, match="boom"):
        fetch_merged_prs_payload([RD], since=_ts(10), until=_ts(12), run=run)


def test_run_bob_forecast_score_end_to_end(capsys) -> None:
    # repos are sorted({RD, UD}) -> [RD, UD] -> aliases s0=RD, s1=UD
    payload = json.dumps({"data": {"s0": {"nodes": [_pr_node(number=5, closes=(100,))]}, "s1": {"nodes": []}}})
    run = _FakeRun(payload)
    report = run_bob_forecast_score(
        FORECAST_MD,
        default_repo=RD,
        since=_ts(10),
        until=_ts(12),
        now=_ts(13),
        run=run,
    )
    hit = next(v for v in report["verdicts"] if v["issue_number"] == 100)
    assert hit["verdict"] == "hit"
    assert hit["merged_pr_number"] == 5
    assert report["misses"] == 2  # #101 and UD #200 did not merge in the window


def test_main_reports_missing_file(tmp_path, capsys) -> None:
    missing = tmp_path / "nope.md"
    rc = main(["--path", str(missing)])
    assert rc == 2
    assert "missing forecast file" in capsys.readouterr().err


def test_main_writes_json_report(tmp_path, capsys) -> None:
    md_path = tmp_path / "forecast.md"
    md_path.write_text(FORECAST_MD, encoding="utf-8")
    payload = json.dumps({"data": {"s0": {"nodes": []}, "s1": {"nodes": []}}})
    rc = main(
        ["--path", str(md_path), "--since", "2026-10-31T10:00:00Z", "--until", "2026-10-31T12:00:00Z"],
        run=_FakeRun(payload),
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 3
    assert out["window"] == {"since": "2026-10-31T10:00:00+00:00", "until": "2026-10-31T12:00:00+00:00"}
