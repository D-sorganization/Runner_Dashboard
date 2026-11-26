"""Board-secretary run tokens may propose ``board.convene`` without ``staff.chat`` (#4605).

Exercises the real mint -> Bearer -> HTTP path (no dependency override on proposals).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from identity import Principal, identity_manager, principal_has_scope, require_principal, require_scope
from server import app
from staff.action_executors import BOARD_PROPOSAL_ROLE
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.thread_bus import reset_thread_bus
from staff.tokens import mint_run_token, revoke_run_token

pytestmark = pytest.mark.integration

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.25", 52003)
_RUN_ID = "run-faa353295aad"
_MAINT_RUN = "run-maintenance-deny-4605"


@pytest.fixture
def board_secretary_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    from staff.store import RunRecord

    from staff import store as run_stores

    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(tmp_path / "identity"))
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    (tmp_path / "identity").mkdir()
    identity_manager.config_dir = tmp_path / "identity"
    identity_manager.principals_path = tmp_path / "identity" / "principals.yml"
    identity_manager.tokens_path = tmp_path / "identity" / "tokens.yml"
    identity_manager.principals = {}
    identity_manager.tokens = []
    identity_manager.load_principals()
    identity_manager.load_tokens()
    reset_conversation_store()
    reset_thread_bus()
    run_stores.reset_store()

    store = get_conversation_store()
    thread = store.create_thread(
        title="Board secretary run",
        kind="direct",
        participants=[BOARD_PROPOSAL_ROLE, "user"],
    )
    message = store.add_message(
        thread_id=thread.id,
        author_kind="role",
        author=BOARD_PROPOSAL_ROLE,
        body_md="Convene the Board for the weekly packet.",
    )

    run_stores.get_store().create_run(
        RunRecord(
            _RUN_ID,
            BOARD_PROPOSAL_ROLE,
            "claude",
            None,
            "node-c",
            "Runner_Dashboard",
            "prompt",
            "",
            "weekly board",
            status="running",
            thread_id=thread.id,
        )
    )

    yield {"thread_id": thread.id, "message_id": message.id}

    revoke_run_token(_RUN_ID)
    run_stores.reset_store()
    store.close()
    reset_conversation_store()
    reset_thread_bus()


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield TestClient(app, raise_server_exceptions=False, client=_REMOTE)


def _proposal_payload(ctx: dict[str, str]) -> dict[str, Any]:
    return {
        "message_id": ctx["message_id"],
        "thread_id": ctx["thread_id"],
        "action": "board.convene",
        "params": {"question": "Review this week's board packet.", "include_queue": True},
    }


def test_board_secretary_run_token_has_convene_scope_without_staff_chat(board_secretary_run: dict[str, str]) -> None:
    raw = mint_run_token(
        role=BOARD_PROPOSAL_ROLE,
        run_id=_RUN_ID,
        fleet_actions=[],
        ttl_seconds=300.0,
    )
    principal = identity_manager.verify_token(raw)
    assert principal is not None
    assert principal_has_scope(principal, "board.convene")
    assert principal_has_scope(principal, "staff.read")
    assert not principal_has_scope(principal, "staff.chat")
    assert not principal_has_scope(principal, "staff.approve")


def test_board_secretary_run_token_can_create_board_convene_proposal(
    client: TestClient, board_secretary_run: dict[str, str]
) -> None:
    token = mint_run_token(BOARD_PROPOSAL_ROLE, _RUN_ID, [], ttl_seconds=300.0)
    resp = client.post(
        "/api/v1/staff/proposals",
        headers={**_XHR, "Authorization": f"Bearer {token}"},
        json=_proposal_payload(board_secretary_run),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["action"] == "board.convene"
    assert resp.json()["risk"] == "medium"


def test_maintenance_run_token_cannot_create_board_convene_proposal(
    client: TestClient, board_secretary_run: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from staff.store import RunRecord

    from staff import store as run_stores

    run_stores.get_store().create_run(
        RunRecord(
            _MAINT_RUN,
            "maintenance",
            "codex",
            None,
            "node-c",
            "Runner_Dashboard",
            "prompt",
            "",
            "maint",
            status="running",
        )
    )
    token = mint_run_token("maintenance", _MAINT_RUN, ["runner.start"], ttl_seconds=300.0)
    resp = client.post(
        "/api/v1/staff/proposals",
        headers={**_XHR, "Authorization": f"Bearer {token}"},
        json=_proposal_payload(board_secretary_run),
    )
    assert resp.status_code == 403
    revoke_run_token(_MAINT_RUN)


def test_expired_board_secretary_run_token_cannot_propose(
    client: TestClient, board_secretary_run: dict[str, str]
) -> None:
    token = mint_run_token(BOARD_PROPOSAL_ROLE, _RUN_ID, [], ttl_seconds=0.05)
    time.sleep(0.1)
    resp = client.post(
        "/api/v1/staff/proposals",
        headers={**_XHR, "Authorization": f"Bearer {token}"},
        json=_proposal_payload(board_secretary_run),
    )
    assert resp.status_code == 401


def test_revoked_board_secretary_run_token_cannot_propose(
    client: TestClient, board_secretary_run: dict[str, str]
) -> None:
    token = mint_run_token(BOARD_PROPOSAL_ROLE, _RUN_ID, [], ttl_seconds=300.0)
    revoke_run_token(_RUN_ID)
    resp = client.post(
        "/api/v1/staff/proposals",
        headers={**_XHR, "Authorization": f"Bearer {token}"},
        json=_proposal_payload(board_secretary_run),
    )
    assert resp.status_code == 401


def test_board_convene_still_requires_owner_approval_after_run_proposal(
    client: TestClient, board_secretary_run: dict[str, str]
) -> None:
    from tests.staff_proposal_hash import hash_query

    token = mint_run_token(BOARD_PROPOSAL_ROLE, _RUN_ID, [], ttl_seconds=300.0)
    proposal_id = client.post(
        "/api/v1/staff/proposals",
        headers={**_XHR, "Authorization": f"Bearer {token}"},
        json=_proposal_payload(board_secretary_run),
    ).json()["id"]

    approver = Principal(
        id="owner-phone",
        type="human",
        name="Owner",
        roles=["owner"],
        scopes=["staff.approve", "board.convene"],
    )
    app.dependency_overrides[require_scope("staff.approve")] = lambda: approver
    app.dependency_overrides[require_principal] = lambda: approver
    try:
        exec_resp = client.post(
            f"/api/v1/staff/proposals/{proposal_id}/execute",
            params=hash_query(proposal_id),
            headers=_XHR,
        )
        assert exec_resp.status_code == 409
    finally:
        app.dependency_overrides.pop(require_scope("staff.approve"), None)
        app.dependency_overrides.pop(require_principal, None)
