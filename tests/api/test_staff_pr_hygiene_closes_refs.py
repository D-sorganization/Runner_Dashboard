"""POST /api/v1/staff/pr-hygiene/closes-refs (#3311 slice 2).

A `Closes #N` in a PR body that targets an epic tracker (label `epic` or
`type:epic`) would wrongly auto-close the tracker on merge, since a tracker is
done only when every linked sub-issue is. Dry run (default) only reports which
PRs have such a mismatch; live execution (`dry_run=False`) retexts only those
`Closes #N` occurrences to `Refs #N` and nothing else in the body.
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
_URL = "/api/v1/staff/pr-hygiene/closes-refs"


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


def _pr(body: str) -> dict[str, Any]:
    return {"number": 7, "state": "open", "body": body}


def _issue(*label_names: str) -> dict[str, Any]:
    return {"labels": [{"name": name} for name in label_names]}


def test_dry_run_flags_closes_targeting_an_epic_without_patching(client: TestClient) -> None:
    pr = _pr("Slice 6 of #3077.\n\nCloses #3077\n")

    async def fake_get(path: str) -> dict[str, Any]:
        if path.endswith("/pulls/7"):
            return pr
        assert path.endswith("/issues/3077")
        return _issue("epic")

    with (
        patch.object(gh_client, "get", AsyncMock(side_effect=fake_get)),
        patch.object(gh_client, "patch", AsyncMock()) as gh_patch,
    ):
        res = client.post(_URL, json={"items": [{"repo": "Runner_Dashboard", "pr": 7}]}, headers=_XHR)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["dry_run"] is True
    plan = body["results"][0]
    assert plan["action"] == "fix_closes_refs"
    assert plan["flagged"] == [{"issue": 3077, "labels": ["epic"]}]
    gh_patch.assert_not_awaited()


def test_closes_targeting_a_non_epic_issue_is_left_alone(client: TestClient) -> None:
    pr = _pr("Closes #42\n")

    async def fake_get(path: str) -> dict[str, Any]:
        if path.endswith("/pulls/7"):
            return pr
        assert path.endswith("/issues/42")
        return _issue("type:task")

    with patch.object(gh_client, "get", AsyncMock(side_effect=fake_get)):
        res = client.post(_URL, json={"items": [{"repo": "Runner_Dashboard", "pr": 7}]}, headers=_XHR)
    assert res.status_code == 200, res.text
    assert res.json()["results"][0]["action"] == "none"


def test_a_body_with_no_closes_keyword_makes_no_issue_lookup(client: TestClient) -> None:
    pr = _pr("Refs #3077, see the epic for context.\n")
    with patch.object(gh_client, "get", AsyncMock(return_value=pr)) as get:
        res = client.post(_URL, json={"items": [{"repo": "Runner_Dashboard", "pr": 7}]}, headers=_XHR)
    assert res.status_code == 200, res.text
    assert res.json()["results"][0]["action"] == "none"
    assert get.await_count == 1


def test_multiple_closes_only_retexts_the_epic_ones(client: TestClient) -> None:
    pr = _pr("Closes #10\n\nCloses #20\n")

    async def fake_get(path: str) -> dict[str, Any]:
        if path.endswith("/pulls/7"):
            return pr
        if path.endswith("/issues/10"):
            return _issue("epic")
        assert path.endswith("/issues/20")
        return _issue("type:task")

    patched_body: dict[str, Any] = {}

    async def fake_patch(path: str, *, json: Any = None) -> dict[str, Any]:
        patched_body["path"] = path
        patched_body["body"] = json["body"]
        return {}

    with (
        patch.object(gh_client, "get", AsyncMock(side_effect=fake_get)),
        patch.object(gh_client, "patch", AsyncMock(side_effect=fake_patch)),
    ):
        res = client.post(
            _URL,
            json={"items": [{"repo": "Runner_Dashboard", "pr": 7}], "dry_run": False},
            headers=_XHR,
        )
    assert res.status_code == 200, res.text
    assert "Refs #10" in patched_body["body"]
    assert "Closes #10" not in patched_body["body"]
    assert "Closes #20" in patched_body["body"]

    entries = get_audit_store().list_entries(action="maintenance", target="pr-hygiene-closes-refs")
    assert len(entries) == 1
    assert entries[0].outcome == "success"
    assert entries[0].principal == "hygiene-bot"


def test_repo_outside_the_org_is_refused_before_calling_github(client: TestClient) -> None:
    with patch.object(gh_client, "get", AsyncMock()) as get:
        res = client.post(
            _URL,
            json={"items": [{"repo": "someone-else/Runner_Dashboard", "pr": 7}]},
            headers=_XHR,
        )
    assert res.status_code == 400
    get.assert_not_awaited()


def test_a_missing_pr_is_reported_as_404(client: TestClient) -> None:
    with patch.object(gh_client, "get", AsyncMock(side_effect=gh_client.GhNotFound("/repos/x/pulls/7"))):
        res = client.post(_URL, json={"items": [{"repo": "Runner_Dashboard", "pr": 7}]}, headers=_XHR)
    assert res.status_code == 404


def test_staff_admin_scope_is_required(client: TestClient) -> None:
    from server import app

    app.dependency_overrides[require_principal] = lambda: _READ_ONLY
    res = client.post(_URL, json={"items": [{"repo": "Runner_Dashboard", "pr": 7}]}, headers=_XHR)
    assert res.status_code == 403


def test_an_empty_item_list_is_rejected(client: TestClient) -> None:
    res = client.post(_URL, json={"items": []}, headers=_XHR)
    assert res.status_code == 422
