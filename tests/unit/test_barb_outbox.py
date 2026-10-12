"""Bounded, persistent, ordered outbox for non-authority nodes (#1800, BR-06).

Invariants: the outbox never exceeds its bounds (full => OutboxFullError, nothing silently
dropped); entries are delivered per (thread, run) in ascending version; a crash mid-delivery
loses nothing (restart recovery); every entry is delivered, pending or dead (I5).
"""

from __future__ import annotations

import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from staff.barb_authority import (
    AuthorityEpochMismatch,
    AuthorityRejected,
    AuthorityUnavailable,
    DeliveryOutcome,
)
from staff.barb_outbox import (
    OutboxEntryTooLarge,
    OutboxFullError,
    OutboxStore,
    backoff_seconds,
)
from staff.barb_outbox_drain import drain_once
from staff.conversations import ConversationStore


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


def _outbox(tmp_path: Path, clock: Clock, **kw: Any) -> tuple[OutboxStore, ConversationStore]:
    cs = ConversationStore(tmp_path / "node.sqlite3")
    kw.setdefault("max_entries", 50)
    kw.setdefault("max_bytes", 1_000_000)
    kw.setdefault("max_entry_bytes", 10_000)
    return cs.barb_outbox(clock=clock, rng=random.Random(1), **kw), cs


def _enq(box: OutboxStore, version: int, status: str = "running", run: str = "run-1", thread: str = "thr", **kw: Any):  # type: ignore[no-untyped-def]
    return box.enqueue("run_card", thread, run, version, {"status": status}, terminal=status == "succeeded", **kw)


class FakeClient:
    """Stands in for AuthorityClient; records order and can fail on demand."""

    def __init__(self, behaviour: Callable[[str, str, int], DeliveryOutcome] | None = None) -> None:
        self.sent: list[tuple[str, str, int]] = []
        self.behaviour = behaviour or (lambda *_: DeliveryOutcome.DELIVERED)

    def post(self, kind: str, thread_id: str, run_id: str, version: int, payload: dict[str, Any]) -> DeliveryOutcome:
        self.sent.append((thread_id, run_id, version))
        return self.behaviour(thread_id, run_id, version)


@pytest.mark.unit
def test_enqueue_persists_across_reopen_and_is_idempotent(tmp_path: Path, clock: Clock) -> None:
    box, cs = _outbox(tmp_path, clock)
    first = _enq(box, 1)
    again = _enq(box, 1)
    assert first == again and box.depth() == 1  # the producer is idempotent on (kind, thread, run, version)
    cs._conn.close()
    box2, _ = _outbox(tmp_path, clock)
    assert box2.depth() == 1


@pytest.mark.unit
def test_entry_count_bound_never_exceeded_and_nothing_dropped(tmp_path: Path, clock: Clock) -> None:
    box, _ = _outbox(tmp_path, clock, max_entries=3)
    for i in range(3):
        _enq(box, 1, run=f"r{i}")
    with pytest.raises(OutboxFullError):
        _enq(box, 1, run="r3")
    assert box.depth() == 3
    assert box.stats().entries <= 3


@pytest.mark.unit
def test_byte_bound_and_entry_size_cap(tmp_path: Path, clock: Clock) -> None:
    box, _ = _outbox(tmp_path, clock, max_bytes=400, max_entry_bytes=300)
    with pytest.raises(OutboxEntryTooLarge):
        box.enqueue("run_card", "thr", "r", 1, {"x": "y" * 500}, terminal=False)
    box.enqueue("run_card", "thr", "r1", 1, {"x": "y" * 150}, terminal=False)
    with pytest.raises(OutboxFullError):
        box.enqueue("run_card", "thr", "r2", 1, {"x": "y" * 250}, terminal=False)
    assert box.stats().bytes <= 400
    assert issubclass(OutboxEntryTooLarge, ValueError)


@pytest.mark.unit
def test_bound_preconditions(tmp_path: Path, clock: Clock) -> None:
    cs = ConversationStore(tmp_path / "x.sqlite3")
    for bad in ({"max_entries": 0}, {"max_bytes": 0}, {"max_entry_bytes": 0}):
        with pytest.raises(ValueError):
            cs.barb_outbox(**{"max_entries": 1, "max_bytes": 1, "max_entry_bytes": 1, **bad})


@pytest.mark.unit
def test_non_terminal_updates_coalesce_but_terminal_is_kept(tmp_path: Path, clock: Clock) -> None:
    box, _ = _outbox(tmp_path, clock, max_entries=2)
    _enq(box, 1, "queued")
    _enq(box, 2, "running")  # supersedes v1 (non-terminal, undelivered)
    assert box.depth() == 1
    _enq(box, 3, "succeeded")  # supersedes v2; the terminal entry itself is never coalesced away
    assert box.depth() == 1
    _enq(box, 4, "running", run="run-2")
    assert box.depth() == 2
    assert 3 in [e.version for e in box.pending_for("thr", "run-1")]


@pytest.mark.unit
def test_drain_delivers_in_version_order_per_key(tmp_path: Path, clock: Clock) -> None:
    box, _ = _outbox(tmp_path, clock)
    for v, s in ((3, "succeeded"), (1, "queued"), (2, "running")):
        _enq(box, v, s)
    _enq(box, 1, "queued", run="run-2")
    client = FakeClient()
    stats = drain_once(box, client, limit=10)
    # One in flight per key per pass: first pass sends the head of each key.
    while box.depth():
        drain_once(box, client, limit=10)
    run1 = [v for (_, r, v) in client.sent if r == "run-1"]
    assert run1 == sorted(run1) and run1[-1] == 3
    assert stats.delivered >= 1 and box.depth() == 0


@pytest.mark.unit
def test_unavailable_authority_retries_with_backoff_and_loses_nothing(tmp_path: Path, clock: Clock) -> None:
    box, _ = _outbox(tmp_path, clock)
    _enq(box, 1, "succeeded")

    def down(*_: Any) -> DeliveryOutcome:
        raise AuthorityUnavailable("partition")

    client = FakeClient(down)
    s1 = drain_once(box, client, limit=5)
    assert s1.retried == 1 and box.depth() == 1
    assert drain_once(box, client, limit=5).attempted == 0  # not due until the backoff elapses
    entry = box.pending_for("thr", "run-1")[0]
    assert entry.attempts == 1 and entry.next_attempt_at > clock.now
    clock.now = entry.next_attempt_at + 0.001
    client.behaviour = lambda *_: DeliveryOutcome.DELIVERED
    assert drain_once(box, client, limit=5).delivered == 1
    assert box.depth() == 0


@pytest.mark.unit
def test_backoff_bounds_and_jitter() -> None:
    rng = random.Random(7)
    for attempts in range(0, 20):
        raw = min(300.0, 2.0 * 2**attempts)
        for _ in range(20):
            d = backoff_seconds(attempts, rng=rng)
            assert raw * 0.5 <= d <= raw
    assert backoff_seconds(0, rng=rng) >= 1.0
    with pytest.raises(ValueError):
        backoff_seconds(-1)


@pytest.mark.unit
def test_stale_is_treated_as_delivered_rejected_is_dead_epoch_retries(tmp_path: Path, clock: Clock) -> None:
    box, _ = _outbox(tmp_path, clock)
    _enq(box, 1, run="a")
    _enq(box, 1, run="b")
    _enq(box, 1, run="c")

    def behaviour(_t: str, run: str, _v: int) -> DeliveryOutcome:
        if run == "a":
            return DeliveryOutcome.STALE
        if run == "b":
            raise AuthorityRejected("422")
        raise AuthorityEpochMismatch("re-resolve")

    stats = drain_once(box, FakeClient(behaviour), limit=10)
    assert (stats.delivered, stats.dead, stats.retried) == (1, 1, 1)
    assert box.stats().dead == 1
    # dead entries still count against the bound and are never silently dropped (I5)
    assert box.depth() == 2
    assert [e.run_id for e in box.dead()] == ["b"]


@pytest.mark.unit
def test_restart_recovers_entries_that_were_mid_delivery(tmp_path: Path, clock: Clock) -> None:
    box, cs = _outbox(tmp_path, clock)
    _enq(box, 1)
    claimed = box.claim_due(limit=5)
    assert len(claimed) == 1 and box.claim_due(limit=5) == []  # in flight: not handed out twice
    cs._conn.close()  # crash after send, before mark_delivered
    box2, _ = _outbox(tmp_path, clock)
    assert box2.recover_in_flight() == 1
    assert len(box2.claim_due(limit=5)) == 1


@pytest.mark.unit
def test_concurrent_producers_respect_the_bound(tmp_path: Path, clock: Clock) -> None:
    import threading

    box, _ = _outbox(tmp_path, clock, max_entries=10)
    accepted: list[int] = []
    lock = threading.Lock()

    def produce(n: int) -> None:
        try:
            _enq(box, 1, run=f"r{n}")
            with lock:
                accepted.append(n)
        except OutboxFullError:
            pass

    threads = [threading.Thread(target=produce, args=(i,)) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(accepted) == 10 and box.depth() == 10


@pytest.mark.unit
def test_delivered_rows_are_pruned_and_do_not_count(tmp_path: Path, clock: Clock) -> None:
    box, _ = _outbox(tmp_path, clock, max_entries=1)
    _enq(box, 1, "succeeded", run="a")
    drain_once(box, FakeClient(), limit=5)
    assert box.depth() == 0
    _enq(box, 1, "succeeded", run="b")  # capacity is free again after delivery
    assert box.depth() == 1
    assert box.prune_delivered(older_than=clock.now + 1) >= 1
