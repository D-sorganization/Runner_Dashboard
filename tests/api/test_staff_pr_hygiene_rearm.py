"""POST /api/v1/staff/pr-hygiene/rearm (#3311 slice 3).

Re-arms auto-merge on a PR that is open, not already armed and not held back,
going through ``scripts.automerge_guard.arm_auto_merge`` -- the fleet's one
sanctioned arming seam -- rather than calling ``gh pr merge --auto`` directly.
Dry run (default) only reports the plan; live execution (`dry_run=False`)
re-arms only a PR whose plan says to, and nothing else.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import gh_client
import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from staff.audit import get_audit_store, reset_audit_store

from scripts import automerge_guard

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_ADMIN = Principal(id="hygiene-bot", type="bot", name="Hygiene", roles=[], scopes=["staff.admin"])
_READ_ONLY = Principal(id="viewer", type="human", name="Viewer", roles=[], scopes=["staff.read"])
_URL = "/api/v1/staff/pr-hygiene/rearm"


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


def _pr(state: str = "open", auto_merge: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"number": 7, "state": state, "auto_merge": auto_merge}


def _not_held() -> automerge_guard.HoldVerdict:
    return automerge_guard.HoldVerdict(False)


def _held(reason: str) -> automerge_guard.HoldVerdict:
    return automerge_guard.HoldVerdict(True, (reason,))


def test_dry_run_plans_a_rearm_without_arming(client: TestClient) -> None:
    with (
        patch.object(gh_client, "get", AsyncMock(return_value=_pr())),
        patch.object(automerge_guard, "evaluate_hold", MagicMock(return_value=_not_held())) as hold,
        patch.object(automerge_guard, "arm_auto_merge") as arm,
    ):
        res = client.post(_URL, json={"items": [{"repo": "Runner_Dashboard", "pr": 7}]}, headers=_XHR)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["dry_run"] is True
    assert body["results"][0]["action"] == "rearm"
    hold.assert_called_once()
    arm.assert_not_called()


def test_an_already_armed_pr_needs_no_action(client: TestClient) -> None:
    with (
        patch.object(gh_client, "get", AsyncMock(return_value=_pr(auto_merge={"merge_method": "squash"}))),
        patch.object(automerge_guard, "evaluate_hold") as hold,
    ):
        res = client.post(_URL, json={"items": [{"repo": "Runner_Dashboard", "pr": 7}]}, headers=_XHR)
    assert res.status_code == 200, res.text
    plan = res.json()["results"][0]
    assert plan["action"] == "none"
    assert "already armed" in plan["reason"]
    hold.assert_not_called()


def test_a_closed_pr_needs_no_action(client: TestClient) -> None:
    with (
        patch.object(gh_client, "get", AsyncMock(return_value=_pr(state="closed"))),
        patch.object(automerge_guard, "evaluate_hold") as hold,
    ):
        res = client.post(_URL, json={"items": [{"repo": "Runner_Dashboard", "pr": 7}]}, headers=_XHR)
    assert res.status_code == 200, res.text
    plan = res.json()["results"][0]
    assert plan["action"] == "none"
    assert "not open" in plan["reason"]
    hold.assert_not_called()


def test_a_held_pr_is_reported_but_never_armed(client: TestClient) -> None:
    with (
        patch.object(gh_client, "get", AsyncMock(return_value=_pr())),
        patch.object(automerge_guard, "evaluate_hold", lambda *a, **k: _held("`blocked` label")),
        patch.object(automerge_guard, "arm_auto_merge") as arm,
    ):
        res = client.post(
            _URL,
            json={"items": [{"repo": "Runner_Dashboard", "pr": 7}], "dry_run": False},
            headers=_XHR,
        )
    assert res.status_code == 200, res.text
    plan = res.json()["results"][0]
    assert plan["action"] == "none"
    assert "blocked" in plan["reason"]
    arm.assert_not_called()


def test_live_run_arms_via_the_automerge_guard_seam_and_is_audited(client: TestClient) -> None:
    verdict = _not_held()
    with (
        patch.object(gh_client, "get", AsyncMock(return_value=_pr())),
        patch.object(automerge_guard, "evaluate_hold", lambda *a, **k: verdict),
        patch.object(
            automerge_guard,
            "arm_auto_merge",
            lambda repo, pr, *, strategy, **k: automerge_guard.ArmResult(True, verdict, "armed"),
        ),
    ):
        res = client.post(
            _URL,
            json={"items": [{"repo": "Runner_Dashboard", "pr": 7}], "dry_run": False},
            headers=_XHR,
        )
    assert res.status_code == 200, res.text
    result = res.json()["results"][0]
    assert result["armed"] is True
    assert result["action"] == "rearm"

    entries = get_audit_store().list_entries(action="maintenance", target="pr-hygiene-rearm")
    assert len(entries) == 1
    assert entries[0].outcome == "success"
    assert entries[0].principal == "hygiene-bot"


def test_a_failed_live_arm_is_reported_not_swallowed(client: TestClient) -> None:
    verdict = _not_held()
    with (
        patch.object(gh_client, "get", AsyncMock(return_value=_pr())),
        patch.object(automerge_guard, "evaluate_hold", lambda *a, **k: verdict),
        patch.object(
            automerge_guard,
            "arm_auto_merge",
            lambda repo, pr, *, strategy, **k: automerge_guard.ArmResult(False, verdict, "gh refused"),
        ),
    ):
        res = client.post(
            _URL,
            json={"items": [{"repo": "Runner_Dashboard", "pr": 7}], "dry_run": False},
            headers=_XHR,
        )
    assert res.status_code == 200, res.text
    result = res.json()["results"][0]
    assert result["armed"] is False
    assert result["detail"] == "gh refused"

    entries = get_audit_store().list_entries(action="maintenance", target="pr-hygiene-rearm")
    assert entries[0].outcome == "failure"


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
