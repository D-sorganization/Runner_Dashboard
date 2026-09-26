"""Tests for cross-node run card relay (SC-B1-G5, Issue #1488).

Tests that when a staff run executes on a remote node, its run-card status transitions
are relayed back to the thread's originating node via the peer API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_fleet_peer, require_scope
from routers.staff_threads import router as staff_threads_router
from staff.conversations import ConversationStore
from staff.run_link import (
    handle_run_status_change,
    relay_run_card_to_origin,
    run_card_id,
)
from staff.store import RunRecord
from staff.thread_bus import ThreadEventBus

PEER_PRINCIPAL = Principal(
    id="fleet-peer",
    type="peer",
    name="Node B",
    roles=["fleet-peer"],
    scopes=["staff.read", "staff.chat", "staff.write"],
)


@pytest.fixture
def node_a_env(tmp_path: Path) -> dict[str, Any]:
    """Simulates Node A (the originating / home node)."""
    db_path = tmp_path / "node_a_conv.sqlite3"
    conv_store = ConversationStore(db_path)
    bus = ThreadEventBus()
    thread = conv_store.create_thread(
        title="Investigation",
        kind="direct",
        role="librarian",
        created_by="user:dieter",
    )

    app = FastAPI()
    app.include_router(staff_threads_router, prefix="/api/v1/staff")
    app.dependency_overrides[require_fleet_peer] = lambda: "fleet-peer"
    app.dependency_overrides[require_scope("staff.chat")] = lambda: PEER_PRINCIPAL
    app.dependency_overrides[require_scope("staff.read")] = lambda: PEER_PRINCIPAL

    # Override get_conversation_store and get_thread_bus for the test app
    from routers import staff_threads

    staff_threads._get_store_or_503 = lambda: conv_store

    client = TestClient(app)
    return {
        "store": conv_store,
        "bus": bus,
        "thread": thread,
        "client": client,
        "app": app,
    }


@pytest.mark.unit
def test_two_node_forwarded_run_relays_cards_to_origin(
    node_a_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A forwarded run executing on Node B relays its status cards to Node A's thread."""
    node_a_store: ConversationStore = node_a_env["store"]
    node_a_thread = node_a_env["thread"]
    client: TestClient = node_a_env["client"]

    # Mock peer_nodes so Node B knows where Node A is
    monkeypatch.setattr(
        "staff.fleet.peer_nodes",
        lambda: {"nodeA": "http://node-a.local:5001"},
    )

    # Intercept relay HTTP call and route it to Node A's test client
    def fake_relay_post(url: str, json: dict[str, Any], headers: dict[str, str]) -> Any:
        assert "node-a.local:5001" in url
        path = url.split("node-a.local:5001")[-1]
        return client.post(path, json=json, headers=headers)

    run = RunRecord(
        id="run-relay-001",
        role="librarian",
        provider="claude",
        model="claude-3-5-sonnet",
        machine="nodeB",
        repo="UpstreamDrift",
        target_kind="prompt",
        target_ref="",
        prompt="Analyze data",
        origin_node="nodeA",
        thread_id=node_a_thread.id,
    )

    # Execute run lifecycle on Node B
    handle_run_status_change(run, "queued", relay_fn=fake_relay_post)
    handle_run_status_change(run, "running", relay_fn=fake_relay_post)
    handle_run_status_change(run, "succeeded", summary="Analysis done", relay_fn=fake_relay_post)

    # Verify Node A's thread now contains the completed run card
    card_id = run_card_id(run.id)
    msg = node_a_store.get_message(card_id)
    assert msg is not None
    assert msg.thread_id == node_a_thread.id
    assert msg.kind == "run_card"
    assert msg.meta["status"] == "succeeded"
    assert msg.meta["machine"] == "nodeB"
    assert msg.meta["run"]["node"] == "nodeB"
    assert msg.meta["run"]["status"] == "completed"
    assert "Analysis done" in msg.body_md


@pytest.mark.unit
def test_relay_card_idempotency_duplicate_delivery(node_a_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Duplicate relays update the existing run card in place without creating new messages."""
    node_a_store: ConversationStore = node_a_env["store"]
    node_a_thread = node_a_env["thread"]
    client: TestClient = node_a_env["client"]

    def fake_relay_post(url: str, json: dict[str, Any], headers: dict[str, str]) -> Any:
        path = url.split("node-a.local:5001")[-1]
        return client.post(path, json=json, headers=headers)

    monkeypatch.setattr("staff.fleet.peer_nodes", lambda: {"nodeA": "http://node-a.local:5001"})

    run = RunRecord(
        id="run-relay-dup",
        role="researcher",
        provider="codex",
        model=None,
        machine="nodeB",
        repo="Tools",
        target_kind="prompt",
        target_ref="",
        prompt="Audit",
        origin_node="nodeA",
        thread_id=node_a_thread.id,
    )

    # Send 3 identical 'running' events
    handle_run_status_change(run, "running", relay_fn=fake_relay_post)
    handle_run_status_change(run, "running", relay_fn=fake_relay_post)
    handle_run_status_change(run, "running", relay_fn=fake_relay_post)

    messages = node_a_store.list_messages(node_a_thread.id)
    cards = [m for m in messages if m.kind == "run_card"]
    assert len(cards) == 1


@pytest.mark.unit
def test_relay_card_out_of_order_protection(node_a_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-terminal update arriving after a terminal update does not overwrite terminal status."""
    node_a_store: ConversationStore = node_a_env["store"]
    node_a_thread = node_a_env["thread"]
    client: TestClient = node_a_env["client"]

    def fake_relay_post(url: str, json: dict[str, Any], headers: dict[str, str]) -> Any:
        path = url.split("node-a.local:5001")[-1]
        return client.post(path, json=json, headers=headers)

    monkeypatch.setattr("staff.fleet.peer_nodes", lambda: {"nodeA": "http://node-a.local:5001"})

    run = RunRecord(
        id="run-relay-ooo",
        role="researcher",
        provider="codex",
        model=None,
        machine="nodeB",
        repo="Tools",
        target_kind="prompt",
        target_ref="",
        prompt="Audit",
        origin_node="nodeA",
        thread_id=node_a_thread.id,
    )

    # First succeeds
    handle_run_status_change(run, "succeeded", summary="Done first", relay_fn=fake_relay_post)
    card_id = run_card_id(run.id)
    assert node_a_store.get_message(card_id).meta["status"] == "succeeded"

    # Late arriving 'running' update should NOT overwrite 'succeeded'
    handle_run_status_change(run, "running", relay_fn=fake_relay_post)
    assert node_a_store.get_message(card_id).meta["status"] == "succeeded"


@pytest.mark.unit
def test_relay_card_home_node_offline_fails_visibly(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the home node is unreachable, the relay logs classified error without crashing."""
    monkeypatch.setattr("staff.fleet.peer_nodes", lambda: {"nodeA": "http://offline-node.invalid:5001"})

    def offline_relay_post(url: str, json: dict[str, Any], headers: dict[str, str]) -> Any:
        raise ConnectionRefusedError("Connection refused by peer")

    run = RunRecord(
        id="run-relay-offline",
        role="researcher",
        provider="codex",
        model=None,
        machine="nodeB",
        repo="Tools",
        target_kind="prompt",
        target_ref="",
        prompt="Audit",
        origin_node="nodeA",
        thread_id="thread-offline",
    )

    # Should not raise exception
    ok = relay_run_card_to_origin(run, "running", relay_fn=offline_relay_post)
    assert ok is False
    assert "failed to relay run card" in caplog.text
    assert "nodeA" in caplog.text


@pytest.mark.unit
def test_relay_card_missing_thread_returns_404(node_a_env: dict[str, Any]) -> None:
    """Relaying a card for a non-existent thread on the home node returns HTTP 404."""
    client: TestClient = node_a_env["client"]
    resp = client.post(
        "/api/v1/staff/threads/thread-nonexistent/relay-card",
        json={
            "run_id": "run-xyz",
            "role": "librarian",
            "status": "running",
            "node": "nodeB",
            "provider": "claude",
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "thread_not_found"
