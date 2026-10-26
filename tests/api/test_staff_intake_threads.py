"""Intake thread flags: no_auto_reply and participants_only (#3017)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from server import app
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.thread_bus import reset_thread_bus

SMITHERS = Principal(
    id="agent-smithers", type="bot", name="Smithers", roles=["bot"], scopes=["staff.chat", "staff.read"]
)
OUTSIDER = Principal(id="agent-other", type="bot", name="Other", roles=["bot"], scopes=["staff.chat", "staff.read"])


def _act_as(prin: Principal) -> None:
    app.dependency_overrides[require_principal] = lambda: prin
    app.dependency_overrides[require_scope("staff.chat")] = lambda: prin
    app.dependency_overrides[require_scope("staff.read")] = lambda: prin


@pytest.fixture(autouse=True)
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_runs.sqlite3"))
    reset_conversation_store()
    reset_thread_bus()
    turns: list[tuple] = []

    async def _record_turn(*args, **_kwargs):
        turns.append(args)

    monkeypatch.setattr("routers.staff_threads.run_chat_turn_in_background", _record_turn)
    _act_as(SMITHERS)
    store = get_conversation_store()
    store.turns = turns
    yield store
    app.dependency_overrides.clear()
    reset_conversation_store()
    reset_thread_bus()


def _create(client: TestClient, **flags: bool) -> dict:
    resp = client.post("/api/v1/staff/threads", json={"title": "Intake", "role": "barb", **flags})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _post(client: TestClient, thread_id: str, key: str) -> dict:
    resp = client.post(
        f"/api/v1/staff/threads/{thread_id}/messages",
        json={"body": "intake item"},
        headers={"Idempotency-Key": key},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


def test_no_auto_reply_thread_stores_message_without_a_turn(store) -> None:
    client = TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})
    thread = _create(client, no_auto_reply=True)
    assert thread["meta"]["no_auto_reply"] is True

    data = _post(client, thread["id"], "k1")

    assert data["message"]["body_md"] == "intake item"
    assert "reply_placeholder" not in data
    assert store.turns == []
    authors = [m.author_kind for m in store.list_messages(thread["id"], limit=10)]
    assert authors == ["user"]


def test_default_thread_still_starts_a_turn(store) -> None:
    client = TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})
    thread = _create(client)
    assert "no_auto_reply" not in thread["meta"]

    data = _post(client, thread["id"], "k1")

    assert data["reply_placeholder"]["delivery"] == "pending"
    assert len(store.turns) == 1


def test_participants_only_thread_hidden_from_outsiders() -> None:
    client = TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})
    thread = _create(client, participants_only=True)
    tid = thread["id"]

    assert client.get(f"/api/v1/staff/threads/{tid}").status_code == 200

    _act_as(OUTSIDER)
    assert client.get(f"/api/v1/staff/threads/{tid}").status_code == 404
    assert client.get(f"/api/v1/staff/threads/{tid}/stream?follow=false").status_code == 404
    listed = [t["id"] for t in client.get("/api/v1/staff/threads").json()["items"]]
    assert tid not in listed


def test_open_thread_stays_readable_by_non_participants() -> None:
    client = TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})
    tid = _create(client)["id"]

    _act_as(OUTSIDER)
    assert client.get(f"/api/v1/staff/threads/{tid}").status_code == 200
    listed = [t["id"] for t in client.get("/api/v1/staff/threads").json()["items"]]
    assert tid in listed
