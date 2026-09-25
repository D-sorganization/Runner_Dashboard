"""The action-proposal API enforces its design (SC-B1-G2, #1485).

- Creating a proposal needs ``staff.chat``, a registered action and a real thread and
  message; the risk always comes from the registry, never from the caller.
- Only an ``approved`` proposal executes; a ``failed`` one needs an explicit retry decision.
- The action's ``required_scope`` is enforced at execute.
- Results are never posted to a thread that does not exist.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from server import app
from staff.actions import (
    ACTION_REGISTRY,
    ActionDefinition,
    ActionResult,
    ProposalReplayError,
    execute_proposal,
    registered_risk,
)
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.thread_bus import reset_thread_bus

_XHR = {"X-Requested-With": "XMLHttpRequest"}

APPROVER = Principal(
    id="operator-user",
    type="human",
    name="Operator",
    roles=["operator"],
    scopes=["staff.chat", "staff.read", "staff.approve", "staff.dispatch"],
)
READER = Principal(id="reader", type="human", name="Reader", roles=[], scopes=["staff.read"])
APPROVER_WITHOUT_DISPATCH = Principal(
    id="approver-no-dispatch",
    type="human",
    name="Approver",
    roles=[],
    scopes=["staff.chat", "staff.read", "staff.approve"],
)


@pytest.fixture(autouse=True)
def _store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_runs.sqlite3"))
    reset_conversation_store()
    reset_thread_bus()
    app.dependency_overrides[require_principal] = lambda: APPROVER
    app.dependency_overrides[require_scope("staff.read")] = lambda: APPROVER
    app.dependency_overrides[require_scope("staff.approve")] = lambda: APPROVER
    yield
    app.dependency_overrides.clear()
    get_conversation_store().close()
    reset_conversation_store()
    reset_thread_bus()


NOOP_CALLS: list[dict[str, object]] = []


def _noop(params: dict[str, object], ctx: object) -> ActionResult:
    NOOP_CALLS.append(params)
    return ActionResult(success=True, result={"ok": True})


@pytest.fixture(autouse=True)
def _noop_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """A registered low-risk action whose executor only records the call."""
    NOOP_CALLS.clear()
    monkeypatch.setitem(
        ACTION_REGISTRY._actions,  # noqa: SLF001 - test-only registration
        "test.noop",
        ActionDefinition(
            name="test.noop", description="noop", required_scope="staff.read", risk_class="low", executor=_noop
        ),
    )


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, base_url="http://testserver")


def _thread_and_message() -> tuple[str, str]:
    store = get_conversation_store()
    th = store.create_thread(title="Hardening", kind="direct", participants=["barb", "user"])
    msg = store.add_message(thread_id=th.id, author_kind="role", author="barb", body_md="proposal")
    return th.id, msg.id


def _body(thread_id: str, message_id: str, **overrides: object) -> dict[str, object]:
    return {
        "message_id": message_id,
        "thread_id": thread_id,
        "action": "staff.dispatch",
        "params": {"role": "ad-hoc", "prompt": "hi"},
        **overrides,
    }


# ── create ─────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_create_needs_staff_chat_not_just_staff_read(client: TestClient) -> None:
    th, msg = _thread_and_message()
    app.dependency_overrides[require_principal] = lambda: READER
    resp = client.post("/api/v1/staff/proposals", json=_body(th, msg), headers=_XHR)
    assert resp.status_code == 403, resp.text
    assert get_conversation_store().list_proposals(thread_id=th) == []


@pytest.mark.unit
def test_create_rejects_an_unregistered_action(client: TestClient) -> None:
    th, msg = _thread_and_message()
    resp = client.post("/api/v1/staff/proposals", json=_body(th, msg, action="shell.exec"), headers=_XHR)
    assert resp.status_code == 422 and "shell.exec" in resp.text


@pytest.mark.unit
def test_create_rejects_a_missing_thread_or_foreign_message(client: TestClient) -> None:
    th, msg = _thread_and_message()
    other_th, other_msg = _thread_and_message()
    assert client.post("/api/v1/staff/proposals", json=_body("th_ghost", msg), headers=_XHR).status_code == 422
    assert client.post("/api/v1/staff/proposals", json=_body(th, "msg_ghost"), headers=_XHR).status_code == 422
    assert client.post("/api/v1/staff/proposals", json=_body(th, other_msg), headers=_XHR).status_code == 422
    assert get_conversation_store().list_proposals() == []


@pytest.mark.unit
def test_create_takes_risk_from_the_registry_not_the_caller(client: TestClient) -> None:
    th, msg = _thread_and_message()
    resp = client.post("/api/v1/staff/proposals", json=_body(th, msg, risk="read"), headers=_XHR)
    assert resp.status_code == 200, resp.text
    assert resp.json()["risk"] == ACTION_REGISTRY.get("staff.dispatch").risk_class == "medium"  # type: ignore[union-attr]


@pytest.mark.unit
def test_registered_risk_is_high_for_unknown_actions() -> None:
    assert registered_risk("staff.dispatch") == "medium"
    assert registered_risk("shell.exec") == "high"


# ── execute ────────────────────────────────────────────────────────────────
def _proposal(action: str = "test.noop", params: dict[str, object] | None = None, thread_id: str = "") -> str:
    th, msg = _thread_and_message()
    return (
        get_conversation_store()
        .create_proposal(
            message_id=msg,
            thread_id=thread_id or th,
            action=action,
            params=params if params is not None else {"text": "freeze"},
            risk=registered_risk(action),
            principal="barb",
        )
        .id
    )


@pytest.mark.unit
def test_execute_route_refuses_a_proposal_that_was_never_decided(client: TestClient) -> None:
    prop_id = _proposal()
    resp = client.post(f"/api/v1/staff/proposals/{prop_id}/execute", headers=_XHR)
    assert resp.status_code == 409, resp.text
    assert NOOP_CALLS == []
    assert get_conversation_store().get_proposal(prop_id).state == "proposed"  # type: ignore[union-attr]


@pytest.mark.unit
def test_execute_proposal_refuses_proposed_without_an_approval() -> None:
    prop_id = _proposal()
    with pytest.raises(ProposalReplayError, match="approved"):
        execute_proposal(prop_id, approver=APPROVER)
    assert NOOP_CALLS == []


@pytest.mark.unit
def test_failed_proposal_needs_an_explicit_retry_decision(client: TestClient) -> None:
    prop_id = _proposal()
    store = get_conversation_store()
    store.decide_proposal(prop_id, "approved", decided_by="operator-user")
    store.transition_proposal_state(prop_id, "executing")
    store.transition_proposal_state(prop_id, "failed", reason="upstream down")

    assert client.post(f"/api/v1/staff/proposals/{prop_id}/execute", headers=_XHR).status_code == 409
    retry = client.post(
        f"/api/v1/staff/proposals/{prop_id}/decide",
        json={"decision": "approved", "reason": "retry", "execute": False},
        headers=_XHR,
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["state"] == "approved"
    done = client.post(f"/api/v1/staff/proposals/{prop_id}/execute", headers=_XHR)
    assert done.status_code == 200, done.text
    assert done.json()["state"] == "done" and len(NOOP_CALLS) == 1


@pytest.mark.unit
def test_execute_enforces_the_action_required_scope() -> None:
    prop_id = _proposal("staff.dispatch", {"role": "ad-hoc", "prompt": "hi"})
    with patch("staff.action_executors.execute_staff_dispatch") as executor:
        with pytest.raises(PermissionError, match="staff.dispatch"):
            execute_proposal(prop_id, approver=APPROVER_WITHOUT_DISPATCH, approve=True)
    executor.assert_not_called()


@pytest.mark.unit
def test_result_is_not_posted_to_a_thread_that_does_not_exist() -> None:
    store = get_conversation_store()
    prop = store.create_proposal(message_id="msg_x", thread_id="th_ghost", action="test.noop", params={}, risk="low")
    res = execute_proposal(prop.id, approver=APPROVER, approve=True)
    assert res.success and NOOP_CALLS == [{}]
    assert store.list_messages("th_ghost") == []
