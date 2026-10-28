"""Hub-thread BusPort (#3235, slice 3 of #3226): guarded envelopes into staff threads via thread_bus.

Uses a fake thread hub only; no live store or HTTP. Each acceptance criterion is one focused test.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from typing import Any

import pytest
from coordination import hub_thread_bus
from coordination.hub_thread_bus import HubThreadBus
from coordination.postman import BoardRef, Delivery, Postman, PostmanState
from coordination.relay_guard import RelayGuard, RelayRejected
from tests.api.test_postman import FakeBoard, Source, msg

NOW = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)
ROSTER = ("claude", "codex")


class FakeThreadHub:
    """Minimal in-memory hub: resolve ``to`` -> thread id and record envelope writes."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, dict[str, Any], str]] = []
        self.failures_before_success = 0

    def thread_for(self, to: str) -> str:
        return f"th_{to}"

    def write_message(self, thread_id: str, envelope: dict[str, Any], idempotency_key: str) -> str:
        if self.failures_before_success:
            self.failures_before_success -= 1
            raise RuntimeError("thread hub unavailable")
        for _tid, _env, key in self.writes:
            if key == idempotency_key:
                return str(_env["id"])
        self.writes.append((thread_id, envelope, idempotency_key))
        return str(envelope["id"])


def _delivery(**overrides: object) -> Delivery:
    base = dict(
        to="claude",
        kind="message",
        type="request",
        body="hello from board",
        board_message_id="m1",
        idempotency_key="pm-" + "a" * 32,
        work_number=2376,
    )
    base.update(overrides)
    return Delivery(**base)  # type: ignore[arg-type]


def _bus(hub: FakeThreadHub | None = None, **guard_kwargs: object) -> HubThreadBus:
    hub = hub or FakeThreadHub()
    guard = RelayGuard(frozenset(ROSTER) | {"barb"}, clock=lambda: NOW, **guard_kwargs)
    return HubThreadBus(hub, guard, clock=lambda: NOW, sleep=lambda _s: None)


def test_board_message_is_delivered_once_into_the_recipients_thread() -> None:
    hub = FakeThreadHub()
    bus = _bus(hub)
    delivery = _delivery()

    first = bus.send(delivery)
    again = bus.send(delivery)

    assert first == again
    assert len(hub.writes) == 1
    thread_id, envelope, key = hub.writes[0]
    assert thread_id == "th_claude" and envelope["to"] == "claude" and key == delivery.idempotency_key


def test_a_replay_is_not_redelivered_to_the_thread() -> None:
    hub = FakeThreadHub()
    bus = _bus(hub)
    delivery = _delivery()

    bus.send(delivery)
    bus.send(delivery)

    assert len(hub.writes) == 1


def test_dry_run_writes_nothing_to_the_thread_hub() -> None:
    hub = FakeThreadHub()
    board = FakeBoard()
    board.live("lane-x", "claude", 100)
    board.inboxes["lane-x"] = [msg("m1", "lane-y", "lane-x", "ping")]
    pm = Postman(board, _bus(hub), Source(BoardRef(issue=2376)), ROSTER, PostmanState(board_issue=2376), clock=lambda: NOW)
    report = pm.cycle(dry_run=True)
    assert report.dry_run is True and hub.writes == []


def test_bus_failure_retries_then_dead_letters() -> None:
    hub = FakeThreadHub()
    hub.failures_before_success = 10
    guard = RelayGuard(frozenset(ROSTER), clock=lambda: NOW)
    bus = HubThreadBus(hub, guard, clock=lambda: NOW, sleep=lambda _s: None, retry_delays=(0.0, 0.0))

    with pytest.raises(RuntimeError):
        bus.send(_delivery())

    assert hub.writes == []
    assert len(guard.dead_letters()) == 1
    assert "unavailable" in guard.dead_letters()[0].reason.lower()


def test_secret_bearing_body_is_refused_before_any_thread_write() -> None:
    hub = FakeThreadHub()
    bus = _bus(hub)
    token = "ghp_" + "a" * 36  # pragma: allowlist secret
    delivery = _delivery(body=f"token: {token}")

    with pytest.raises(RelayRejected):
        bus.send(delivery)

    assert hub.writes == []


def test_hub_thread_port_imports_nothing_from_dispatch_or_staff_run_code() -> None:
    tree = ast.parse(inspect.getsource(hub_thread_bus))
    module_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module_names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_names.append(node.module)
    banned = ("dispatch", "staff_run", "cloud_run", "work_request")
    offenders = [n for n in module_names if any(b in n.lower() for b in banned)]
    assert offenders == []


def test_postman_live_cycle_delivers_through_hub_thread_bus() -> None:
    hub = FakeThreadHub()
    board = FakeBoard()
    board.live("lane-x", "claude", 100)
    board.inboxes["lane-x"] = [msg("m1", "lane-y", "claude", "ping")]
    state = PostmanState(board_issue=2376)
    pm = Postman(board, _bus(hub), Source(BoardRef(issue=2376)), ROSTER, state, clock=lambda: NOW)
    pm.cycle()
    pm.cycle()
    assert len(hub.writes) == 1
    assert len(state.delivered) == 1
