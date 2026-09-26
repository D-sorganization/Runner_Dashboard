"""One way to request work: ``POST /api/v1/staff/requests`` (SC-G5-1, #1497).

A request is validated per kind, recorded as a Work Item plus an ActionProposal on the
requester's own *Requests* thread, and executed through the action registry. The
dispatch service is stubbed at its seam so no runner, repository or network is used.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from server import app
from staff import dispatch_service
from staff.actions import ACTION_REGISTRY
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.thread_bus import reset_thread_bus
from staff.work_items import get_work_item_store, reset_work_item_store

_XHR = {"X-Requested-With": "XMLHttpRequest"}
URL = "/api/v1/staff/requests"

OPERATOR = Principal(
    id="operator-user",
    type="human",
    name="Operator",
    roles=["operator"],
    scopes=["staff.read", "staff.chat", "staff.dispatch", "staff.approve"],
)
REQUESTER = Principal(id="requester", type="human", name="Requester", roles=[], scopes=["staff.read", "staff.dispatch"])
READER = Principal(id="reader", type="human", name="Reader", roles=[], scopes=["staff.read"])

CALLS: list[dispatch_service.DispatchCommand] = []


@pytest.fixture(autouse=True)
def _stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_runs.sqlite3"))
    reset_conversation_store()
    reset_work_item_store()
    reset_thread_bus()
    CALLS.clear()
    app.dependency_overrides[require_principal] = lambda: OPERATOR
    yield
    app.dependency_overrides.clear()
    get_conversation_store().close()
    reset_conversation_store()
    reset_work_item_store()
    reset_thread_bus()


def _as(principal: Principal) -> None:
    app.dependency_overrides[require_principal] = lambda: principal


@pytest.fixture
def dispatch_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(cmd: dispatch_service.DispatchCommand, caller: Principal) -> dict[str, Any]:
        CALLS.append(cmd)
        if cmd.dry_run:
            return {"dry_run": True, "plan": {"role": cmd.role, "repo": cmd.repo, "argv": ["claude"]}, "machine": "m"}
        run = {"id": "run_test_1", "role": cmd.role, "repo": cmd.repo, "status": "queued"}
        return {"dry_run": False, "run": run, "machine": "m"}

    monkeypatch.setattr(dispatch_service, "dispatch_staff_run", fake)
    act = ACTION_REGISTRY.get("staff.dispatch")
    assert act is not None
    # The real verifier reads the run store; the stubbed run only exists in the fake.
    monkeypatch.setitem(ACTION_REGISTRY._actions, "staff.dispatch", replace(act, verifier=None))  # noqa: SLF001


@pytest.fixture
def dispatch_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(cmd: dispatch_service.DispatchCommand, caller: Principal) -> dict[str, Any]:
        CALLS.append(cmd)
        raise HTTPException(status_code=429, detail="dispatch rate limit exceeded")

    monkeypatch.setattr(dispatch_service, "dispatch_staff_run", fake)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, base_url="http://testserver")


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": "staff.dispatch",
        "role": "ad-hoc",
        "target": {"repo": "Runner_Dashboard", "issue": 42},
        "prompt": "fix the flaky test",
    }
    body.update(overrides)
    return body


def test_accepted_request_records_work_item_and_proposal_and_dispatches(client: TestClient, dispatch_ok: None) -> None:
    resp = client.post(URL, json=_body(), headers=_XHR)

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["state"] == "executed"
    assert data["run_id"] == "run_test_1"
    assert data["risk"] == "medium"
    store = get_conversation_store()
    thread = store.get_thread(data["thread_id"])
    assert thread is not None and "barb" in thread.participants
    prop = store.get_proposal(data["proposal_id"])
    assert prop is not None and prop.state == "done"
    assert prop.params["work_item_id"] == data["work_item_id"]
    wi = get_work_item_store().get_work_item(data["work_item_id"])
    assert wi is not None and "run_test_1" in wi.links["runs"]
    assert wi.requested_by and wi.thread_id == data["thread_id"]
    [cmd] = CALLS
    assert (cmd.role, cmd.repo, cmd.issue, cmd.work_item_id) == ("ad-hoc", "Runner_Dashboard", 42, data["work_item_id"])


def test_requests_from_one_principal_share_one_requests_thread(client: TestClient, dispatch_ok: None) -> None:
    first = client.post(URL, json=_body(), headers=_XHR).json()
    second = client.post(URL, json=_body(prompt="another"), headers=_XHR).json()
    assert first["thread_id"] == second["thread_id"]
    _as(REQUESTER)
    third = client.post(URL, json=_body(), headers=_XHR).json()
    assert third["thread_id"] != first["thread_id"]


def test_dry_run_returns_plan_and_records_nothing(client: TestClient, dispatch_ok: None) -> None:
    resp = client.post(URL, json=_body(dry_run=True), headers=_XHR)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["state"] == "planned"
    assert data["plan"]["plan"]["argv"] == ["claude"]
    assert "work_item_id" not in data and "proposal_id" not in data
    assert get_work_item_store().list_work_items() == []
    assert CALLS[0].dry_run is True


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"kind": "no.such.kind"}, "kind"),
        (_body(role=None), "role"),
        (_body(target={"repo": "R", "issue": 1, "pr": 2}), "issue or a pr"),
        (_body(target={"repo": "R", "issues": [1, 2]}), "bulk"),
        (_body(target={"repo": "R", "run_id": 7}), "run_id"),
        (_body(target={"repo": "R"}, prompt=""), "prompt"),
        (_body(profile_id="p1"), "profile_id"),
        (_body(unexpected="x"), "unexpected"),
    ],
)
def test_invalid_requests_are_rejected_before_anything_is_recorded(
    client: TestClient, dispatch_ok: None, body: dict[str, Any], fragment: str
) -> None:
    resp = client.post(URL, json=body, headers=_XHR)

    assert resp.status_code == 422, resp.text
    assert fragment in resp.text
    assert CALLS == []
    assert get_work_item_store().list_work_items() == []


def test_requester_without_the_action_scope_is_forbidden(client: TestClient, dispatch_ok: None) -> None:
    _as(READER)
    resp = client.post(URL, json=_body(), headers=_XHR)

    assert resp.status_code == 403
    assert "staff.dispatch" in resp.text
    assert get_work_item_store().list_work_items() == []


def test_request_needing_approval_is_recorded_but_not_executed(client: TestClient, dispatch_ok: None) -> None:
    _as(REQUESTER)  # may request staff.dispatch, but cannot approve a medium-risk action
    resp = client.post(URL, json=_body(), headers=_XHR)

    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["state"] == "approval_required"
    assert "staff.approve" in data["approval"]
    prop = get_conversation_store().get_proposal(data["proposal_id"])
    assert prop is not None and prop.state == "proposed"
    wi = get_work_item_store().get_work_item(data["work_item_id"])
    assert wi is not None and wi.state == "open"
    assert CALLS == []


def test_backend_failure_is_classified_and_keeps_the_request(client: TestClient, dispatch_rate_limited: None) -> None:
    resp = client.post(URL, json=_body(), headers=_XHR)

    assert resp.status_code == 429, resp.text
    error = resp.json()["error"]  # the stable v1 envelope (SC-F3)
    assert (error["code"], error["retryable"]) == ("rate_limited", True)
    assert "rate limit" in error["message"]
    detail = error["request"]
    assert detail["state"] == "failed"
    prop = get_conversation_store().get_proposal(detail["proposal_id"])
    assert prop is not None and prop.state == "failed"
    wi = get_work_item_store().get_work_item(detail["work_item_id"])
    assert wi is not None and wi.state == "blocked"
    msgs = get_conversation_store().list_messages(detail["thread_id"])
    assert any("fix the flaky test" in m.body_md for m in msgs), "the user's input is kept on the thread"
