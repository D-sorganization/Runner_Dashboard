"""Fail-closed params_hash handling on the assistant execute route, plus hash edge cases (#1807)."""

from __future__ import annotations

import dataclasses
import inspect
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from routers import assistant as assistant_router
from server import app
from staff import actions as actions_mod
from staff import conversation_proposals
from staff.actions import (
    ParamsHashMismatchError,
    ParamsHashUnavailableError,
    execute_proposal,
    stored_params_hash,
)
from staff.approval_authority import params_hash
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.proposal_preview import build_proposal_preview
from staff.thread_bus import reset_thread_bus

_XHR = {"X-Requested-With": "XMLHttpRequest"}

OWNER = Principal(
    id="operator-user",
    type="human",
    name="Operator",
    roles=["operator", "owner"],
    scopes=[
        "staff.chat",
        "staff.read",
        "staff.approve",
        "staff.dispatch",
        "assistant.chat",
        "assistant.execute",
        "admin",
        "owner",
    ],
)


def _who() -> Principal:
    return OWNER


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "assistant.sqlite3"))
    reset_conversation_store()
    reset_thread_bus()
    assistant_router._proposed_actions.clear()
    app.dependency_overrides[require_principal] = _who
    app.dependency_overrides[require_scope("assistant.chat")] = _who
    app.dependency_overrides[require_scope("assistant.execute")] = _who
    app.dependency_overrides[require_scope("staff.read")] = _who
    app.dependency_overrides[require_scope("staff.approve")] = _who
    store = get_conversation_store()
    yield
    app.dependency_overrides.clear()
    assistant_router._proposed_actions.clear()
    store.close()
    reset_conversation_store()
    reset_thread_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, base_url="http://testserver")


def _propose(client: TestClient) -> str:
    resp = client.post(
        "/api/assistant/propose-action",
        json={"user_request": "restart it", "context": {"current_tab": "overview"}},
        headers=_XHR,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["action_id"]


def _execute(client: TestClient, action_id: str):
    return client.post("/api/assistant/execute-action", json={"action_id": action_id, "approved": True}, headers=_XHR)


def _stored_state(action_id: str) -> str:
    prop = get_conversation_store().get_proposal(f"prop_{action_id}")
    assert prop is not None
    return prop.state


def test_execute_action_happy_path_still_works(client: TestClient) -> None:
    action_id = _propose(client)
    resp = _execute(client, action_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True
    assert assistant_router._proposed_actions[action_id]["approved"] is True


def test_execute_action_fails_closed_on_hash_mismatch(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    action_id = _propose(client)

    def _raise(*_a: object, **_k: object) -> None:
        raise ParamsHashMismatchError("parameters changed since they were reviewed")

    monkeypatch.setattr(actions_mod, "execute_proposal", _raise)
    resp = _execute(client, action_id)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "params_changed"
    # No side effects: the legacy in-memory action did not run, the stored proposal is untouched.
    record = assistant_router._proposed_actions[action_id]
    assert record["approved"] is False
    assert "result" not in record
    assert _stored_state(action_id) == "proposed"


def test_execute_action_fails_closed_when_hash_cannot_be_computed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    action_id = _propose(client)

    def _raise(*_a: object, **_k: object) -> str:
        raise ParamsHashUnavailableError("params are not canonically serialisable")

    monkeypatch.setattr(actions_mod, "stored_params_hash", _raise)
    resp = _execute(client, action_id)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "hash_unavailable"
    assert assistant_router._proposed_actions[action_id]["approved"] is False
    assert _stored_state(action_id) == "proposed"


# --- decide/execute hash edge cases ------------------------------------------------------------


def _proposal():
    store = get_conversation_store()
    th = store.create_thread(title="T", kind="direct", participants=["barb", "user"])
    return store.create_proposal(
        message_id="m1",
        thread_id=th.id,
        action="staff.dispatch",
        params={"role": "librarian", "repo": "Tools"},
        risk="medium",
        principal="barb",
    )


def _decide(client: TestClient, pid: str, **body: object):
    return client.post(
        f"/api/v1/staff/proposals/{pid}/decide", json={"decision": "approved", "execute": True, **body}, headers=_XHR
    )


def test_empty_string_hash_is_428(client: TestClient) -> None:
    prop = _proposal()
    resp = _decide(client, prop.id, params_hash="")
    assert resp.status_code == 428
    assert get_conversation_store().get_proposal(prop.id).state == "proposed"  # type: ignore[union-attr]


def test_overlong_hash_is_422(client: TestClient) -> None:
    prop = _proposal()
    resp = _decide(client, prop.id, params_hash="a" * 65)
    assert resp.status_code == 422
    assert get_conversation_store().get_proposal(prop.id).state == "proposed"  # type: ignore[union-attr]


def test_wrong_case_hash_is_409(client: TestClient) -> None:
    prop = _proposal()
    good = params_hash(prop.action, prop.params)
    assert good != good.upper()
    resp = _decide(client, prop.id, params_hash=good.upper())
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "params_changed"
    assert get_conversation_store().get_proposal(prop.id).state == "proposed"  # type: ignore[union-attr]


def test_mid_flight_mismatch_after_precheck_is_409_with_no_side_effects(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    prop = _proposal()
    good = params_hash(prop.action, prop.params)

    def _raced(*_a: object, **_k: object) -> None:
        # The router pre-check (its own reference) passed; the check inside execute_proposal loses the race.
        raise ParamsHashMismatchError("parameters changed since they were reviewed")

    monkeypatch.setattr(actions_mod, "verify_params_hash", _raced)
    resp = _decide(client, prop.id, params_hash=good)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "params_changed"
    # The pre-check passed so the decision was recorded, but nothing executed (no executing/done/failed).
    assert get_conversation_store().get_proposal(prop.id).state == "approved"  # type: ignore[union-attr]


def test_execute_proposal_mismatch_raised_before_any_state_change() -> None:
    prop = _proposal()
    store = get_conversation_store()
    with pytest.raises(ParamsHashMismatchError):
        execute_proposal(prop.id, approver=OWNER, store=store, approve=True, params_hash="0" * 64)
    assert store.get_proposal(prop.id).state == "proposed"  # type: ignore[union-attr]


# --- invariant: stored proposal params are immutable -------------------------------------------


def test_proposal_store_updates_never_touch_params_or_action() -> None:
    """Why ``stored_params_hash`` is safe for internal callers: params/action are write-once.

    The only UPDATEs against ``action_proposals`` set state/decided_by/decided_at/reason, so the
    hash computed from the stored record equals the hash of what was created (and reviewed).
    """
    source = inspect.getsource(conversation_proposals)
    updates = re.findall(r"UPDATE action_proposals SET (.*?) WHERE", source, flags=re.S)
    assert updates, "expected to find the proposal UPDATE statements"
    for assignment in updates:
        columns = {part.split("=")[0].strip() for part in assignment.split(",")}
        assert columns <= {"state", "decided_by", "decided_at", "reason"}, columns


def test_stored_hash_is_stable_across_decisions() -> None:
    prop = _proposal()
    store = get_conversation_store()
    before = stored_params_hash(store, prop.id)
    store.decide_proposal(prop.id, "approved", decided_by="x")
    assert stored_params_hash(store, prop.id) == before == params_hash(prop.action, prop.params)


# --- preview: unhashable params -> explicit error, not a 500 -----------------------------------


def test_preview_raises_explicit_error_for_unhashable_params() -> None:
    prop = dataclasses.replace(_proposal(), params={"x": float("nan")})
    with pytest.raises(ParamsHashUnavailableError):
        build_proposal_preview(get_conversation_store(), prop, OWNER)


def test_get_proposal_with_unhashable_params_is_422(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    prop = _proposal()

    def _unavailable(*_a: object, **_k: object) -> dict:
        raise ParamsHashUnavailableError("not canonically serialisable")

    from routers import staff_proposals

    monkeypatch.setattr(staff_proposals, "build_proposal_preview", _unavailable)
    resp = client.get(f"/api/v1/staff/proposals/{prop.id}")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "hash_unavailable"
