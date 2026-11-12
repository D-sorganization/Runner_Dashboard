"""POST /api/v1/staff/pr-hygiene (#3311): batch rerun of a PR's own failed workflow runs.

Dry run (default) only reports what would be rerun; it never calls GitHub's rerun
endpoint. Live execution (``dry_run=False``) reruns failed jobs only -- it never
cancels, admin-merges, force-pushes or closes anything.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import gh_client
import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from staff.audit import get_audit_store, reset_audit_store

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_ADMIN = Principal(id="hygiene-bot", type="bot", name="Hygiene", roles=[], scopes=["staff.admin"])
_READ_ONLY = Principal(id="viewer", type="human", name="Viewer", roles=[], scopes=["staff.read"])


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_hygiene.sqlite3"))
    reset_audit_store()
    yield
    reset_audit_store()


@pytest.fixture
def client() -> Iterator[TestClient]:
    from server import app  # noqa: PLC0415

    app.dependency_overrides[require_principal] = lambda: _ADMIN
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _pr(sha: str = "abc123", state: str = "open") -> dict[str, Any]:
    return {"state": state, "head": {"sha": sha}}


def _runs_page(*runs: dict[str, Any]) -> dict[str, Any]:
    return {"workflow_runs": list(runs)}


def test_dry_run_reports_failed_runs_without_calling_github_rerun(client: TestClient) -> None:
    pr = _pr()
    page = _runs_page(
        {"id": 42, "name": "tests (3.11)", "run_attempt": 1, "status": "completed", "conclusion": "failure"},
        {"id": 43, "name": "lint", "run_attempt": 1, "status": "completed", "conclusion": "success"},
    )

    async def fake_get(path: str) -> dict[str, Any]:
        if path.endswith("/pulls/7"):
            return pr
        assert "actions/runs" in path and "head_sha=abc123" in path
        return page

    with (
        patch.object(gh_client, "get", AsyncMock(side_effect=fake_get)),
        patch.object(gh_client, "rerun_failed", AsyncMock()) as rerun,
    ):
        res = client.post(
            "/api/v1/staff/pr-hygiene",
            json={"items": [{"repo": "Runner_Dashboard", "pr": 7}]},
            headers=_XHR,
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["dry_run"] is True
    plan = body["results"][0]
    assert plan["action"] == "rerun_failed_jobs"
    assert [r["run_id"] for r in plan["failed_runs"]] == [42]
    rerun.assert_not_awaited()


def test_dry_run_is_the_default_when_omitted(client: TestClient) -> None:
    with (
        patch.object(gh_client, "get", AsyncMock(side_effect=[_pr(), _runs_page()])),
        patch.object(gh_client, "rerun_failed", AsyncMock()) as rerun,
    ):
        res = client.post(
            "/api/v1/staff/pr-hygiene",
            json={"items": [{"repo": "Runner_Dashboard", "pr": 7}]},
            headers=_XHR,
        )
    assert res.status_code == 200, res.text
    assert res.json()["dry_run"] is True
    rerun.assert_not_awaited()


def test_no_failed_runs_means_action_none(client: TestClient) -> None:
    with patch.object(gh_client, "get", AsyncMock(side_effect=[_pr(), _runs_page()])):
        res = client.post(
            "/api/v1/staff/pr-hygiene",
            json={"items": [{"repo": "Runner_Dashboard", "pr": 7}], "dry_run": True},
            headers=_XHR,
        )
    assert res.status_code == 200, res.text
    assert res.json()["results"][0]["action"] == "none"


def test_live_run_reruns_each_failed_run_and_is_audited(client: TestClient) -> None:
    pr = _pr()
    page = _runs_page(
        {"id": 42, "name": "tests (3.11)", "run_attempt": 1, "status": "completed", "conclusion": "failure"},
        {"id": 44, "name": "e2e", "run_attempt": 2, "status": "completed", "conclusion": "failure"},
    )
    with (
        patch.object(gh_client, "get", AsyncMock(side_effect=[pr, page])),
        patch.object(gh_client, "rerun_failed", AsyncMock()) as rerun,
    ):
        res = client.post(
            "/api/v1/staff/pr-hygiene",
            json={"items": [{"repo": "Runner_Dashboard", "pr": 7}], "dry_run": False},
            headers=_XHR,
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["dry_run"] is False
    applied = body["results"][0]["applied"]
    assert {a["run_id"] for a in applied} == {42, 44}
    assert all(a["rerun_requested"] for a in applied)
    assert rerun.await_count == 2
    rerun.assert_any_await("D-sorganization/Runner_Dashboard", 42)
    rerun.assert_any_await("D-sorganization/Runner_Dashboard", 44)

    entries = get_audit_store().list_entries(action="maintenance", target="pr-hygiene")
    assert len(entries) == 1
    assert entries[0].outcome == "success"
    assert entries[0].principal == "hygiene-bot"


def test_a_github_rerun_failure_is_reported_not_swallowed(client: TestClient) -> None:
    pr = _pr()
    page = _runs_page({"id": 42, "name": "tests", "run_attempt": 1, "status": "completed", "conclusion": "failure"})
    with (
        patch.object(gh_client, "get", AsyncMock(side_effect=[pr, page])),
        patch.object(gh_client, "rerun_failed", AsyncMock(side_effect=gh_client.GhServerError(409, "/rerun", "busy"))),
    ):
        res = client.post(
            "/api/v1/staff/pr-hygiene",
            json={"items": [{"repo": "Runner_Dashboard", "pr": 7}], "dry_run": False},
            headers=_XHR,
        )
    assert res.status_code == 200, res.text
    result = res.json()["results"][0]
    assert result["success"] is False
    assert result["applied"][0]["rerun_requested"] is False
    assert "busy" in result["applied"][0]["error"]

    entries = get_audit_store().list_entries(action="maintenance", target="pr-hygiene")
    assert entries[0].outcome == "failure"


def test_repo_outside_the_org_is_refused_before_calling_github(client: TestClient) -> None:
    with patch.object(gh_client, "get", AsyncMock()) as get:
        res = client.post(
            "/api/v1/staff/pr-hygiene",
            json={"items": [{"repo": "someone-else/Runner_Dashboard", "pr": 7}]},
            headers=_XHR,
        )
    assert res.status_code == 400
    get.assert_not_awaited()


def test_a_missing_pr_is_reported_as_404(client: TestClient) -> None:
    with patch.object(gh_client, "get", AsyncMock(side_effect=gh_client.GhNotFound("/repos/x/pulls/7"))):
        res = client.post(
            "/api/v1/staff/pr-hygiene",
            json={"items": [{"repo": "Runner_Dashboard", "pr": 7}]},
            headers=_XHR,
        )
    assert res.status_code == 404


def test_staff_admin_scope_is_required(client: TestClient) -> None:
    from server import app

    app.dependency_overrides[require_principal] = lambda: _READ_ONLY
    res = client.post(
        "/api/v1/staff/pr-hygiene",
        json={"items": [{"repo": "Runner_Dashboard", "pr": 7}]},
        headers=_XHR,
    )
    assert res.status_code == 403


def test_an_empty_item_list_is_rejected(client: TestClient) -> None:
    res = client.post("/api/v1/staff/pr-hygiene", json={"items": []}, headers=_XHR)
    assert res.status_code == 422


def test_more_than_the_batch_cap_is_rejected(client: TestClient) -> None:
    items = [{"repo": "Runner_Dashboard", "pr": n} for n in range(1, 25)]
    res = client.post("/api/v1/staff/pr-hygiene", json={"items": items}, headers=_XHR)
    assert res.status_code == 422
