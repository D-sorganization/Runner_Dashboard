"""GET /api/v1/staff/scorecard/daily — daily per-model scorecard (#4564, scope item 2)."""

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

URL = "/api/v1/staff/scorecard/daily"
_XHR = {"X-Requested-With": "XMLHttpRequest"}


def _run(run_id: str, **kw: Any) -> RunRecord:
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
        "created_at": "2099-01-01T08:00:00Z",
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
            return [{"sha": "abc"}]
        if "/check-runs" in path:
            return {"check_runs": [{"status": "completed", "conclusion": "failure"}]}
        if "/pulls/" in path:
            return {
                "state": "closed",
                "created_at": "2099-01-01T08:00:00Z",
                "merged_at": "2099-01-01T10:00:00Z",
                "review_comments": 3,
            }
        return {"items": []}

    monkeypatch.setattr(gh_client, "get", fake_get)
    return calls


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(staff_outcomes.router, prefix="/api/v1/staff")
    return TestClient(app)


def test_one_row_per_utc_day_and_model(store: RunStore, github: list[str], client: TestClient) -> None:
    store.create_run(_run("a", pr_number=5))
    store.create_run(_run("b", created_at="2099-01-02T08:00:00Z", model="gpt-6.1"))
    store.create_run(_run("c", created_at="2099-01-02T09:00:00Z", model="gpt-6.1"))
    body = client.get(URL, headers=_XHR)
    assert body.status_code == 200, body.text
    payload = body.json()
    assert payload["machine"] == "Node" and payload["prs_truncated"] is False
    assert payload["since"].endswith("Z") and payload["until"].endswith("Z")
    assert [(r["date"], r["model"], r["runs"]) for r in payload["rows"]] == [
        ("2099-01-01", "claude-opus-5-5", 1),
        ("2099-01-02", "gpt-6.1", 2),
    ]
    assert payload["totals"]["runs"] == 3


def test_pr_outcomes_reach_the_row(store: RunStore, github: list[str], client: TestClient) -> None:
    store.create_run(_run("a", pr_number=5))
    row = client.get(URL, headers=_XHR).json()["rows"][0]
    assert (row["prs"], row["merged"], row["prs_unknown"]) == (1, 1, 0)
    assert row["ci_failure_rate"] == 1.0
    assert row["merge_seconds_median"] == 7200.0
    assert row["review_findings"] == 3


def test_no_github_call_for_runs_without_a_pr(store: RunStore, github: list[str], client: TestClient) -> None:
    store.create_run(_run("a"))
    store.create_run(_run("b", model="gpt-6.1", verification="not_applicable"))
    assert client.get(URL, headers=_XHR).status_code == 200
    assert github == []


def test_since_bounds_the_window_and_earlier_runs_are_dropped(
    store: RunStore, github: list[str], client: TestClient
) -> None:
    store.create_run(_run("old", created_at="2020-01-01T00:00:00Z"))
    store.create_run(_run("new", created_at="2099-01-01T00:00:00Z"))
    body = client.get(URL, params={"since": "2098-12-01"}, headers=_XHR).json()
    assert body["since"] == "2098-12-01"
    assert body["totals"]["runs"] == 1


def test_model_filter_narrows_rows_and_rollups(store: RunStore, github: list[str], client: TestClient) -> None:
    store.create_run(_run("a", model="gpt-6.1"))
    store.create_run(_run("b", model="claude-opus-5-5"))
    body = client.get(URL, params={"model": "gpt-6.1"}, headers=_XHR).json()
    assert [r["model"] for r in body["rows"]] == ["gpt-6.1"]
    assert [m["model"] for m in body["models"]] == ["gpt-6.1"]
    assert body["totals"]["runs"] == 1


def test_runs_without_a_model_report_the_unknown_bucket(store: RunStore, github: list[str], client: TestClient) -> None:
    store.create_run(_run("a", model=None))
    assert client.get(URL, headers=_XHR).json()["rows"][0]["model"] == "unknown"


BAD_QUERIES = [({"days": "0"}, "days"), ({"days": "500"}, "days"), ({"since": "yesterday"}, "since")]


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
    assert body["totals"]["ci_failure_rate"] is None and body["totals"]["merge_seconds_median"] is None


def test_truncation_is_reported(store: RunStore, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(staff_outcomes.outcomes, "MAX_PRS", 1)
    for i in range(3):
        store.create_run(_run(f"r{i}", pr_number=10 + i, created_at=f"2099-01-0{i + 1}T00:00:00Z"))
    body = client.get(URL, headers=_XHR).json()
    assert body["prs_truncated"] is True
