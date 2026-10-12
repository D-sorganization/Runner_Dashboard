"""BR-06 PR 2: relay wiring - replica outbox, authority ingest, status route (#1800).

Invariants under test: a replica never commits a conversation write (no split write); a worker's
card update reaches the authority exactly once even across a partition and a lost response; a
stale-epoch or stale-version sender is refused; unconfigured nodes keep the legacy behaviour.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_fleet_peer, require_scope
from routers import staff_threads
from routers.staff_barb import router as barb_router
from routers.staff_threads import router as threads_router
from staff import barb_runtime
from staff.barb_authority import AuthorityClient, AuthorityConfig
from staff.barb_outbox_drain import drain_once
from staff.conversations import ConversationStore
from staff.run_link import handle_run_status_change, run_card_id
from staff.store import RunRecord, RunStore
from staff.thread_bus import ThreadEventBus

PRINCIPAL = Principal(id="u", type="human", name="U", roles=["admin"], scopes=["staff.read"])
EPOCH = 3


def _run(thread_id: str, run_id: str = "run-1", machine: str = "worker") -> RunRecord:
    return RunRecord(
        id=run_id,
        role="librarian",
        provider="claude",
        model=None,
        machine=machine,
        repo="Repo",
        target_kind="prompt",
        target_ref="",
        prompt="p",
        thread_id=thread_id,
    )


@pytest.fixture
def authority_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """An authority app (real store + routes) reachable through a switchable in-process network."""
    store = ConversationStore(tmp_path / "authority.sqlite3")
    thread = store.create_thread(title="t", kind="direct", role="librarian", created_by="user:d")
    monkeypatch.setattr(staff_threads, "_get_store_or_503", lambda: store)
    monkeypatch.setattr(staff_threads, "get_thread_bus", lambda: ThreadEventBus())
    app = FastAPI()
    app.include_router(threads_router, prefix="/api/v1/staff")
    app.dependency_overrides[require_fleet_peer] = lambda: "peer"
    client = TestClient(app)
    monkeypatch.setenv("BARB_AUTHORITY_NODE", "CT")
    monkeypatch.setenv("BARB_AUTHORITY_URL", "http://ct:8321")
    monkeypatch.setenv("BARB_AUTHORITY_EPOCH", str(EPOCH))
    monkeypatch.setattr(barb_runtime, "local_node_name", lambda: "CT")
    barb_runtime.reset_authority_config()
    yield {"store": store, "thread": thread, "client": client, "monkeypatch": monkeypatch}
    barb_runtime.reset_authority_config()


class Net:
    def __init__(self, client: TestClient) -> None:
        self.client, self.up, self.lose_response = client, True, 0

    def __call__(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        if not self.up:
            raise ConnectionError("partition")
        replica_cfg = barb_runtime._config  # the in-process "authority" must see its own role, not the replica's
        barb_runtime._config = AuthorityConfig.from_env(
            {"BARB_AUTHORITY_NODE": "CT", "BARB_AUTHORITY_URL": "http://ct:8321", "BARB_AUTHORITY_EPOCH": str(EPOCH)},
            local_node="CT",
        )
        try:
            resp = self.client.post(url.split("ct:8321")[1], json=body, headers=headers)
        finally:
            barb_runtime._config = replica_cfg
        if self.lose_response:
            self.lose_response -= 1
            raise ConnectionError("response lost")
        return resp.status_code, resp.json()


def _become_replica(env: dict[str, Any]) -> None:
    env["monkeypatch"].setattr(barb_runtime, "local_node_name", lambda: "worker")
    barb_runtime.reset_authority_config()


# ── ingest on the authority ──────────────────────────────────────────────────
@pytest.mark.unit
def test_ingest_applies_once_and_dedupes_a_replay(authority_env: dict[str, Any]) -> None:
    c, tid, store = authority_env["client"], authority_env["thread"].id, authority_env["store"]
    body = {"run_id": "r1", "role": "librarian", "status": "succeeded", "version": 2, "epoch": EPOCH}
    first = c.post(f"/api/v1/staff/threads/{tid}/relay-card", json=body)
    again = c.post(f"/api/v1/staff/threads/{tid}/relay-card", json=body)
    assert first.status_code == again.status_code == 200
    assert len(store.list_messages(tid)) == 1


@pytest.mark.unit
def test_ingest_refuses_a_stale_epoch_and_a_stale_version(authority_env: dict[str, Any]) -> None:
    c, tid, store = authority_env["client"], authority_env["thread"].id, authority_env["store"]
    old = c.post(
        f"/api/v1/staff/threads/{tid}/relay-card",
        json={"run_id": "r1", "role": "x", "status": "running", "version": 1, "epoch": EPOCH - 1},
    )
    assert old.status_code == 409 and old.json()["code"] == "stale_authority_epoch"
    assert store.list_messages(tid) == []
    c.post(
        f"/api/v1/staff/threads/{tid}/relay-card",
        json={"run_id": "r1", "role": "x", "status": "succeeded", "version": 5, "epoch": EPOCH},
    )
    late = c.post(
        f"/api/v1/staff/threads/{tid}/relay-card",
        json={"run_id": "r1", "role": "x", "status": "running", "version": 3, "epoch": EPOCH},
    )
    assert late.status_code == 409 and late.json()["code"] == "stale_version"
    assert store.get_message(run_card_id("r1")).meta["status"] == "succeeded"  # type: ignore[union-attr]


@pytest.mark.unit
def test_a_replica_refuses_to_ingest(authority_env: dict[str, Any]) -> None:
    _become_replica(authority_env)
    tid = authority_env["thread"].id
    resp = authority_env["client"].post(
        f"/api/v1/staff/threads/{tid}/relay-card",
        json={"run_id": "r1", "role": "x", "status": "running", "version": 1, "epoch": EPOCH},
    )
    assert resp.status_code == 503 and resp.json()["code"] == "not_authority"
    assert authority_env["store"].list_messages(tid) == []


@pytest.mark.unit
def test_legacy_unversioned_relay_still_works_without_authority(
    authority_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("BARB_AUTHORITY_NODE", "BARB_AUTHORITY_URL", "BARB_AUTHORITY_EPOCH"):
        monkeypatch.delenv(name)
    barb_runtime.reset_authority_config()
    tid = authority_env["thread"].id
    resp = authority_env["client"].post(
        f"/api/v1/staff/threads/{tid}/relay-card", json={"run_id": "r1", "role": "x", "status": "running"}
    )
    assert resp.status_code == 200 and len(authority_env["store"].list_messages(tid)) == 1


# ── replica producer ─────────────────────────────────────────────────────────
@pytest.fixture
def replica(authority_env: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    worker = ConversationStore(tmp_path / "worker.sqlite3")
    runs = RunStore(tmp_path / "runs.sqlite3")
    tid = authority_env["thread"].id
    runs.create_run(_run(tid))
    _become_replica(authority_env)
    monkeypatch.setattr(barb_runtime, "get_conversation_store", lambda: worker)
    monkeypatch.setattr(barb_runtime, "get_store", lambda: runs)
    monkeypatch.setattr("staff.run_link.get_conversation_store", lambda: worker)
    return {**authority_env, "worker": worker, "runs": runs}


def _client(net: Net) -> AuthorityClient:
    cfg = barb_runtime.get_authority_config()
    return AuthorityClient(cfg, net)


@pytest.mark.unit
def test_replica_enqueues_instead_of_committing_locally(replica: dict[str, Any]) -> None:
    tid = replica["thread"].id
    handle_run_status_change(_run(tid), "running")
    handle_run_status_change(_run(tid), "succeeded", summary="done")
    assert replica["worker"].list_messages(tid) == []  # no split write
    box = replica["worker"].barb_outbox()
    # version 1 (non-terminal) is coalesced into the terminal version 2, which is never dropped
    assert [e.version for e in box.pending_for(tid, "run-1")] == [2]
    assert box.pending_for(tid, "run-1")[-1].terminal is True
    assert replica["runs"].next_card_version("run-1") == 3  # the producer advanced the run's counter


@pytest.mark.unit
def test_partition_then_heal_delivers_exactly_once(replica: dict[str, Any]) -> None:
    tid, store = replica["thread"].id, replica["store"]
    net = Net(replica["client"])
    net.up = False
    handle_run_status_change(_run(tid), "running")
    handle_run_status_change(_run(tid), "succeeded", summary="done")
    box = replica["worker"].barb_outbox()
    drain_once(box, _client(net), limit=10)
    assert store.list_messages(tid) == [] and box.depth() >= 1  # nothing lost, nothing visible
    net.up, net.lose_response = True, 1
    base = box.clock()
    for i in range(1, 30):
        if box.depth() == 0:
            break
        drain_once(box, _client(net), limit=10, now=base + 1000.0 * i)
    assert box.depth() == 0
    card = store.get_message(run_card_id("run-1"))
    assert card is not None and card.meta["status"] == "succeeded"
    assert len(store.list_messages(tid)) == 1


@pytest.mark.unit
def test_full_outbox_is_counted_and_never_raises_into_the_run(replica: dict[str, Any]) -> None:
    tid = replica["thread"].id
    replica["monkeypatch"].setenv("BARB_OUTBOX_MAX_ENTRIES", "1")
    runs: RunStore = replica["runs"]
    runs.create_run(_run(tid, "run-2"))
    handle_run_status_change(_run(tid, "run-1"), "running")
    handle_run_status_change(_run(tid, "run-2"), "running")  # outbox full: counted, run unaffected
    assert barb_runtime.barb_status()["enqueue_failures"] == 1
    assert replica["worker"].list_messages(tid) == []


# ── status route ─────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_status_route_reports_role_epoch_and_degradation(replica: dict[str, Any]) -> None:
    tid = replica["thread"].id
    app = FastAPI()
    app.include_router(barb_router, prefix="/api/v1/staff")
    app.dependency_overrides[require_scope("staff.read")] = lambda: PRINCIPAL
    c = TestClient(app)
    clear = c.get("/api/v1/staff/barb/status").json()
    assert clear["role"] == "replica" and clear["epoch"] == EPOCH and clear["degraded"] is False
    handle_run_status_change(_run(tid), "running")
    pending = c.get("/api/v1/staff/barb/status").json()
    assert pending["degraded"] is True and pending["outbox"]["entries"] == 1


@pytest.mark.unit
def test_config_for_unconfigured_node_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("BARB_AUTHORITY_NODE", "BARB_AUTHORITY_URL", "BARB_AUTHORITY_EPOCH"):
        monkeypatch.delenv(name, raising=False)
    barb_runtime.reset_authority_config()
    assert barb_runtime.get_authority_config() == AuthorityConfig.from_env(
        {}, local_node=barb_runtime.local_node_name()
    )
