"""Postman (issue #2930): board messages reach agents; presence kept; rejections visible; rollover followed.

All ports are fakes; the engine itself is pure, so each acceptance criterion is one focused test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from coordination import postman
from coordination.postman import BoardRef, ContractError, Delivery, Postman, PostmanState

NOW = datetime(2026, 10, 26, 4, 0, tzinfo=UTC)
ROSTER = ("claude", "codex")


def iso(delta_minutes: float) -> str:
    return (NOW + timedelta(minutes=delta_minutes)).isoformat()


def msg(mid: str, sender: str, recipient: str, text: str = "hello") -> dict[str, Any]:
    return {"id": mid, "session": sender, "recipient": recipient, "text": text, "repo": "Repository_Management"}


class FakeBoard:
    def __init__(self) -> None:
        self.sessions: list[dict[str, Any]] = []
        self.inboxes: dict[str, list[dict[str, Any]]] = {}
        self.warnings: list[str] = []
        self.registered: list[str] = []

    def read_sessions(self) -> list[dict[str, Any]]:
        return self.sessions

    def read_inbox(self, session: str, repo: str) -> dict[str, Any]:
        return {"messages": self.inboxes.get(session, []), "warnings": self.warnings}

    def register(self, agent: str, session: str, issue: int) -> None:
        self.registered.append(session)

    def live(self, session: str, agent: str, expires_in: float, repo: str = "Repository_Management") -> None:
        self.sessions.append({"session": session, "agent": agent, "repo": repo, "expires": iso(expires_in)})


class FakeBus:
    def __init__(self) -> None:
        self.inbox: dict[str, list[Delivery]] = {}
        self.keys: set[str] = set()
        self.acked: set[tuple[str, str]] = set()
        self.calls = 0

    def send(self, delivery: Delivery) -> str:
        self.calls += 1
        if delivery.idempotency_key not in self.keys:
            self.keys.add(delivery.idempotency_key)
            self.inbox.setdefault(delivery.to, []).append(delivery)
        return delivery.idempotency_key

    def ack(self, to: str, message_id: str) -> None:
        self.acked.add((to, message_id))


class Source:
    def __init__(self, ref: BoardRef) -> None:
        self.ref = ref

    def inspect(self, issue: int) -> BoardRef:
        return self.ref


def make(board: FakeBoard, bus: FakeBus, *, source: Source | None = None, state: PostmanState | None = None) -> Postman:
    src = source or Source(BoardRef(issue=2376))
    return Postman(board, bus, src, ROSTER, state or PostmanState(board_issue=2376), clock=lambda: NOW)


def delivered_to(bus: FakeBus, to: str) -> list[Delivery]:
    return [d for d in bus.inbox.get(to, []) if d.kind == "message"]


def test_presence_renewed_before_expiry_and_lapsed_reregistered() -> None:
    board, bus = FakeBoard(), FakeBus()
    board.live("pm-claude", "claude", expires_in=10)  # inside the renew margin
    report = make(board, bus).cycle()
    assert board.registered == ["pm-claude", "pm-codex"]  # codex had lapsed (no session), claude is due
    reasons = {a.key: a.detail for a in report.actions if a.kind == "presence"}
    assert reasons == {"presence:pm-claude": "renew", "presence:pm-codex": "re-register"}


def test_directed_message_lands_exactly_once_and_ack_marks_read() -> None:
    board, bus = FakeBoard(), FakeBus()
    board.live("lane-x", "claude", 100)
    board.inboxes["lane-x"] = [msg("m1", "lane-y", "lane-x", "ping")]
    state = PostmanState(board_issue=2376)
    pm = make(board, bus, state=state)
    pm.cycle()
    pm.cycle()  # re-run: cursor must stop duplicates
    make(board, bus, state=PostmanState.from_json(state.to_json())).cycle()  # even after a restart
    assert [d.board_message_id for d in delivered_to(bus, "lane-x")] == ["m1"]
    pm.ack("lane-x", "m1")
    assert bus.acked == {("lane-x", "m1")}


def test_broadcast_reaches_every_roster_agent_once() -> None:
    board, bus = FakeBoard(), FakeBus()
    for agent in ROSTER:
        board.live(f"pm-{agent}", agent, 100)
        board.inboxes[f"pm-{agent}"] = [msg("b1", "lane-y", "*", "all hands")]
    make(board, bus).cycle()
    assert [d.board_message_id for d in delivered_to(bus, "claude")] == ["b1"]
    assert [d.board_message_id for d in delivered_to(bus, "codex")] == ["b1"]


def test_claim_and_direct_mention_alert_barb_and_addressee() -> None:
    board, bus = FakeBoard(), FakeBus()
    board.live("lane-x", "claude", 100)
    board.inboxes["lane-x"] = [
        msg("c1", "lane-y", "*", "Claimed RD #2930 for codex"),
        msg("n1", "lane-y", "codex", "please look"),
        msg("n2", "lane-y", "*", "@Barb needs a decision"),
        msg("q1", "lane-y", "*", "nothing special"),
    ]
    make(board, bus).cycle()
    alerts = {(d.to, d.board_message_id) for batch in bus.inbox.values() for d in batch if d.kind == "alert"}
    assert ("barb", "c1") in alerts and ("codex", "c1") in alerts  # claim: Barb + the named agent
    assert ("codex", "n1") in alerts  # addressed by name
    assert ("barb", "n2") in alerts  # mention of Barb
    assert not any(mid == "q1" for _, mid in alerts)


def test_unknown_sender_and_identity_change_produce_visible_report() -> None:
    board, bus = FakeBoard(), FakeBus()
    board.live("lane-x", "claude", 100)
    board.warnings = ["Rejected unregistered sender lane-ud-7", "Rejected identity change for codex-ct-health"]
    state = PostmanState(board_issue=2376)
    report = make(board, bus, state=state).cycle()
    rejections = [a for a in report.actions if a.kind == "rejection"]
    assert {(r.payload["sender"], r.payload["reason"]) for r in rejections} == {
        ("lane-ud-7", "unknown sender"),
        ("codex-ct-health", "identity change"),
    }
    assert all("issues/2376" in r.payload["link"] for r in rejections)
    reported = [d for d in bus.inbox["barb"] if d.kind == "rejection"]
    assert len(reported) == 2 and "lane-ud-7" in reported[0].body + reported[1].body
    make(board, bus, state=PostmanState.from_json(state.to_json())).cycle()
    assert len([d for d in bus.inbox["barb"] if d.kind == "rejection"]) == 2  # reported once, not every cycle


def test_rollover_switches_to_successor_and_keeps_cursor() -> None:
    board, bus = FakeBoard(), FakeBus()
    board.live("lane-x", "claude", 100)
    board.inboxes["lane-x"] = [msg("m1", "lane-y", "lane-x")]
    state = PostmanState(board_issue=1576)
    source = Source(BoardRef(issue=1576, closed=True, successor=2376))
    pm = make(board, bus, source=source, state=state)
    report = pm.cycle()
    assert state.board_issue == 2376 and any(a.kind == "rollover" for a in report.actions)
    delivered_before = list(state.delivered)
    assert delivered_before  # cursor survived the switch
    source.ref = BoardRef(issue=2376)
    pm.cycle()
    assert state.delivered == delivered_before
    assert len(delivered_to(bus, "lane-x")) == 1


def test_dry_run_writes_nothing_but_plans_everything() -> None:
    board, bus = FakeBoard(), FakeBus()
    board.live("lane-x", "claude", 100)
    board.inboxes["lane-x"] = [msg("m1", "lane-y", "lane-x", "Claimed #1")]
    state = PostmanState(board_issue=2376)
    report = make(board, bus, state=state).cycle(dry_run=True)
    assert report.dry_run is True
    assert {a.kind for a in report.actions} >= {"presence", "message", "alert"}
    assert board.registered == [] and bus.calls == 0 and state.delivered == []


def test_contract_rejects_bad_delivery_and_unknown_ack() -> None:
    with pytest.raises(ContractError):
        Delivery(to="", kind="message", type="request", body="x", board_message_id="m", idempotency_key="k" * 8)
    with pytest.raises(ContractError):
        Delivery(to="a", kind="message", type="request", body="", board_message_id="m", idempotency_key="k" * 8)
    with pytest.raises(ContractError):
        Delivery(
            to="a", kind="message", type="request", body="x" * 5000, board_message_id="m", idempotency_key="kkkkkkkk"
        )
    pm = make(FakeBoard(), FakeBus())
    with pytest.raises(ContractError):
        pm.ack("lane-x", "never-delivered")


def test_failed_send_is_not_marked_delivered() -> None:
    board, bus = FakeBoard(), FakeBus()
    board.live("lane-x", "claude", 100)
    board.inboxes["lane-x"] = [msg("m1", "lane-y", "lane-x")]

    def boom(delivery: Delivery) -> str:
        raise RuntimeError("bus down")

    bus.send = boom  # type: ignore[method-assign]
    state = PostmanState(board_issue=2376)
    report = make(board, bus, state=state).cycle()
    assert state.delivered == [] and any(a.kind == "error" for a in report.actions)


def test_relay_guard_refuses_a_secret_body_instead_of_delivering_it() -> None:
    board, bus = FakeBoard(), FakeBus()
    board.live("lane-x", "claude", 100)
    board.inboxes["lane-x"] = [msg("m1", "lane-y", "lane-x", "token: " + "ghp_" + "a" * 36)]
    state = PostmanState(board_issue=2376)
    report = make(board, bus, state=state).cycle()
    assert delivered_to(bus, "lane-x") == []
    assert state.delivered == [] and any(a.kind == "error" for a in report.actions)


def test_state_roundtrip_is_bounded() -> None:
    state = PostmanState(board_issue=2376)
    for i in range(postman.MAX_DELIVERED + 50):
        state.mark(f"k{i}")
    assert len(state.delivered) == postman.MAX_DELIVERED and "k0" not in state.delivered
    assert PostmanState.from_json(state.to_json()).delivered == state.delivered
