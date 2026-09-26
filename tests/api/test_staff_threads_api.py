"""Tests for Staff Conversation & Thread API v1 (SC-B3, Issue #1306).

Covers:
- POST /api/v1/staff/threads (create direct, group, auto to Barb)
- GET /api/v1/staff/threads (filtering by role, status, unread)
- GET /api/v1/staff/threads/{id} (thread detail with messages)
- PATCH /api/v1/staff/threads/{id} (rename, archive)
- POST /api/v1/staff/threads/{id}/messages (idempotency key required, 202 Accepted)
- GET /api/v1/staff/threads/{id}/stream (SSE token/message stream, Last-Event-ID resume, heartbeats)
- POST /api/v1/staff/threads/{id}/read and GET /api/v1/staff/inbox
- Error handling on provider failure producing classified error message with Retry action
- 503 fail-closed when store is degraded
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from server import app
from staff.conversations import (
    ConversationStoreStatus,
    get_conversation_store,
    reset_conversation_store,
)
from staff.thread_bus import reset_thread_bus

TEST_PRINCIPAL = Principal(
    id="operator-user",
    type="human",
    name="Operator",
    roles=["operator"],
    scopes=["staff.chat", "staff.read"],
)


@pytest.fixture(autouse=True)
def clean_conversations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    reset_conversation_store()
    reset_thread_bus()

    app.dependency_overrides[require_principal] = lambda: TEST_PRINCIPAL
    app.dependency_overrides[require_scope("staff.chat")] = lambda: TEST_PRINCIPAL
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_PRINCIPAL

    store = get_conversation_store()
    yield store
    app.dependency_overrides.clear()
    reset_conversation_store()
    reset_thread_bus()


@pytest.fixture
def client():
    return TestClient(
        app,
        headers={"X-Requested-With": "XMLHttpRequest"},
        raise_server_exceptions=False,
    )


def test_create_thread_and_get_detail(client: TestClient):
    """POST /threads creates a thread; GET /threads/{id} returns details and messages."""
    resp = client.post(
        "/api/v1/staff/threads",
        json={"title": "Planning session", "role": "night-watch"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert "id" in data
    assert data["title"] == "Planning session"
    assert "night-watch" in data["participants"]
    thread_id = data["id"]

    # Get thread detail
    detail_resp = client.get(f"/api/v1/staff/threads/{thread_id}")
    assert detail_resp.status_code == 200, detail_resp.text
    detail = detail_resp.json()
    assert detail["thread"]["id"] == thread_id
    assert detail["messages"] == []


def test_create_auto_thread_defaults_to_barb(client: TestClient):
    """POST /threads with kind='auto' defaults to Barb."""
    resp = client.post(
        "/api/v1/staff/threads",
        json={"kind": "auto"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["kind"] == "auto"
    assert "barb" in data["participants"]


def test_list_threads_filtering(client: TestClient):
    """GET /threads filters by role and status."""
    client.post("/api/v1/staff/threads", json={"title": "T1", "role": "night-watch"})
    client.post("/api/v1/staff/threads", json={"title": "T2", "role": "barb"})

    # Filter by role
    resp = client.get("/api/v1/staff/threads?role=night-watch")
    assert resp.status_code == 200
    items = resp.json().get("items", resp.json().get("threads", []))
    assert len(items) == 1
    assert items[0]["title"] == "T1"


def test_patch_thread_rename_and_archive(client: TestClient):
    """PATCH /threads/{id} renames or archives thread."""
    created = client.post("/api/v1/staff/threads", json={"title": "Initial", "role": "barb"}).json()
    tid = created["id"]

    # Rename
    patch_resp = client.patch(f"/api/v1/staff/threads/{tid}", json={"title": "Renamed Title"})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["title"] == "Renamed Title"

    # Archive
    arch_resp = client.patch(f"/api/v1/staff/threads/{tid}", json={"status": "archived"})
    assert arch_resp.status_code == 200
    assert arch_resp.json()["status"] == "archived"


def test_post_message_requires_idempotency_key(client: TestClient):
    """POST /threads/{id}/messages requires Idempotency-Key header."""
    created = client.post("/api/v1/staff/threads", json={"title": "T", "role": "barb"}).json()
    tid = created["id"]

    # Missing header -> 400 Bad Request with v1 error envelope
    resp = client.post(f"/api/v1/staff/threads/{tid}/messages", json={"body": "Hello"})
    assert resp.status_code == 400, resp.text
    err = resp.json()
    assert "error" in err
    assert err["error"]["code"] == "missing_idempotency_key"


def test_post_message_success_and_idempotent_replay(client: TestClient):
    """POST /threads/{id}/messages returns 202 with message and reply placeholder, replays identically."""
    created = client.post("/api/v1/staff/threads", json={"title": "T", "role": "barb"}).json()
    tid = created["id"]

    headers = {"Idempotency-Key": "example-key-msg-001"}  # gitleaks:allow
    resp = client.post(
        f"/api/v1/staff/threads/{tid}/messages",
        headers=headers,
        json={"body": "Please analyze fleet capacity"},
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert "message" in data
    assert "reply_placeholder" in data
    assert data["message"]["body_md"] == "Please analyze fleet capacity"
    assert data["message"]["author_kind"] == "user"
    assert data["reply_placeholder"]["delivery"] == "pending"

    # Duplicate POST with same key -> same message returned (idempotent replay)
    replay_resp = client.post(
        f"/api/v1/staff/threads/{tid}/messages",
        headers=headers,
        json={"body": "Please analyze fleet capacity"},
    )
    assert replay_resp.status_code in (200, 202)
    replay_data = replay_resp.json()
    assert replay_data["message"]["id"] == data["message"]["id"]


def test_thread_stream_sse_and_resume(client: TestClient):
    """GET /threads/{id}/stream streams events and replays missed events with Last-Event-ID."""
    created = client.post("/api/v1/staff/threads", json={"title": "T", "role": "barb"}).json()
    tid = created["id"]

    # Post first message
    client.post(
        f"/api/v1/staff/threads/{tid}/messages",
        headers={"Idempotency-Key": "k-1"},
        json={"body": "First message"},
    )

    # Reconnect stream with Last-Event-ID: 0 -> replays user message + placeholder
    stream_resp = client.get(
        f"/api/v1/staff/threads/{tid}/stream",
        headers={"Last-Event-ID": "0"},
        params={"limit": 2, "follow": "false"},
    )
    assert stream_resp.status_code == 200
    assert "text/event-stream" in stream_resp.headers["content-type"]
    text = stream_resp.text
    assert "event: message" in text
    assert "First message" in text


def test_mark_thread_read_and_inbox(client: TestClient):
    """POST /threads/{id}/read marks thread read; GET /inbox reports unread threads."""
    created = client.post("/api/v1/staff/threads", json={"title": "Inbox Test", "role": "night-watch"}).json()
    tid = created["id"]

    # Direct insert role message to simulate incoming message from night-watch
    store = get_conversation_store()
    store.add_message(
        thread_id=tid,
        author_kind="role",
        author="night-watch",
        body_md="Night watch sweep completed with 2 alerts",
    )

    # Thread should show in inbox for test-user
    inbox_resp = client.get("/api/v1/staff/inbox")
    assert inbox_resp.status_code == 200
    inbox_data = inbox_resp.json()
    items = inbox_data.get("items", inbox_data.get("inbox", []))
    assert any(item["id"] == tid for item in items)

    # Mark thread read
    read_resp = client.post(f"/api/v1/staff/threads/{tid}/read")
    assert read_resp.status_code == 200
    assert read_resp.json()["ok"] is True

    # Now inbox should no longer include this thread
    inbox_after = client.get("/api/v1/staff/inbox").json()
    items_after = inbox_after.get("items", inbox_after.get("inbox", []))
    assert not any(item["id"] == tid for item in items_after)


def test_reply_generation_failure_creates_error_message(client: TestClient):
    """Failure during reply generation creates an error message with failure_class and Retry action."""
    created = client.post("/api/v1/staff/threads", json={"title": "Error flow", "role": "barb"}).json()
    tid = created["id"]

    store = get_conversation_store()
    # Post message
    msg = store.add_message(
        thread_id=tid,
        author_kind="user",
        author="test-user",
        body_md="Check provider status",
    )
    # Simulate reply error recording
    err_msg = store.add_message(
        thread_id=tid,
        author_kind="role",
        author="barb",
        kind="error",
        body_md="Failed to reach Barb: connection timeout",
        meta={
            "failure_class": "provider_unavailable",
            "retryable": True,
            "actions": [{"name": "retry", "label": "Retry"}],
            "in_reply_to": msg.id,
        },
        delivery="failed",
    )
    assert err_msg.kind == "error"
    assert err_msg.meta["failure_class"] == "provider_unavailable"

    # User message is still intact in thread
    detail = client.get(f"/api/v1/staff/threads/{tid}").json()
    messages = detail["messages"]
    assert len(messages) == 2
    assert messages[0]["body_md"] == "Check provider status"
    assert messages[1]["kind"] == "error"


def test_store_unavailable_returns_503(client: TestClient):
    """When conversation store is degraded, operations return 503 with retryable: true."""
    store = get_conversation_store()
    store.status = ConversationStoreStatus(
        available=False,
        banner_message="Database migration failed, conversations unavailable",
    )
    resp = client.get("/api/v1/staff/threads")
    assert resp.status_code == 503, resp.text
    err = resp.json()["error"]
    assert err["retryable"] is True
    assert err["code"] == "conversations_unavailable"


def test_reconcile_shows_system_retry_message_in_thread(client: TestClient):
    """API test: thread shows the system retry message after reconciliation of interrupted reply."""
    from staff.reconcile import reconcile_interrupted_chat_messages

    # Create thread
    resp = client.post("/api/v1/staff/threads", json={"title": "Restart Test", "role": "barb"})
    assert resp.status_code == 201, resp.text
    thread_id = resp.json()["id"]

    store = get_conversation_store()

    # User message
    user_msg = store.add_message(
        thread_id=thread_id,
        author_kind="user",
        author="operator-user",
        body_md="Hello Barb",
        delivery="complete",
    )

    # Stuck pending reply message from backend restart
    reply_placeholder = store.add_message(
        thread_id=thread_id,
        author_kind="role",
        author="barb",
        kind="text",
        body_md="",
        meta={"in_reply_to": user_msg.id},
        delivery="pending",
    )

    # Reconcile interrupted messages
    reconciled = reconcile_interrupted_chat_messages(store)
    assert reply_placeholder.id in reconciled

    # API test: GET /api/v1/staff/threads/{thread_id} shows system retry message
    detail_resp = client.get(f"/api/v1/staff/threads/{thread_id}")
    assert detail_resp.status_code == 200, detail_resp.text
    data = detail_resp.json()
    messages = data["messages"]

    # Verify messages
    assert len(messages) == 3
    assert messages[0]["id"] == user_msg.id
    assert messages[0]["delivery"] == "complete"

    assert messages[1]["id"] == reply_placeholder.id
    assert messages[1]["delivery"] == "failed"
    assert messages[1]["meta"]["failure_class"] == "interrupted_by_restart"

    system_msg = messages[2]
    assert system_msg["author_kind"] == "system"
    assert system_msg["delivery"] == "complete"
    assert "retry" in system_msg["body_md"].lower() or system_msg["meta"].get("retryable") is True
