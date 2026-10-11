"""Simulated partition: no loss and no split write (#1800, BR-06 invariants I1-I5).

Two real SQLite stores stand in for the authority and a worker node. The worker's only path to
the authority is an injectable transport that can be partitioned, can drop responses after the
authority has applied (the nasty case), and can duplicate/reorder deliveries.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import pytest
from staff.barb_authority import AuthorityClient, AuthorityConfig
from staff.barb_outbox import OutboxStore
from staff.barb_outbox_drain import drain_once
from staff.barb_versions import ApplyOutcome, StaleEpochError
from staff.conversations import ConversationStore
from staff.run_link import run_card_id

EPOCH = 2


class Network:
    """Transport between worker and authority. ``partitioned`` drops requests; ``drop_response_once``
    applies the write at the authority and then fails, as when a response is lost."""

    def __init__(self, authority: ConversationStore) -> None:
        self.authority = authority
        self.partitioned = False
        self.drop_response_next = 0
        self.requests = 0

    def __call__(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        self.requests += 1
        if self.partitioned:
            raise ConnectionError("partition")
        thread_id = url.split("/threads/")[1].split("/")[0]
        try:
            _, res = self.authority.apply_run_card_update(
                thread_id=thread_id,
                run_id=body["run_id"],
                message_id=run_card_id(body["run_id"]),
                status=body["status"],
                version=body["version"],
                epoch=body["epoch"],
                authority_epoch=EPOCH,
                body_md=f"card {body['status']}",
                meta={"run_id": body["run_id"], "status": body["status"]},
                author=body.get("role", "librarian"),
            )
        except StaleEpochError:
            return 409, {"code": "stale_authority_epoch"}
        if self.drop_response_next:
            self.drop_response_next -= 1
            raise ConnectionError("response lost after apply")
        if res.outcome is ApplyOutcome.STALE:
            return 409, {"code": "stale_version"}
        return 200, {"outcome": res.outcome.value}


@pytest.fixture
def world(tmp_path: Path):  # type: ignore[no-untyped-def]
    authority = ConversationStore(tmp_path / "authority.sqlite3")
    worker = ConversationStore(tmp_path / "worker.sqlite3")
    thread = authority.create_thread(title="t", kind="direct", role="librarian", created_by="user:d")
    net = Network(authority)
    cfg = AuthorityConfig.from_env(
        {"BARB_AUTHORITY_NODE": "CT", "BARB_AUTHORITY_URL": "http://ct:8321", "BARB_AUTHORITY_EPOCH": str(EPOCH)},
        local_node="worker",
    )
    client = AuthorityClient(cfg, net)
    box: OutboxStore = worker.barb_outbox(max_entries=100, max_bytes=1_000_000, max_entry_bytes=10_000)
    return authority, worker, thread.id, net, client, box


def _emit(box: OutboxStore, thread_id: str, run: str, version: int, status: str) -> None:
    box.enqueue(
        "run_card",
        thread_id,
        run,
        version,
        {"status": status, "role": "librarian"},
        terminal=status in ("succeeded", "failed"),
    )


def _drain_all(box: OutboxStore, client: AuthorityClient, now_step: float = 1000.0) -> None:
    base = box.clock()
    for i in range(1, 51):
        if box.depth() == 0:
            return
        drain_once(box, client, limit=50, now=base + now_step * i)
    raise AssertionError("outbox did not drain")


@pytest.mark.unit
def test_worker_completes_during_partition_and_result_appears_after_heal(world) -> None:  # type: ignore[no-untyped-def]
    authority, worker, tid, net, client, box = world
    net.partitioned = True
    _emit(box, tid, "run-1", 1, "running")
    _emit(box, tid, "run-1", 2, "succeeded")  # terminal card produced while the origin is down
    drain_once(box, client, limit=10)
    assert authority.list_messages(tid) == []  # nothing visible yet, nothing lost
    assert box.depth() >= 1  # I5: still pending
    assert worker.list_messages(tid) == []  # no split write: the worker never committed a conversation row

    net.partitioned = False
    _drain_all(box, client)
    card = authority.get_message(run_card_id("run-1"))
    assert card is not None and card.meta["status"] == "succeeded"
    assert len(authority.list_messages(tid)) == 1
    assert worker.list_messages(tid) == []  # still no split write after recovery


@pytest.mark.unit
def test_lost_response_after_apply_is_deduped_not_applied_twice(world) -> None:  # type: ignore[no-untyped-def]
    authority, _worker, tid, net, client, box = world
    _emit(box, tid, "run-1", 1, "succeeded")
    net.drop_response_next = 1
    drain_once(box, client, limit=10)  # applied at the authority, ack lost, worker keeps the entry
    assert box.depth() == 1
    assert len(authority.list_messages(tid)) == 1
    _drain_all(box, client)  # redelivery => DUPLICATE (200)
    assert box.depth() == 0
    assert len(authority.list_messages(tid)) == 1  # I2: never accepted twice
    n = authority._conn.execute("SELECT COUNT(*) AS n FROM barb_receipts WHERE ref_id='run-1:1'").fetchone()["n"]
    assert n == 1


@pytest.mark.unit
@pytest.mark.parametrize("seed", range(10))
def test_reordered_duplicated_flaky_delivery_converges(world, seed: int) -> None:  # type: ignore[no-untyped-def]
    authority, _worker, tid, net, client, box = world
    rng = random.Random(seed)
    states = [(1, "queued"), (2, "running"), (3, "running"), (4, "succeeded")]
    rng.shuffle(states)
    for v, s in states:
        _emit(box, tid, "run-1", v, s)
    for _ in range(40):
        net.partitioned = rng.random() < 0.5
        net.drop_response_next = 1 if rng.random() < 0.3 else 0
        drain_once(box, client, limit=50, now=box.clock() + 1000.0 * (_ + 1))
    net.partitioned = False
    _drain_all(box, client)
    card = authority.get_message(run_card_id("run-1"))
    assert card is not None and card.meta["status"] == "succeeded"
    assert len(authority.list_messages(tid)) == 1
    assert box.depth() == 0


@pytest.mark.unit
def test_stale_epoch_sender_cannot_write(world, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    authority, _w, tid, net, _client, box = world
    old = AuthorityClient(
        AuthorityConfig.from_env(
            {"BARB_AUTHORITY_NODE": "CT", "BARB_AUTHORITY_URL": "http://ct:8321", "BARB_AUTHORITY_EPOCH": "1"},
            local_node="worker",
        ),
        net,
    )
    _emit(box, tid, "run-1", 1, "running")
    stats = drain_once(box, old, limit=10)
    assert stats.retried == 1 and authority.list_messages(tid) == []  # fenced, retried (config will be re-resolved)
    assert box.depth() == 1  # not lost
