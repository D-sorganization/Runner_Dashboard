"""Barb as the owner's front door (Runner_Dashboard#1786).

When the person who sent a message may approve, the actions the answering role
proposes in that turn run at once: the request is the approval. HIGH-risk actions
and callers without ``staff.approve`` still get a pending card.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from identity import Principal, require_principal, require_scope
from server import app
from staff.chat import ChatTurnRunner
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.groups import SeatReply, SeatSpec, reset_group_runner_override, set_group_runner_override
from staff.thread_bus import reset_thread_bus

# The real Desk principal: its power comes only from the ``loopback`` role preset (#1789).
OWNER = Principal(id="__loopback__", type="human", name="Loopback development admin", roles=["loopback"])
CHAT_ONLY = Principal(id="viewer-bob", type="human", name="Bob", roles=["viewer"], scopes=["staff.read", "staff.chat"])


async def _stub_seat_runner(seat: SeatSpec, prompt: str, thread_id: str) -> SeatReply:
    return SeatReply(seat_name=seat.name, status="ok", text=f"{seat.name} view on: {prompt}")


@pytest.fixture(autouse=True)
def clean_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_runs.sqlite3"))
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "barb.yml").write_text("name: barb\nstrategy:\n  provider: claude\n", encoding="utf-8")
    monkeypatch.setenv("STAFF_ROLES_DIR", str(roles_dir))
    reset_conversation_store()
    reset_thread_bus()
    set_group_runner_override(_stub_seat_runner)
    yield
    app.dependency_overrides.clear()
    reset_group_runner_override()
    reset_conversation_store()
    reset_thread_bus()


def _as(principal: Principal) -> None:
    for dep in (require_principal, require_scope("staff.chat"), require_scope("staff.read")):
        app.dependency_overrides[dep] = lambda p=principal: p


def _reply_proposing(actions: list[dict[str, Any]]) -> str:
    return "On it.\n\n```staff-actions\n" + json.dumps(actions) + "\n```\n"


async def _ask_barb(body: str, reply: str) -> str:
    """Send *body* to Barb, whose CLI answers *reply*; return the thread id once the turn settles."""

    def make_proc(*a: Any, **kw: Any) -> MagicMock:
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = iter([json.dumps({"type": "text", "text": reply})])
        proc.stderr = iter([])
        proc.poll.return_value = 0
        return proc

    with patch.object(ChatTurnRunner, "_spawn_cli_process", side_effect=make_proc):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as client:
            tid = (await client.post("/api/v1/staff/threads", json={"title": "Ask Barb", "role": "barb"})).json()["id"]
            resp = await client.post(
                f"/api/v1/staff/threads/{tid}/messages",
                headers={"Idempotency-Key": f"k-{body}"},
                json={"body": body},
            )
            assert resp.status_code == 202
            store = get_conversation_store()
            for _ in range(60):
                props = store.list_proposals(thread_id=tid)
                if props and all(p.state not in ("proposed", "approved", "executing") for p in props):
                    break
                await asyncio.sleep(0.05)
            else:
                await asyncio.sleep(0.2)
    return tid


def _convene() -> str:
    return _reply_proposing(
        [{"action": "board.convene", "params": {"question": "Review PR #1776", "title": "PR 1776"}, "reason": "asked"}]
    )


@pytest.mark.asyncio
async def test_owner_asks_barb_to_convene_the_board_and_it_just_runs() -> None:
    _as(OWNER)
    tid = await _ask_barb("Take PR #1776 to the Board", _convene())

    store = get_conversation_store()
    (prop,) = store.list_proposals(thread_id=tid)
    assert prop.action == "board.convene"
    assert prop.state == "done"
    board_threads = [t for t in store.list_threads() if t.meta.get("group") == "board"]
    assert len(board_threads) == 1
    # The card shows the outcome, not a pending Approve button.
    card = store.get_message(prop.message_id)
    assert card is not None and card.meta["proposal"]["status"] != "pending"


@pytest.mark.asyncio
async def test_caller_without_approve_scope_still_gets_a_pending_card() -> None:
    _as(CHAT_ONLY)
    tid = await _ask_barb("Take PR #1776 to the Board", _convene())

    (prop,) = get_conversation_store().list_proposals(thread_id=tid)
    assert prop.state == "proposed"


@pytest.mark.asyncio
async def test_high_risk_action_in_an_owner_turn_keeps_its_card() -> None:
    _as(OWNER)
    reply = _reply_proposing([{"action": "staff.hold", "params": {"role": "night-watch"}, "reason": "pause"}])
    tid = await _ask_barb("Hold night-watch", reply)

    (prop,) = get_conversation_store().list_proposals(thread_id=tid)
    assert prop.action == "staff.hold"
    assert prop.state == "proposed"


def test_desk_principal_passes_the_approval_policy_through_its_role() -> None:
    """#1789: ``staff.approve`` granted by a role preset counts, not only explicit scopes."""
    from staff.actions import ACTION_REGISTRY, check_approval_policy
    from staff.conversation_models import ActionProposalRecord

    prop = ActionProposalRecord(
        id="prop_x", message_id="m", thread_id="t", action="board.convene", params={}, risk="medium", state="approved"
    )
    check_approval_policy(ACTION_REGISTRY.get("board.convene"), prop, OWNER)  # does not raise
