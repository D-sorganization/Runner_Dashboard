"""Tests for queue-shepherd GraphQL polling + recommendation report (#2929 slice 2).

Strictly read-only: every GraphQL/gh call is mocked at the subprocess boundary.
No test makes a real network or ``gh`` call.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from shared_scripts.queue_shepherd_logic import DependencyViolation, PathConflict, QueuedPR
from shared_scripts.queue_shepherd_poll import (
    QueueShepherdPollError,
    QueueShepherdReport,
    armed_unqueued_pr_numbers,
    build_report,
    fetch_queue_payload,
    poll_queue,
    queue_entries_from_graphql,
    render_brief,
)


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _entry(
    number: int, position: int | None, body: str = "", files: list[str] | None = None, next_page: bool = False
) -> dict:
    node: dict = {
        "pullRequest": {
            "number": number,
            "body": body,
            "files": {"pageInfo": {"hasNextPage": next_page}, "nodes": [{"path": f} for f in (files or [])]},
        }
    }
    if position is not None:
        node["position"] = position
    return node


def _payload(nodes: list[dict] | None = None, has_next: bool = False, armed: list[dict] | None = None) -> dict:
    repo: dict = {}
    if nodes is not None or has_next:
        repo["mergeQueue"] = {"entries": {"pageInfo": {"hasNextPage": has_next}, "nodes": nodes or []}}
    else:
        repo["mergeQueue"] = {"entries": None}
    if armed is not None:
        repo["armedPullRequests"] = {"nodes": armed}
    return {"repository": repo}


# ---------------------------------------------------------------------------
# queue_entries_from_graphql
# ---------------------------------------------------------------------------


def test_maps_graphql_entries_to_queued_pr_snapshot() -> None:
    payload = _payload(
        [
            _entry(2780, 1, body="Depends on #2775", files=["a.py"]),
            _entry(2775, 2, files=["a.py", "b.py"]),
        ]
    )

    entries = queue_entries_from_graphql(payload)

    assert entries == [
        QueuedPR(number=2780, position=1, files=frozenset({"a.py"}), depends_on=frozenset({2775})),
        QueuedPR(number=2775, position=2, files=frozenset({"a.py", "b.py"})),
    ]


def test_no_merge_queue_entries_returns_empty_list() -> None:
    assert queue_entries_from_graphql(_payload(None)) == []


def test_missing_position_falls_back_to_array_order() -> None:
    entries = queue_entries_from_graphql(_payload([_entry(100, None)]))

    assert entries == [QueuedPR(number=100, position=1)]


def test_truncated_entries_page_raises() -> None:
    with pytest.raises(QueueShepherdPollError, match="hasNextPage"):
        queue_entries_from_graphql(_payload([_entry(1, 1)], has_next=True))


def test_truncated_files_page_raises() -> None:
    with pytest.raises(QueueShepherdPollError, match="files"):
        queue_entries_from_graphql(_payload([_entry(1, 1, files=["a.py"], next_page=True)]))


def test_depends_on_parses_closes_fixes_resolves_refs_and_depends_on() -> None:
    body = "Closes #1\nFixes #2\nresolves #3\nDepends on #4\nRefs #5\nsee #6"
    entries = queue_entries_from_graphql(_payload([_entry(10, 1, body=body)]))

    assert entries[0].depends_on == frozenset({1, 2, 3, 4, 5})


# ---------------------------------------------------------------------------
# armed_unqueued_pr_numbers / build_report / render_brief
# ---------------------------------------------------------------------------


def test_armed_unqueued_excludes_prs_already_in_queue() -> None:
    payload = _payload(
        None,
        armed=[
            {"number": 50, "autoMergeRequest": {"enabledAt": "2026-01-01T00:00:00Z"}},
            {"number": 2780, "autoMergeRequest": {"enabledAt": "2026-01-01T00:00:00Z"}},
            {"number": 60, "autoMergeRequest": None},
        ],
    )

    assert armed_unqueued_pr_numbers(payload, {2780}) == [50]


def test_build_report_surfaces_violations_and_conflicts() -> None:
    entries = [
        QueuedPR(number=2780, position=1, depends_on=frozenset({2775}), files=frozenset({"a.py"})),
        QueuedPR(number=2775, position=2, files=frozenset({"a.py"})),
    ]

    report = build_report("o/r", entries, armed_not_queued=[90])

    assert report.queue_depth == 2
    assert report.dependency_violations == (
        DependencyViolation(pr=2780, pr_position=1, blocked_by=2775, blocked_by_position=2),
    )
    assert report.path_conflicts == (PathConflict(2780, 1, 2775, 2, frozenset({"a.py"})),)
    assert report.armed_not_queued == (90,)


def test_build_report_clean_queue() -> None:
    report = build_report("o/r", [QueuedPR(number=1, position=1)])

    assert (report.dependency_violations, report.path_conflicts, report.armed_not_queued) == ((), (), ())


def test_render_brief_reports_clean_queue() -> None:
    brief = render_brief(QueueShepherdReport(repo="o/r", queue_depth=1))

    assert "o/r" in brief and "depth=1" in brief
    assert "dependency-order: clean" in brief and "path-conflict: none" in brief


def test_render_brief_reports_violations_and_conflicts_and_armed() -> None:
    report = QueueShepherdReport(
        repo="o/r",
        queue_depth=2,
        dependency_violations=(DependencyViolation(pr=2780, pr_position=1, blocked_by=2775, blocked_by_position=2),),
        path_conflicts=(PathConflict(2780, 1, 2775, 2, frozenset({"a.py"})),),
        armed_not_queued=(90,),
    )

    brief = render_brief(report)

    assert "#2780" in brief and "#2775" in brief and "a.py" in brief and "#90" in brief


# ---------------------------------------------------------------------------
# fetch_queue_payload / poll_queue — subprocess boundary is mocked
# ---------------------------------------------------------------------------


def test_fetch_queue_payload_parses_gh_stdout() -> None:
    expected = _payload(None)
    calls: list[list[str]] = []

    def fake_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        return _completed(stdout=json.dumps({"data": expected}))

    payload = fetch_queue_payload("o", "r", runner=fake_runner)

    assert payload == expected
    assert calls[0][:3] == ["gh", "api", "graphql"]
    assert "owner=o" in calls[0] and "name=r" in calls[0]


def test_fetch_queue_payload_raises_on_nonzero_exit() -> None:
    def fake_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return _completed(returncode=1, stderr="boom")

    with pytest.raises(QueueShepherdPollError, match="boom"):
        fetch_queue_payload("o", "r", runner=fake_runner)


def test_fetch_queue_payload_raises_on_graphql_errors() -> None:
    def fake_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return _completed(stdout=json.dumps({"errors": [{"message": "nope"}]}))

    with pytest.raises(QueueShepherdPollError, match="nope"):
        fetch_queue_payload("o", "r", runner=fake_runner)


def test_poll_queue_fetches_maps_and_builds_report_without_mutating_anything() -> None:
    gql_payload = {
        "data": _payload(
            [_entry(2780, 1, body="Depends on #2775", files=["a.py"]), _entry(2775, 2, files=["a.py"])],
            armed=[{"number": 90, "autoMergeRequest": {"enabledAt": "x"}}],
        )
    }
    calls: list[list[str]] = []

    def fake_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        return _completed(stdout=json.dumps(gql_payload))

    report = poll_queue("o", "r", runner=fake_runner)

    # Exactly one read call; no mutation subcommand (enqueue/merge/cancel) ever runs.
    assert len(calls) == 1
    joined = " ".join(" ".join(c) for c in calls)
    for forbidden in ("enqueuePullRequest", "mergePullRequest", "cancel", "-X POST", "-X DELETE"):
        assert forbidden not in joined

    assert report.repo == "o/r"
    assert report.queue_depth == 2
    assert len(report.dependency_violations) == 1
    assert len(report.path_conflicts) == 1
    assert report.armed_not_queued == (90,)
