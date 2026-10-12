"""API tests: the decide route evaluates approval authority exactly as the preview does (#1942)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from server import app
from staff.audit import get_audit_store, reset_audit_store
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.thread_bus import reset_thread_bus

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_BASE = "/api/v1/staff/proposals"

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
NO_ACTION_SCOPE = Principal(
    id="noact", type="human", name="NoAct", roles=["developer"], scopes=["staff.read", "staff.approve"]
)
READER = Principal(id="reader", type="human", name="Reader", roles=["developer"], scopes=["staff.read"])
FLEET_PEER = Principal(id="fleet-peer", type="bot", name="Fleet Peer", roles=["fleet-peer"])
ALL_CALLERS = [OWNER, APPROVER, NO_ACTION_SCOPE, READER, FLEET_PEER]

_HOLD = {"text": "x", "applies_to": ["*"]}
_DISPATCH = {"role": "librarian", "repo": "Tools"}

_current: dict[str, Principal] = {}


def _who() -> Principal:
    return _current["p"]


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, staff_launches: list[str]) -> Iterator[None]:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "decide.sqlite3"))
    reset_audit_store()
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
    reset_audit_store()
    reset_thread_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, base_url="http://testserver")


def _proposal(action: str = "staff.dispatch", risk: str = "medium", params: dict | None = None):
    store = get_conversation_store()
    th = store.create_thread(title="T", kind="direct", participants=["barb", "user"])
    return store.create_proposal(
        message_id="m1", thread_id=th.id, action=action, params=params or _DISPATCH, risk=risk, principal="barb"
    )


def _owner_risk():
    return _proposal(action="staff.hold", risk="high", params=_HOLD)


def _state(pid: str) -> str:
    prop = get_conversation_store().get_proposal(pid)
    assert prop is not None
    return prop.state


def _decide(client: TestClient, pid: str, who: Principal, decision: str = "approved", **extra: object):
    _current["p"] = who
    body: dict[str, object] = {"decision": decision, **extra}
    if decision == "approved" and "params_hash" not in body:
        body["params_hash"] = client.get(f"{_BASE}/{pid}").json().get("params_hash")
    return client.post(f"{_BASE}/{pid}/decide", json=body, headers=_XHR)


def _audit(action: str, thread_id: str):
    return get_audit_store().list_entries(action=action, thread_id=thread_id)


def test_non_owner_approve_of_owner_risk_is_403_coded_and_changes_nothing(client: TestClient) -> None:
    prop = _owner_risk()
    resp = _decide(client, prop.id, APPROVER)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "owner_required"
    assert _state(prop.id) == "proposed"
    assert get_conversation_store().get_proposal(prop.id).decided_by in ("", None)
    assert _audit("proposal_approve", prop.thread_id) == []


def test_refused_attempt_is_audited_with_the_verdict(client: TestClient) -> None:
    prop = _owner_risk()
    _decide(client, prop.id, APPROVER)
    refused = _audit("proposal_decide_refused", prop.thread_id)
    assert len(refused) == 1
    assert refused[0].outcome == "denied"
    assert refused[0].detail["authority"]["code"] == "owner_required"
    assert refused[0].detail["authority"]["allowed"] is False


def test_unauthorized_caller_gets_403_before_the_hash_check(client: TestClient) -> None:
    prop = _owner_risk()
    _current["p"] = APPROVER
    resp = client.post(f"{_BASE}/{prop.id}/decide", json={"decision": "approved"}, headers=_XHR)
    assert resp.status_code == 403
    assert _state(prop.id) == "proposed"


def test_authorized_approve_is_recorded_with_the_evaluated_authority(client: TestClient) -> None:
    prop = _owner_risk()
    resp = _decide(client, prop.id, OWNER)
    assert resp.status_code == 200, resp.text
    assert _state(prop.id) == "approved"
    rows = _audit("proposal_approve", prop.thread_id)
    assert len(rows) == 1
    assert rows[0].detail["authority"] == {"allowed": True, "code": "ok"}


def test_non_owner_deny_of_owner_risk_is_403_and_changes_nothing(client: TestClient) -> None:
    prop = _owner_risk()
    resp = _decide(client, prop.id, APPROVER, decision="denied", reason="no")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "owner_required"
    assert _state(prop.id) == "proposed"
    assert _audit("proposal_deny", prop.thread_id) == []


def test_authorized_deny_is_recorded_with_the_evaluated_authority(client: TestClient) -> None:
    prop = _proposal()
    resp = _decide(client, prop.id, APPROVER, decision="denied", reason="no")
    assert resp.status_code == 200, resp.text
    assert _state(prop.id) == "denied"
    rows = _audit("proposal_deny", prop.thread_id)
    assert len(rows) == 1 and rows[0].detail["authority"]["allowed"] is True


def test_replay_of_a_decided_proposal_is_400_and_records_nothing_more(client: TestClient) -> None:
    prop = _proposal()
    assert _decide(client, prop.id, APPROVER).status_code == 200
    again = _decide(client, prop.id, APPROVER)
    assert again.status_code == 400
    assert len(_audit("proposal_approve", prop.thread_id)) == 1
    flip = _decide(client, prop.id, APPROVER, decision="denied")
    assert flip.status_code == 400
    assert _state(prop.id) == "approved"


def test_unauthorized_replay_of_a_decided_proposal_learns_nothing_and_changes_nothing(client: TestClient) -> None:
    prop = _owner_risk()
    assert _decide(client, prop.id, OWNER).status_code == 200
    resp = _decide(client, prop.id, APPROVER, decision="denied")
    assert resp.status_code == 400
    assert _state(prop.id) == "approved"


def test_expired_proposal_cannot_be_approved_but_can_still_be_denied(client: TestClient) -> None:
    store = get_conversation_store()
    old = _proposal()
    stale = _proposal()
    for p in (old, stale):
        store._conn.execute("UPDATE action_proposals SET created_at = ? WHERE id = ?", ("2000-01-01T00:00:00Z", p.id))
        store._conn.commit()
    resp = _decide(client, old.id, OWNER)
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "expired"
    assert _state(old.id) == "proposed"
    assert _decide(client, stale.id, OWNER, decision="denied").status_code == 200


def test_fleet_peer_cannot_decide(client: TestClient) -> None:
    prop = _proposal()
    resp = _decide(client, prop.id, FLEET_PEER)
    assert resp.status_code == 403
    assert _state(prop.id) == "proposed"


@pytest.mark.parametrize("who", ALL_CALLERS, ids=lambda p: p.id)
@pytest.mark.parametrize("decision", ["approved", "denied"])
@pytest.mark.parametrize(
    ("action", "risk", "params"), [("staff.dispatch", "medium", _DISPATCH), ("staff.hold", "high", _HOLD)]
)
def test_preview_and_decide_agree_over_the_authority_matrix(
    client: TestClient, who: Principal, decision: str, action: str, risk: str, params: dict
) -> None:
    prop = _proposal(action=action, risk=risk, params=params)
    _current["p"] = who
    preview = client.get(f"{_BASE}/{prop.id}").json()
    resp = _decide(client, prop.id, who, decision=decision, params_hash=preview["params_hash"])
    assert (resp.status_code == 200) is preview["can_approve"], resp.text
    if not preview["can_approve"]:
        assert resp.status_code == 403
        assert _state(prop.id) == "proposed"
    else:
        assert _state(prop.id) == decision
