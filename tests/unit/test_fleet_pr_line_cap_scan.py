"""Unit tests for the fleet-wide open-PR line-cap scan (RD#3643): pure logic + CLI."""

from __future__ import annotations

import datetime
import json
import subprocess

import pytest
from staff.fleet_pr_line_cap_scan import (
    LINE_CAP,
    build_graphql_query,
    build_report,
    is_exempt,
    parse_scan_response,
    scan_violations,
)

from scripts.fleet_pr_line_cap_scan import fetch_scan_payload, load_fleet_repos, main


def _pr_node(**over):
    node = {
        "number": 1,
        "title": "feat: thing",
        "additions": 10,
        "deletions": 5,
        "isDraft": False,
        "headRefName": "claude/1-thing",
        "author": {"login": "d-sorggeneralagent[bot]"},
        "labels": {"nodes": []},
    }
    node.update(over)
    node["url"] = f"https://github.com/D-sorganization/Runner_Dashboard/pull/{node['number']}"
    return node


def _normalized(node, *, repo="D-sorganization/Runner_Dashboard"):
    parsed = parse_scan_response({"data": {"r0": {"pullRequests": {"nodes": [node]}}}}, [repo])
    return parsed[repo][0]


def test_build_graphql_query_aliases_every_repo() -> None:
    query = build_graphql_query(["D-sorganization/Runner_Dashboard", "D-sorganization/UpstreamDrift"])
    assert 'r0: repository(owner: "D-sorganization", name: "Runner_Dashboard")' in query
    assert 'r1: repository(owner: "D-sorganization", name: "UpstreamDrift")' in query


@pytest.mark.parametrize(("repos", "match"), [([], "empty"), (["not-a-repo"], "invalid repo")])
def test_build_graphql_query_rejects(repos, match) -> None:
    with pytest.raises(ValueError, match=match):
        build_graphql_query(repos)


def test_parse_scan_response_groups_by_repo_and_fails_open_on_missing_repo() -> None:
    repos = ["D-sorganization/Runner_Dashboard", "D-sorganization/Renamed_Repo"]
    payload = {"data": {"r0": {"pullRequests": {"nodes": [_pr_node(number=42)]}}, "r1": None}}
    parsed = parse_scan_response(payload, repos)
    assert [pr["number"] for pr in parsed[repos[0]]] == [42]
    assert parsed[repos[0]][0]["changed_lines"] == 15
    assert parsed[repos[1]] == []


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({}, "missing GraphQL data"),
        ({"data": {"r0": {"pullRequests": {"nodes": [{"number": "nope"}]}}}}, "invalid PR number"),
    ],
)
def test_parse_scan_response_raises(payload, match) -> None:
    with pytest.raises(ValueError, match=match):
        parse_scan_response(payload, ["D-sorganization/Runner_Dashboard"])


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"isDraft": True}, True),
        ({"additions": 0, "deletions": 900}, True),
        ({"author": {"login": "dependabot[bot]"}}, True),
        ({"headRefName": "bot/sync-fleet-agents-20261031"}, True),
        ({"labels": {"nodes": [{"name": "ship-alone"}]}}, True),
        ({}, False),
    ],
)
def test_is_exempt(kwargs, expected) -> None:
    assert is_exempt(_normalized(_pr_node(**kwargs))) is expected


def test_scan_violations_flags_oversized_non_exempt_pr() -> None:
    parsed = {"D-sorganization/Runner_Dashboard": [_normalized(_pr_node(number=5, additions=400, deletions=200))]}
    violations = scan_violations(parsed)
    assert (len(violations), violations[0]["number"], violations[0]["changed_lines"]) == (1, 5, 600)


def test_scan_violations_excludes_exempt_and_under_cap() -> None:
    parsed = {
        "D-sorganization/Runner_Dashboard": [
            _normalized(_pr_node(number=5, additions=400, deletions=200, isDraft=True)),
            _normalized(_pr_node(number=6, additions=250, deletions=250)),
        ],
    }
    assert scan_violations(parsed) == []


def test_scan_violations_sorted_by_repo_then_number() -> None:
    parsed = {
        "D-sorganization/Zeus-Gas-Bottling-Proposal": [
            _normalized(_pr_node(number=2, additions=600), repo="D-sorganization/Zeus-Gas-Bottling-Proposal"),
        ],
        "D-sorganization/AffineDrift": [
            _normalized(_pr_node(number=9, additions=600), repo="D-sorganization/AffineDrift"),
        ],
    }
    assert [(v["repo"], v["number"]) for v in scan_violations(parsed)] == [
        ("D-sorganization/AffineDrift", 9),
        ("D-sorganization/Zeus-Gas-Bottling-Proposal", 2),
    ]


def test_build_report_tallies() -> None:
    parsed = {
        "D-sorganization/Runner_Dashboard": [
            _normalized(_pr_node(number=1, additions=600)),
            _normalized(_pr_node(number=2, additions=10, deletions=5)),
            _normalized(_pr_node(number=3, isDraft=True, additions=600)),
        ],
    }
    report = build_report(parsed, now=datetime.datetime(2026, 10, 31, tzinfo=datetime.UTC))
    assert (report["cap"], report["scanned_repos"], report["scanned_prs"], report["exempt_prs"]) == (
        LINE_CAP,
        1,
        3,
        1,
    )
    assert (report["violation_count"], report["generated_at"]) == (1, "2026-10-31T00:00:00+00:00")


def _fake_run(payload, *, returncode=0, stderr=""):
    def _run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout=json.dumps(payload), stderr=stderr)

    return _run


def test_fetch_scan_payload_raises_on_gh_failure() -> None:
    run = _fake_run({}, returncode=1, stderr="rate limited")
    with pytest.raises(RuntimeError, match="rate limited"):
        fetch_scan_payload(["D-sorganization/Runner_Dashboard"], run=run)


def test_load_fleet_repos_round_trip(tmp_path) -> None:
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps({"repositories": ["D-sorganization/Runner_Dashboard"]}))
    assert load_fleet_repos(policy_path) == ["D-sorganization/Runner_Dashboard"]
    policy_path.write_text(json.dumps({"repositories": []}))
    with pytest.raises(ValueError, match="repositories"):
        load_fleet_repos(policy_path)


def test_main_prints_json_report(capsys) -> None:
    payload = {"data": {"r0": {"pullRequests": {"nodes": [_pr_node(number=7, additions=400, deletions=200)]}}}}
    exit_code = main(["--repos", "D-sorganization/Runner_Dashboard"], run=_fake_run(payload))
    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert (report["scanned_repos"], report["violation_count"], report["violations"][0]["number"]) == (1, 1, 7)
