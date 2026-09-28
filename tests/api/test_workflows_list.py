"""/api/workflows/list must stay bounded in GitHub calls (#1718 review).

The live Workflows page never loaded: the route spawned two sequential
``gh`` subprocesses per workflow (file content + recent runs) across 20
repos, so a cold load took minutes and spent hundreds of API calls.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import cache_utils
import pytest
from routers import runs_workflows as rw

WORKFLOW_YAML = {
    "ci.yml": "on:\n  push:\n  pull_request:\n  workflow_dispatch:\n",
    "nightly.yml": "on:\n  schedule:\n    - cron: '0 3 * * *'\n",
}


def _fake_github(calls: list[str]) -> Any:
    async def fake_gh_api(endpoint: str) -> Any:
        calls.append(endpoint)
        repo = endpoint.split("/")[3]
        if endpoint.endswith("/actions/workflows?per_page=100"):
            return {
                "workflows": [
                    {"id": 1, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active", "html_url": "u1"},
                    {"id": 2, "name": "Nightly", "path": ".github/workflows/nightly.yml", "state": "active"},
                ]
            }
        if endpoint.endswith("/actions/runs?per_page=100"):
            return {
                "workflow_runs": [
                    {
                        "id": 10 + i,
                        "workflow_id": 1,
                        "status": "completed",
                        "conclusion": "success",
                        "created_at": f"2026-09-27T0{9 - i}:00:00Z",
                        "html_url": f"r{i}",
                        "head_branch": "main",
                    }
                    for i in range(5)
                ]
            }
        if endpoint.endswith("/contents/.github/workflows"):
            return [
                {"name": name, "path": f".github/workflows/{name}", "sha": f"{repo}-{name}"} for name in WORKFLOW_YAML
            ]
        if "/git/blobs/" in endpoint:
            name = endpoint.rsplit("-", 1)[1]
            return {"content": base64.b64encode(WORKFLOW_YAML[name].encode()).decode(), "encoding": "base64"}
        raise AssertionError(f"unexpected endpoint {endpoint}")

    return fake_gh_api


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(rw, "gh_api", _fake_github(calls))
    monkeypatch.setattr(cache_utils, "_main_cache", cache_utils.Cache("workflows-list-test"))
    monkeypatch.setattr(cache_utils, "_swr_refreshes", {})
    monkeypatch.setattr(rw, "_WORKFLOW_TRIGGER_CACHE", {})

    async def fake_repos(limit: int = 30) -> list[dict]:
        return [{"name": "alpha"}, {"name": "beta"}]

    monkeypatch.setattr(rw, "_get_recent_org_repos", fake_repos)

    async def no_subprocess(*args: object, **kwargs: object) -> None:
        raise AssertionError("workflows list must not spawn gh subprocesses")

    monkeypatch.setattr(rw, "run_cmd", no_subprocess)
    return calls


@pytest.mark.unit
def test_list_is_a_fixed_number_of_calls_per_repo(isolated: list[str]) -> None:
    result = asyncio.run(rw.list_workflows())

    assert result["total"] == 4
    per_repo = [c for c in isolated if "/alpha/" in c]
    # workflows + runs + workflow dir listing + one blob per workflow file.
    assert len(per_repo) == 5


@pytest.mark.unit
def test_triggers_and_recent_runs_are_preserved(isolated: list[str]) -> None:
    result = asyncio.run(rw.list_workflows())
    by_key = {(w["repository"], w["name"]): w for w in result["workflows"]}

    ci = by_key[("alpha", "CI")]
    assert set(ci["triggers"]) == {"manual", "push_pr"}
    assert ci["latest_run"]["id"] == 10
    assert [r["id"] for r in ci["recent_runs"]] == [10, 11, 12]

    nightly = by_key[("alpha", "Nightly")]
    assert nightly["triggers"] == ["schedule"]
    assert nightly["latest_run"] is None
    assert nightly["recent_runs"] == []


@pytest.mark.unit
def test_workflow_files_are_fetched_once_per_blob_sha(isolated: list[str]) -> None:
    asyncio.run(rw.list_workflows())
    first_blobs = [c for c in isolated if "/git/blobs/" in c]
    isolated.clear()
    cache_utils.cache_delete("workflows_list")  # recompute with a warm trigger cache

    asyncio.run(rw.list_workflows())

    assert len(first_blobs) == 4
    assert [c for c in isolated if "/git/blobs/" in c] == []
