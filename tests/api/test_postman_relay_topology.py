"""Postman relay slice 5 (#3237, part of #3226): bind relay to #3073 leader lease in #3117 topology."""

from __future__ import annotations

import ast
import datetime as _dt_mod
import inspect
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from coordination import postman_relay_topology
from coordination.hub_thread_bus import HubThreadBus
from coordination.postman import BoardRef, Delivery, PostmanState
from coordination.postman_relay_topology import FencedBusPort, TopologyRelayNode, select_provider_adapter
from coordination.relay_guard import RelayGuard
from staff.barb_continuity import BarbContinuityStore
from staff.barb_lease_fence import StaleBarbLease
from staff.barb_node_leader import (
    NodeLeaderConfig,
    NodeRoleEntry,
    RelayLeadershipError,
    claim_for,
    ensure_relay_lease,
)

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
T0 = datetime(2026, 10, 29, 14, 0, 0, tzinfo=UTC)
LEASE_TTL_S = 60
ROSTER = ("claude", "codex", "antigravity")
TOPOLOGY = NodeLeaderConfig(
    nodes=(
        NodeRoleEntry(name="hub-a", role="leader", priority=1),
        NodeRoleEntry(name="node-a", role="standby", priority=2),
        NodeRoleEntry(name="node-c", role="forwarder", priority=0),
    )
)


class FakeThreadHub:
    def __init__(self) -> None:
        self.writes: list[tuple[str, dict[str, Any], str]] = []

    def thread_for(self, to: str) -> str:
        return f"th_{to}"

    def write_message(self, thread_id: str, envelope: dict[str, Any], idempotency_key: str) -> str:
        for _tid, existing, key in self.writes:
            if key == idempotency_key:
                return str(existing["id"])
        self.writes.append((thread_id, envelope, idempotency_key))
        return str(envelope["id"])


class FakeBoard:
    def __init__(self) -> None:
        self.sessions: list[dict[str, Any]] = []
        self.inboxes: dict[str, list[dict[str, Any]]] = {}
        self.registered: list[tuple[str, str, int]] = []

    def live(self, session: str, agent: str, minutes_left: int = 120) -> None:
        expires = (T0 + timedelta(minutes=minutes_left)).isoformat()
        self.sessions.append({"session": session, "agent": agent, "expires": expires})

    def read_sessions(self) -> list[dict[str, Any]]:
        return list(self.sessions)

    def read_inbox(self, session: str, repo: str) -> dict[str, Any]:
        return {"messages": list(self.inboxes.get(session, [])), "warnings": []}

    def register(self, agent: str, session: str, issue: int) -> None:
        self.registered.append((agent, session, issue))


class StaticBoardSource:
    def __init__(self, ref: BoardRef) -> None:
        self.ref = ref

    def inspect(self, issue: int) -> BoardRef:
        return self.ref


def _board_msg(mid: str, session: str, recipient: str, text: str) -> dict[str, Any]:
    return {"id": mid, "session": session, "recipient": recipient, "text": text}


def _make_bus(hub: FakeThreadHub, clock: Any) -> HubThreadBus:
    guard = RelayGuard(frozenset(ROSTER) | {"barb", "rd-barb"}, clock=clock)
    return HubThreadBus(hub, guard, clock=clock, sleep=lambda _s: None)


def test_lease_contract_covers_relay_ownership_expiry_and_standby_failover(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "barb.db")
    with pytest.raises(RelayLeadershipError, match="forwarder"):
        ensure_relay_lease(store, TOPOLOGY, node="node-c", ttl_seconds=LEASE_TTL_S, now=T0)
    with pytest.raises(RelayLeadershipError, match="not in the node-roles config"):
        ensure_relay_lease(store, TOPOLOGY, node="unknown-node", ttl_seconds=LEASE_TTL_S, now=T0)

    leader_status = ensure_relay_lease(store, TOPOLOGY, node="hub-a", ttl_seconds=LEASE_TTL_S, now=T0)
    assert leader_status["active"] is True and leader_status["promoted"] is False
    assert leader_status["claim"] == claim_for("hub-a", 1)

    standby_healthy = ensure_relay_lease(
        store, TOPOLOGY, node="node-a", ttl_seconds=LEASE_TTL_S, now=T0 + timedelta(seconds=20)
    )
    assert standby_healthy["active"] is False and standby_healthy["leader"] == "hub-a"

    lapsed = T0 + timedelta(seconds=LEASE_TTL_S + 1)
    standby_promoted = ensure_relay_lease(store, TOPOLOGY, node="node-a", ttl_seconds=LEASE_TTL_S, now=lapsed)
    assert standby_promoted["active"] is True and standby_promoted["promoted"] is True
    assert standby_promoted["claim"] == claim_for("node-a", 2)


def test_exactly_one_active_relay_and_standby_makes_zero_model_calls_while_leader_healthy(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "barb.db")
    hub, board = FakeThreadHub(), FakeBoard()
    board.live("lane-1", "claude", 101)
    board.inboxes["lane-1"] = [_board_msg("m1", "lane-2", "claude", "@claude check PR")]
    state = PostmanState(board_issue=2376)
    model_calls: list[str] = []

    def _node(name: str) -> TopologyRelayNode:
        return TopologyRelayNode(
            node=name,
            config=TOPOLOGY,
            continuity=store,
            board=board,
            bus=_make_bus(hub, lambda: T0),
            source=StaticBoardSource(BoardRef(issue=2376)),
            roster=ROSTER,
            state=state,
            ttl_seconds=LEASE_TTL_S,
            clock=lambda: T0,
            model_call_probe=lambda: model_calls.append(name),
        )

    reports = [_node(n).step() for n in ("hub-a", "node-a", "node-c")]
    assert [r.active for r in reports] == [True, False, False]
    assert [r.model_calls for r in reports] == [0, 0, 0] and model_calls == []
    assert len(hub.writes) == 1 and reports[0].woken_recipients == ("claude",)


def test_stale_leader_write_is_fenced_and_rejected(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "barb.db")
    now = T0
    hub = FakeThreadHub()
    stale_claim = ensure_relay_lease(store, TOPOLOGY, node="hub-a", ttl_seconds=LEASE_TTL_S, now=now)["claim"]
    now = T0 + timedelta(seconds=LEASE_TTL_S + 1)
    ensure_relay_lease(store, TOPOLOGY, node="node-a", ttl_seconds=LEASE_TTL_S, now=now)

    fenced_stale_bus = FencedBusPort(_make_bus(hub, lambda: now), store, stale_claim, clock=lambda: now)
    stale_delivery = Delivery("claude", "message", "request", "hello", "m_stale", "pm-" + "s" * 32, work_number=2376)
    with pytest.raises(StaleBarbLease):
        fenced_stale_bus.send(stale_delivery)
    assert hub.writes == []


def test_failover_mid_batch_loses_nothing_and_delivers_nothing_twice(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "barb.db")
    now = T0
    hub, board = FakeThreadHub(), FakeBoard()
    board.live("lane-1", "claude", 101)
    board.inboxes["lane-1"] = [_board_msg(f"m{i}", "lane-2", "claude", f"status update {i}") for i in (1, 2, 3, 4)]
    shared_state = PostmanState(board_issue=2376)

    def _on_after_send(count: int) -> None:
        nonlocal now
        if count == 2:
            now = T0 + timedelta(seconds=LEASE_TTL_S + 5)
            ensure_relay_lease(store, TOPOLOGY, node="node-a", ttl_seconds=LEASE_TTL_S, now=now)

    def _node(name: str, hook: Any = None) -> TopologyRelayNode:
        return TopologyRelayNode(
            node=name,
            config=TOPOLOGY,
            continuity=store,
            board=board,
            bus=_make_bus(hub, lambda: now),
            source=StaticBoardSource(BoardRef(issue=2376)),
            roster=ROSTER,
            state=shared_state,
            ttl_seconds=LEASE_TTL_S,
            clock=lambda: now,
            after_send_hook=hook,
        )

    rep_a = _node("hub-a", _on_after_send).step()
    assert rep_a.fenced_mid_batch is True and len(hub.writes) == 2

    rep_b = _node("node-a").step()
    assert rep_b.active is True and rep_b.fenced_mid_batch is False and len(hub.writes) == 4
    assert [env["body"].rsplit(": ", 1)[-1] for _tid, env, _key in hub.writes] == [
        f"status update {i}" for i in (1, 2, 3, 4)
    ]


def test_provider_adapter_selected_from_slice_2_capability_probe(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "hooks.json").write_text('{"hooks": {"UserPromptSubmit": ["echo ok"]}}', encoding="utf-8")
    assert select_provider_adapter("claude", env={"CLAUDE_CODE_SSE_PORT": "9999"}).mode == "push"
    assert select_provider_adapter("codex", env={}, codex_home=codex_home).mode == "hook"
    assert select_provider_adapter("agy", env={"ANTIGRAVITY_HOME": str(tmp_path / "no-agy")}).mode == "poll"


def test_relay_topology_imports_no_dispatch_or_staff_run_modules() -> None:
    tree = ast.parse(inspect.getsource(postman_relay_topology))
    imports = [
        alias.name if isinstance(n, ast.Import) else n.module
        for n in ast.walk(tree)
        if (isinstance(n, ast.Import) and [alias := n.names[0]]) or (isinstance(n, ast.ImportFrom) and n.module)
    ]
    banned = ("dispatch", "staff_run", "cloud_run", "work_request")
    assert [m for m in imports if m and any(b in m.lower() for b in banned)] == []
