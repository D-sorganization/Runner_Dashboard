"""CLI entry tests for merge shepherd hold gate (#4248)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "backend"))
if str(_REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import merge_shepherd_pass as msp  # noqa: E402


def test_main_exits_nonzero_and_skips_graphql_when_hub_holds_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MERGE_SHEPHERD_HUB_HOLDS", "required")
    monkeypatch.delenv("RUNNER_DASHBOARD_URL", raising=False)
    monkeypatch.delenv("HUB_FLEET_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "github-app-token")

    def _should_not_run(*_a: object, **_k: object) -> None:
        raise AssertionError("graphql must not run when holds are unavailable")

    monkeypatch.setattr(msp, "_graphql", _should_not_run)
    code = msp.main(["--repo-root", str(_REPO_ROOT), "--json"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["armed"] == []
    assert payload["status"] == "holds_unavailable"
    assert str(payload["holds_unavailable"]).startswith("holds_unavailable:")


def _armed_page1_graphql_stdout() -> str:
    page1 = {
        "nodes": [{"number": i, "autoMergeRequest": None} for i in range(1, 101)],
        "pageInfo": {"hasNextPage": True, "endCursor": "page-1"},
    }
    payload = {
        "repository": {
            "mergeQueue": {"entries": {"totalCount": 0}},
            "armedPullRequests": page1,
            "pullRequests": {"nodes": []},
        }
    }
    return json.dumps({"data": payload})


def _graphql_runner_followup(cmd: list[str], *, followup_stdout: str) -> object:
    import subprocess

    is_followup = any(isinstance(part, str) and part.startswith("after=") for part in cmd)
    if is_followup:
        return subprocess.CompletedProcess(cmd, 0, followup_stdout, "")
    return subprocess.CompletedProcess(cmd, 0, _armed_page1_graphql_stdout(), "")


def test_graphql_fails_closed_when_armed_follow_up_page_unparseable() -> None:
    def runner(cmd: list[str]) -> object:
        return _graphql_runner_followup(cmd, followup_stdout=json.dumps({"data": []}))

    with pytest.raises(OSError, match="armed_pull_requests_pagination_incomplete"):
        msp._graphql("D-sorganization", "Runner_Dashboard", runner=runner)


def test_graphql_fails_closed_when_armed_only_on_broken_page_two() -> None:
    def runner(cmd: list[str]) -> object:
        return _graphql_runner_followup(cmd, followup_stdout=json.dumps({"data": None}))

    with pytest.raises(OSError, match="armed_pull_requests_pagination_incomplete"):
        msp._graphql("D-sorganization", "Runner_Dashboard", runner=runner)

    from coordination.merge_shepherd_pass import graphql_nodes

    undercount_payload = json.loads(_armed_page1_graphql_stdout())["data"]
    _, armed_count, _ = graphql_nodes(undercount_payload)
    assert armed_count == 0


def test_graphql_stitches_armed_when_follow_up_pages_complete() -> None:
    page2 = {
        "nodes": [{"number": i, "autoMergeRequest": {"enabledAt": "t"}} for i in range(101, 108)],
        "pageInfo": {"hasNextPage": False, "endCursor": "page-2"},
    }
    followup = json.dumps({"data": {"repository": {"armedPullRequests": page2}}})

    def runner(cmd: list[str]) -> object:
        return _graphql_runner_followup(cmd, followup_stdout=followup)

    payload = msp._graphql("D-sorganization", "Runner_Dashboard", runner=runner)
    from coordination.merge_shepherd_pass import graphql_nodes

    _, armed_count, _ = graphql_nodes(payload)
    assert armed_count == 7


def test_hub_fleet_token_does_not_fall_back_to_gh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HUB_FLEET_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "github-app-only")
    assert msp.hub_fleet_token_for_holds() == ""


def test_fetch_hub_holds_rejects_mixed_valid_and_malformed_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    payload = {
        "holds": [
            {"active": True, "kind": "schedule", "applies_to": ["repo:Runner_Dashboard"], "text": "hold PR #1"},
            "not-a-hold-dict",
        ]
    }

    class _Resp:
        def getcode(self) -> int:
            return 200

        def read(self) -> bytes:
            return json.dumps(payload).encode()

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _Resp())
    with pytest.raises(OSError, match="hold_row_not_object"):
        msp._fetch_hub_holds("https://hub.example", "fleet-token")


def test_resolve_hub_holds_fail_closed_on_malformed_hold_row(monkeypatch: pytest.MonkeyPatch) -> None:
    def _bad_fetch(_url: str, _token: str) -> list[dict[str, str]]:
        raise OSError("unparseable:hold_row_not_object")

    from coordination.merge_shepherd_pass import resolve_hub_holds_policy

    holds, err = resolve_hub_holds_policy(
        mode="required",
        dashboard_url="https://hub.example",
        token="fleet-token",
        fetch_holds=_bad_fetch,
    )
    assert holds == []
    assert err == "holds_unavailable:unreachable:unparseable:hold_row_not_object"
