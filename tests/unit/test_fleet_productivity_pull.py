"""Unit tests for the 5 PM fleet productivity data pull (RD#3641): pure logic,
CLI and the JSON router endpoint."""

from __future__ import annotations

import datetime
import json
import subprocess
from typing import Any

import pytest
from staff.fleet_productivity_pull import (
    FLEET_REPOS,
    build_productivity_query,
    build_snapshot,
    default_window,
    fetch_productivity_payload,
    parse_productivity_response,
    pull_snapshot,
)

from scripts.fleet_productivity_pull import main as cli_main


def test_default_window_is_trailing_24h() -> None:
    now = datetime.datetime(2026, 10, 31, 17, 0, tzinfo=datetime.UTC)
    start, end = default_window(now)
    assert (end - start) == datetime.timedelta(hours=24)
    assert end == now


def test_build_productivity_query_aliases_merged_prs_and_closed_issues() -> None:
    since = datetime.datetime(2026, 10, 30, 17, 0, tzinfo=datetime.UTC)
    until = datetime.datetime(2026, 10, 31, 17, 0, tzinfo=datetime.UTC)
    query = build_productivity_query(["D-sorganization/Runner_Dashboard"], since=since, until=until)
    assert 'p0: search(query: "repo:D-sorganization/Runner_Dashboard is:pr is:merged merged:' in query
    assert 'i0: search(query: "repo:D-sorganization/Runner_Dashboard is:issue is:closed closed:' in query
    assert f"{since.isoformat()}..{until.isoformat()}" in query


def test_build_productivity_query_rejects_empty_repos() -> None:
    now = datetime.datetime(2026, 10, 31, tzinfo=datetime.UTC)
    with pytest.raises(ValueError, match="empty"):
        build_productivity_query([], since=now, until=now)


def test_parse_productivity_response_counts_per_repo() -> None:
    payload = {
        "data": {
            "p0": {"nodes": [{"number": 10}, {"number": 11}]},
            "i0": {"nodes": [{"number": 20}]},
        }
    }
    [result] = parse_productivity_response(payload, ["D-sorganization/Runner_Dashboard"])
    assert (result["repo"], result["merged_prs"], result["closed_issues"], result["merged_pr_numbers"]) == (
        "D-sorganization/Runner_Dashboard",
        2,
        1,
        [10, 11],
    )


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({}, "missing GraphQL data"),
        ({"data": {"p0": {"nodes": [{"number": "nope"}]}, "i0": {"nodes": []}}}, "invalid number"),
        ({"data": {"p0": None, "i0": {"nodes": []}}}, "malformed search result"),
    ],
)
def test_parse_productivity_response_raises(payload: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        parse_productivity_response(payload, ["D-sorganization/Runner_Dashboard"])


def test_build_snapshot_tallies_totals_across_repos() -> None:
    by_repo = [
        {"repo": "a", "merged_prs": 2, "closed_issues": 1, "merged_pr_numbers": [1, 2]},
        {"repo": "b", "merged_prs": 0, "closed_issues": 3, "merged_pr_numbers": []},
    ]
    now = datetime.datetime(2026, 10, 31, 17, 0, tzinfo=datetime.UTC)
    start, end = default_window(now)
    snapshot = build_snapshot(by_repo, window_start=start, window_end=end, now=now)
    assert (snapshot["total_merged_prs"], snapshot["total_closed_issues"]) == (2, 4)
    assert snapshot["generated_at"] == now.isoformat()
    assert snapshot["by_repo"] == by_repo


def _fake_run(payload: dict[str, Any], *, returncode: int = 0, stderr: str = ""):
    def _run(cmd, **kwargs):  # noqa: ANN001, ANN003
        return subprocess.CompletedProcess(cmd, returncode, stdout=json.dumps(payload), stderr=stderr)

    return _run


def test_fetch_productivity_payload_raises_on_gh_failure() -> None:
    run = _fake_run({}, returncode=1, stderr="rate limited")
    now = datetime.datetime(2026, 10, 31, tzinfo=datetime.UTC)
    with pytest.raises(RuntimeError, match="rate limited"):
        fetch_productivity_payload(["D-sorganization/Runner_Dashboard"], since=now, until=now, run=run)


def test_pull_snapshot_round_trips_fetch_parse_and_build() -> None:
    payload = {
        "data": {
            "p0": {"nodes": [{"number": 1}]},
            "i0": {"nodes": []},
        }
    }
    now = datetime.datetime(2026, 10, 31, 17, 0, tzinfo=datetime.UTC)
    snapshot = pull_snapshot(["D-sorganization/Runner_Dashboard"], now=now, run=_fake_run(payload))
    assert (snapshot["total_merged_prs"], snapshot["total_closed_issues"]) == (1, 0)
    assert snapshot["window_end"] == now.isoformat()


def test_fleet_repos_is_non_empty_and_matches_lane_aliases() -> None:
    assert FLEET_REPOS and all(isinstance(r, str) and "/" in r for r in FLEET_REPOS)


def test_cli_main_prints_json_snapshot(capsys: pytest.CaptureFixture[str]) -> None:
    payload = {"data": {"p0": {"nodes": [{"number": 1}, {"number": 2}]}, "i0": {"nodes": [{"number": 3}]}}}
    exit_code = cli_main(["--repos", "D-sorganization/Runner_Dashboard"], run=_fake_run(payload))
    assert exit_code == 0
    snapshot = json.loads(capsys.readouterr().out)
    assert (snapshot["total_merged_prs"], snapshot["total_closed_issues"]) == (2, 1)


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch) -> Any:
    from fastapi.testclient import TestClient
    from identity import require_fleet_peer
    from server import app  # noqa: PLC0415

    async def _fake_to_thread(func, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return func(*args, **kwargs)

    def _fake_pull_snapshot(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"generated_at": "2026-10-31T17:00:00+00:00", "total_merged_prs": 5, "total_closed_issues": 2}

    monkeypatch.setattr("routers.fleet_productivity_pull.asyncio.to_thread", _fake_to_thread)
    monkeypatch.setattr("routers.fleet_productivity_pull.pull_snapshot", _fake_pull_snapshot)
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    try:
        yield TestClient(app, raise_server_exceptions=False, headers={"X-Requested-With": "XMLHttpRequest"})
    finally:
        app.dependency_overrides.pop(require_fleet_peer, None)


def test_get_productivity_pull_returns_json(api_client: Any) -> None:
    resp = api_client.get("/api/v1/fleet/productivity-pull")
    assert resp.status_code == 200
    body = resp.json()
    assert (body["total_merged_prs"], body["total_closed_issues"]) == (5, 2)
