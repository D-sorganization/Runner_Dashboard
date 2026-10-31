from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import ci_slo_check as slo  # noqa: E402

BASELINE = Path(__file__).resolve().parent / "fixtures" / "ci_slo" / "baseline_2026_10_25.json"


def _expand(spec: Mapping[str, Any], base: slo.QueueDataset) -> slo.QueueDataset:
    total, landed_groups = int(spec["merge_group_runs"]), int(spec["landed_groups"])
    wait_p90 = float(spec["merge_group_wait_p90_minutes"])
    runs, jobs, merged = [], {}, list(base.merged_prs)
    waits = [wait_p90] * max(1, int(total * 0.9)) + [wait_p90 * 0.25] * (total - max(1, int(total * 0.9)))
    for idx in range(total):
        run_id, landed = 10_000 + idx, idx < landed_groups
        sha = f"{idx:040x}" if landed else f"dead{idx:036x}"
        created = base.as_of - timedelta(minutes=total - idx)
        runs.append(
            {
                "id": run_id,
                "event": "merge_group",
                "status": "completed",
                "conclusion": "success" if landed else "failure",
                "created_at": created.isoformat().replace("+00:00", "Z"),
                "head_branch": f"gh-readonly-queue/main/pr-{2700 + idx}-{sha[:12]}",
                "head_sha": sha,
            }
        )
        wm = waits[idx]
        jobs[run_id] = [{"started_at": (created + timedelta(minutes=wm)).isoformat().replace("+00:00", "Z")}]
        if landed:
            merged.append(
                {
                    "number": 2700 + idx,
                    "merged_at": (created + timedelta(minutes=wm + 8)).isoformat().replace("+00:00", "Z"),
                    "merge_commit_sha": sha,
                    "enqueued_at": (created - timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
                }
            )
    return slo.QueueDataset(
        base.as_of, base.open_pr_count, base.queue_samples, base.merge_queue_entries, merged, runs, jobs
    )


def _baseline() -> slo.QueueDataset:
    raw = json.loads(BASELINE.read_text(encoding="utf-8"))
    base = slo.QueueDataset(
        slo._parse_ts(raw["as_of"]),
        int(raw["open_pr_count"]),
        list(raw["queue_samples"]),
        list(raw["merge_queue_entries"]),
        [],
        [],
        {},
    )
    return _expand(raw["synthetic_replay"], base)


def test_baseline_replay_and_full_report_exit() -> None:
    metrics = slo.compute_metrics(_baseline())
    assert metrics.peak_queued_runs == 416
    assert metrics.landed_built_share == pytest.approx(0.12, abs=0.02)
    assert metrics.discarded_merge_group_share == pytest.approx(0.88, abs=0.02)
    assert metrics.merge_group_wait_p90_minutes == pytest.approx(18.4, abs=0.5)
    assert slo.run_check(_baseline(), mode="full") == 1


def _merged_row(merged_at: str, enqueued_at: str) -> dict[str, str]:
    return {"number": 1, "merged_at": merged_at, "merge_commit_sha": "a" * 40, "enqueued_at": enqueued_at}


def test_stall_alarm_windows() -> None:
    stalled = slo.QueueDataset(
        datetime(2026, 10, 25, 18, 0, tzinfo=UTC),
        40,
        [{"queued_runs": 120}],
        [{"pr_number": 1}],
        [_merged_row("2026-10-25T16:45:00Z", "2026-10-25T16:00:00Z")],
        [],
        {},
    )
    healthy = slo.QueueDataset(
        datetime(2026, 10, 25, 12, 30, tzinfo=UTC),
        12,
        [{"queued_runs": 0}],
        [],
        [_merged_row("2026-10-25T12:05:00Z", "2026-10-25T11:50:00Z")],
        [],
        {},
    )
    assert slo.stall_alarm(stalled)[0] and slo.run_check(stalled, mode="stall") == 1
    assert not slo.stall_alarm(healthy)[0] and slo.run_check(healthy, mode="stall") == 0


class _Proc:
    returncode = 0
    stdout = "[]\n"
    stderr = ""


def test_gh_api_reads_with_get_even_with_query_params(monkeypatch: pytest.MonkeyPatch) -> None:
    # `gh api -f k=v` switches the method to POST unless told otherwise, which
    # turned every live read into a create call ("gh: Invalid request").
    monkeypatch.setenv("GITHUB_TOKEN", "test")
    seen: list[list[str]] = []

    def runner(cmd: list[str]) -> _Proc:
        seen.append(list(cmd))
        return _Proc()

    slo.gh_api("repos/o/r/pulls", runner=runner, params={"state": "open"})
    cmd = seen[0]
    assert cmd[cmd.index("--method") + 1] == "GET"
    assert "state=open" in cmd


class _PagedProc:
    returncode = 0
    stderr = ""

    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def test_gh_api_decodes_back_to_back_list_pages_with_no_separator(monkeypatch: pytest.MonkeyPatch) -> None:
    # `gh api --paginate` concatenates each page's JSON with no newline between
    # them, so a two-page list response arrives as `[...][...]` on one line.
    monkeypatch.setenv("GITHUB_TOKEN", "test")
    page1 = [{"number": 1}, {"number": 2}]
    page2 = [{"number": 3}]
    stdout = json.dumps(page1) + json.dumps(page2)

    def runner(cmd: list[str]) -> _PagedProc:
        return _PagedProc(stdout)

    result = slo.gh_api("repos/o/r/pulls", runner=runner)
    assert result == [{"number": 1}, {"number": 2}, {"number": 3}]


def test_gh_api_decodes_back_to_back_workflow_run_pages_with_no_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test")
    page1 = {"workflow_runs": [{"id": 1}, {"id": 2}]}
    page2 = {"workflow_runs": [{"id": 3}]}
    stdout = json.dumps(page1) + json.dumps(page2)

    def runner(cmd: list[str]) -> _PagedProc:
        return _PagedProc(stdout)

    result = slo.gh_api("repos/o/r/actions/runs", runner=runner)
    assert result == [{"id": 1}, {"id": 2}, {"id": 3}]


def test_gh_api_decodes_a_single_page_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test")
    page = {"workflow_runs": [{"id": 1}]}
    stdout = json.dumps(page)

    def runner(cmd: list[str]) -> _PagedProc:
        return _PagedProc(stdout)

    result = slo.gh_api("repos/o/r/actions/runs", runner=runner)
    assert result == page


def test_gh_api_handles_pretty_printed_and_empty_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test")
    pretty = '[\n  {"number": 1}\n]\n[\n  {"number": 2}\n]\n'

    def runner(cmd: list[str]) -> _PagedProc:
        return _PagedProc(pretty)

    assert slo.gh_api("repos/o/r/pulls", runner=runner) == [{"number": 1}, {"number": 2}]

    def empty_runner(cmd: list[str]) -> _PagedProc:
        return _PagedProc("  \n")

    assert slo.gh_api("repos/o/r/pulls", runner=empty_runner) == []


def test_gh_api_still_raises_on_malformed_trailing_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test")

    def runner(cmd: list[str]) -> _PagedProc:
        return _PagedProc('[{"number":1}][{"numb')

    with pytest.raises(json.JSONDecodeError):
        slo.gh_api("repos/o/r/pulls", runner=runner)
