"""GET /api/v1/staff/outcomes (WP-1.2, #1517)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import gh_client
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import staff_outcomes
from staff.store import RunRecord, RunStore

URL = "/api/v1/staff/outcomes"
_XHR = {"X-Requested-With": "XMLHttpRequest"}


def _run(run_id: str, **kw: Any) -> RunRecord:
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
        "created_at": "2099-01-01T00:00:00Z",
        "cost_usd": 2.0,
        "verification": "verified",
    }
    return RunRecord(**{**base, **kw})


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RunStore:
    s = RunStore(tmp_path / "runs.db")
    monkeypatch.setattr(staff_outcomes, "get_runner", lambda: SimpleNamespace(store=s, machine="Node"))
    return s


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def fake_get(path: str) -> Any:
        calls.append(path)
        if path.endswith("/commits?per_page=1"):
            return []
        if "/pulls/" in path:
            return {"state": "closed", "merged_at": "2099-01-01T01:00:00Z"}
        return {"items": []}

    monkeypatch.setattr(gh_client, "get", fake_get)
    return calls


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(staff_outcomes.router, prefix="/api/v1/staff")
    return TestClient(app)


def test_scorecard_by_role_with_default_window(store: RunStore, github: list[str], client: TestClient) -> None:
    store.create_run(_run("a", pr_number=5))
    store.create_run(_run("b", role="sanitation", verification="failed"))
    resp = client.get(URL, headers=_XHR)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["group_by"] == "role" and body["machine"] == "Node" and body["prs_truncated"] is False
    assert [r["key"] for r in body["rows"]] == ["night-watch", "sanitation"]
    nw = body["rows"][0]
    assert nw["merged"] == 1 and nw["merge_rate"] == 1.0 and nw["cost_per_merged_pr"] == 2.0
    assert body["rows"][1]["merge_rate"] is None  # no PR: no data, not 0%
    assert body["totals"]["runs"] == 2


def test_no_github_call_for_runs_without_a_pr(store: RunStore, github: list[str], client: TestClient) -> None:
    store.create_run(_run("a"))
    store.create_run(_run("b", verification="not_applicable"))
    assert client.get(URL, headers=_XHR).status_code == 200
    assert github == []


@pytest.mark.parametrize("group_by", ["provider", "repo"])
def test_group_by_provider_and_repo(group_by: str, store: RunStore, github: list[str], client: TestClient) -> None:
    store.create_run(_run("a"))
    body = client.get(URL, params={"group_by": group_by}, headers=_XHR).json()
    assert body["group_by"] == group_by
    assert body["rows"][0]["key"] == ("claude" if group_by == "provider" else "Tools")


def test_runs_before_since_are_excluded(store: RunStore, github: list[str], client: TestClient) -> None:
    store.create_run(_run("old", created_at="2020-01-01T00:00:00Z"))
    body = client.get(URL, params={"since": "2021-01-01"}, headers=_XHR).json()
    assert body["since"] == "2021-01-01" and body["totals"]["runs"] == 0


BAD_QUERIES = [({"group_by": "machine"}, "group_by"), ({"since": "yesterday"}, "since")]


@pytest.mark.parametrize(("params", "field"), BAD_QUERIES)
def test_bad_query_is_422(params: dict[str, str], field: str, store: RunStore, client: TestClient) -> None:
    resp = client.get(URL, params=params, headers=_XHR)
    assert resp.status_code == 422 and field in resp.text


def test_an_unreadable_pr_is_unknown_not_a_500(
    store: RunStore, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down(path: str) -> Any:
        raise gh_client.GhServerError(502, path)

    monkeypatch.setattr(gh_client, "get", down)
    store.create_run(_run("a", pr_number=5))
    body = client.get(URL, headers=_XHR).json()
    assert body["totals"]["prs"] == 1 and body["totals"]["prs_unknown"] == 1
    assert body["totals"]["merge_rate"] is None
