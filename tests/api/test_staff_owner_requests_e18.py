"""Owner-request chat auto-execute hardening (WP19 #4274, E-18).

Before E-18, a turn from a ``staff.approve`` caller auto-ran every model-proposed
action within ``REQUEST_APPROVES_RISKS`` (and Barb HIGH actions) without an explicit
per-action confirm. A prompt-injection or model drift could therefore execute under
the owner's principal as soon as they merely sent a message.

After E-18, chat turns only auto-execute when the sender lists that action in
``message.meta.confirm_actions``; otherwise proposals stay ``proposed`` for an
explicit ``POST .../proposals/{id}/decide`` (or card tap).
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
from staff.owner_requests import message_confirms_action, request_approves
from staff.thread_bus import reset_thread_bus

OWNER = Principal(
    id="__loopback__",
    type="human",
    name="Owner at Desk",
    roles=["loopback"],
    scopes=["staff.read", "staff.chat", "staff.approve"],
)
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


async def _ask_barb(
    body: str,
    reply: str,
    *,
    message_meta: dict[str, Any] | None = None,
) -> str:
    """Send *body* to Barb; return thread id once proposals settle."""

    def make_proc(*_a: Any, **_kw: Any) -> MagicMock:
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = iter([json.dumps({"type": "text", "text": reply})])
        proc.stderr = iter([])
        proc.poll.return_value = 0
        return proc

    payload: dict[str, Any] = {"body": body}
    if message_meta is not None:
        payload["meta"] = message_meta

    with patch.object(ChatTurnRunner, "_spawn_cli_process", side_effect=make_proc):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as client:
            tid = (await client.post("/api/v1/staff/threads", json={"title": "Ask Barb", "role": "barb"})).json()["id"]
            resp = await client.post(
                f"/api/v1/staff/threads/{tid}/messages",
                headers={"Idempotency-Key": f"k-{body}-{message_meta!r}"},
                json=payload,
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


def _convene_reply() -> str:
    return _reply_proposing(
        [{"action": "board.convene", "params": {"question": "Review PR #1776", "title": "PR 1776"}, "reason": "asked"}]
    )


def test_message_confirms_action_requires_meta_list() -> None:
    assert not message_confirms_action(None, "board.convene")
    assert not message_confirms_action({}, "board.convene")
    assert not message_confirms_action({"confirm_actions": "board.convene"}, "board.convene")
    assert message_confirms_action({"confirm_actions": ["board.convene"]}, "board.convene")
    assert not message_confirms_action({"confirm_actions": ["staff.hold"]}, "board.convene")


def test_request_approves_without_confirm_meta_never_auto_executes() -> None:
    """E-18: approve scope alone must not count as per-action confirmation."""
    assert not request_approves(OWNER, "board.convene", "barb", message_meta=None)
    assert not request_approves(OWNER, "board.convene", "barb", message_meta={})
    assert not request_approves(OWNER, "staff.hold", "barb", message_meta=None)


def test_request_approves_with_explicit_confirm_meta() -> None:
    meta = {"confirm_actions": ["board.convene"]}
    assert request_approves(OWNER, "board.convene", "barb", message_meta=meta)
    meta_hold = {"confirm_actions": ["staff.hold"]}
    assert request_approves(OWNER, "staff.hold", "barb", message_meta=meta_hold)


@pytest.mark.asyncio
async def test_owner_message_without_confirm_leaves_proposal_pending() -> None:
    _as(OWNER)
    tid = await _ask_barb("Take PR #1776 to the Board", _convene_reply())

    store = get_conversation_store()
    (prop,) = store.list_proposals(thread_id=tid)
    assert prop.action == "board.convene"
    assert prop.state == "proposed"
    assert not [t for t in store.list_threads() if t.meta.get("group") == "board"]


@pytest.mark.asyncio
async def test_owner_message_with_confirm_meta_runs_matching_proposal_only() -> None:
    _as(OWNER)
    tid = await _ask_barb(
        "Take PR #1776 to the Board",
        _convene_reply(),
        message_meta={"confirm_actions": ["board.convene"]},
    )

    store = get_conversation_store()
    (prop,) = store.list_proposals(thread_id=tid)
    assert prop.state == "done"
    board_threads = [t for t in store.list_threads() if t.meta.get("group") == "board"]
    assert len(board_threads) == 1


@pytest.mark.asyncio
async def test_caller_without_approve_scope_still_gets_pending_card() -> None:
    _as(CHAT_ONLY)
    meta = {"confirm_actions": ["board.convene"]}
    tid = await _ask_barb("Take PR #1776 to the Board", _convene_reply(), message_meta=meta)

    (prop,) = get_conversation_store().list_proposals(thread_id=tid)
    assert prop.state == "proposed"
