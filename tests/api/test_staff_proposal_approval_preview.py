"""API tests: per-viewer approval preview and reviewed-parameter hash enforcement (#1807)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from server import app
from staff.approval_authority import params_hash
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.proposal_cards import proposal_card
from staff.thread_bus import reset_thread_bus

_XHR = {"X-Requested-With": "XMLHttpRequest"}

OWNER = Principal(
    id="operator-user",
    type="human",
    name="Operator",
    roles=["operator", "owner"],
    scopes=["staff.chat", "staff.read", "staff.approve", "staff.dispatch", "admin", "owner"],
)
APPROVER = Principal(
    id="appr", type="human", name="Appr", roles=["operator"], scopes=["staff.read", "staff.approve", "staff.dispatch"]
)
READER = Principal(id="reader", type="human", name="Reader", roles=["developer"], scopes=["staff.read"])

_current: dict[str, Principal] = {}


def _who() -> Principal:
    return _current["p"]


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, staff_launches: list[str]) -> Iterator[None]:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "preview.sqlite3"))
    reset_conversation_store()
    reset_thread_bus()
    _current["p"] = OWNER
    app.dependency_overrides[require_principal] = _who
    app.dependency_overrides[require_scope("staff.read")] = _who
    app.dependency_overrides[require_scope("staff.approve")] = _who
    store = get_conversation_store()
    yield
    app.dependency_overrides.clear()
    store.close()
    reset_conversation_store()
    reset_thread_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, base_url="http://testserver")


def _proposal(action: str = "staff.dispatch", risk: str = "medium", params: dict | None = None):
    store = get_conversation_store()
    th = store.create_thread(title="T", kind="direct", participants=["barb", "user"])
    return store.create_proposal(
        message_id="m1",
        thread_id=th.id,
        action=action,
        params=params or {"role": "librarian", "repo": "Tools"},
        risk=risk,
        principal="barb",
    )


def _state(pid: str) -> str:
    prop = get_conversation_store().get_proposal(pid)
    assert prop is not None
    return prop.state


@pytest.mark.parametrize(
    ("who", "action", "risk", "can"),
    [
        (OWNER, "staff.dispatch", "medium", True),
        (APPROVER, "staff.dispatch", "medium", True),
        (READER, "staff.dispatch", "medium", False),
        (APPROVER, "staff.hold", "high", False),
        (OWNER, "staff.hold", "high", True),
    ],
)
def test_get_reports_the_callers_authority(
    client: TestClient, who: Principal, action: str, risk: str, can: bool
) -> None:
    prop = _proposal(
        action=action, risk=risk, params={"text": "x", "applies_to": ["*"]} if action == "staff.hold" else None
    )
    _current["p"] = who
    body = client.get(f"/api/v1/staff/proposals/{prop.id}").json()
    assert body["can_approve"] is can
    assert bool(body["denial_reason"]) is (not can)
    assert body["params_hash"] == params_hash(prop.action, prop.params)


@pytest.mark.parametrize("who", [OWNER, APPROVER, READER])
def test_preview_verdict_matches_whether_decide_succeeds(client: TestClient, who: Principal) -> None:
    prop = _proposal()
    _current["p"] = who
    preview = client.get(f"/api/v1/staff/proposals/{prop.id}").json()
    resp = client.post(
        f"/api/v1/staff/proposals/{prop.id}/decide",
        json={"decision": "approved", "execute": True, "params_hash": preview["params_hash"]},
        headers=_XHR,
    )
    assert (resp.status_code == 200) is preview["can_approve"], resp.text


def test_preview_never_grants_authority(client: TestClient) -> None:
    prop = _proposal()
    _current["p"] = READER
    preview = client.get(f"/api/v1/staff/proposals/{prop.id}").json()
    assert preview["can_approve"] is False
    resp = client.post(
        f"/api/v1/staff/proposals/{prop.id}/execute", params={"params_hash": preview["params_hash"]}, headers=_XHR
    )
    assert resp.status_code in (403, 409)
    assert _state(prop.id) == "proposed"


def test_preview_is_per_viewer_and_not_stored_on_the_card() -> None:
    prop = _proposal()
    card = proposal_card(prop, description="d", proposed_by="barb")
    assert not {"can_approve", "denial_reason", "params_hash"} & set(card)


def test_decide_approve_without_hash_is_428_and_changes_nothing(client: TestClient) -> None:
    prop = _proposal()
    resp = client.post(
        f"/api/v1/staff/proposals/{prop.id}/decide", json={"decision": "approved", "execute": True}, headers=_XHR
    )
    assert resp.status_code == 428
    assert resp.json()["error"]["code"] == "params_hash_required"
    assert _state(prop.id) == "proposed"


def test_decide_approve_with_stale_hash_is_409_and_changes_nothing(client: TestClient) -> None:
    prop = _proposal()
    resp = client.post(
        f"/api/v1/staff/proposals/{prop.id}/decide",
        json={"decision": "approved", "execute": True, "params_hash": "0" * 64},
        headers=_XHR,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "params_changed"
    assert _state(prop.id) == "proposed"


def test_deny_does_not_need_a_hash(client: TestClient) -> None:
    prop = _proposal()
    resp = client.post(
        f"/api/v1/staff/proposals/{prop.id}/decide", json={"decision": "denied", "reason": "no"}, headers=_XHR
    )
    assert resp.status_code == 200 and _state(prop.id) == "denied"


def test_execute_without_hash_is_428(client: TestClient) -> None:
    prop = _proposal()
    get_conversation_store().decide_proposal(prop.id, "approved", decided_by="x")
    resp = client.post(f"/api/v1/staff/proposals/{prop.id}/execute", headers=_XHR)
    assert resp.status_code == 428
    assert resp.json()["error"]["code"] == "params_hash_required"
    assert _state(prop.id) == "approved"


def test_execute_with_stale_hash_is_409(client: TestClient) -> None:
    prop = _proposal()
    get_conversation_store().decide_proposal(prop.id, "approved", decided_by="x")
    resp = client.post(f"/api/v1/staff/proposals/{prop.id}/execute", params={"params_hash": "f" * 64}, headers=_XHR)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "params_changed"
    assert _state(prop.id) == "approved"


def test_execute_with_the_reviewed_hash_runs(client: TestClient) -> None:
    prop = _proposal()
    get_conversation_store().decide_proposal(prop.id, "approved", decided_by="x")
    resp = client.post(
        f"/api/v1/staff/proposals/{prop.id}/execute",
        params={"params_hash": params_hash(prop.action, prop.params)},
        headers=_XHR,
    )
    assert resp.status_code == 200, resp.text
    assert _state(prop.id) != "approved"
