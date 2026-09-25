"""Tests for linking runs to conversation threads, remote run proxying, and needs_input (SC-B7, Issue #1314)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from routers.staff import router as staff_router
from routers.staff_threads import router as staff_threads_router
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.plan import RunRequest
from staff.run_link import (
    answer_needs_input,
    handle_run_status_change,
    post_run_card,
)
from staff.store import RunRecord, RunStore, get_store, reset_store
from staff.thread_bus import get_thread_bus, reset_thread_bus

TEST_PRINCIPAL = Principal(
    id="test-user",
    type="human",
    name="Test User",
    roles=["admin"],
    scopes=["staff.read", "staff.dispatch", "staff.cancel", "staff.write"],
)


@pytest.fixture
def run_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[RunStore]:
    db = tmp_path / "test_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db))
    reset_store()
    store = get_store()
    yield store
    reset_store()


@pytest.fixture
def conv_store(tmp_path: Path) -> Any:
    reset_conversation_store()
    reset_thread_bus()
    store = get_conversation_store(tmp_path / "test_conv.sqlite3")
    return store


@pytest.fixture
def client(run_store: RunStore, conv_store: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(staff_router)
    app.include_router(staff_threads_router, prefix="/api/v1/staff")

    app.dependency_overrides[require_principal] = lambda: TEST_PRINCIPAL
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_PRINCIPAL
    app.dependency_overrides[require_scope("staff.dispatch")] = lambda: TEST_PRINCIPAL
    app.dependency_overrides[require_scope("staff.cancel")] = lambda: TEST_PRINCIPAL
    app.dependency_overrides[require_scope("staff.write")] = lambda: TEST_PRINCIPAL

    with TestClient(app) as tc:
        yield tc

    app.dependency_overrides.clear()
    reset_store()
    reset_conversation_store()
    reset_thread_bus()


@pytest.mark.unit
def test_run_record_and_request_carry_thread_and_work_item(run_store: RunStore) -> None:
    req = RunRequest(
        role="librarian",
        repo="UpstreamDrift",
        prompt="Check consistency",
        thread_id="thread-abc-123",
        work_item_id="work-item-456",
    )
    assert req.thread_id == "thread-abc-123"
    assert req.work_item_id == "work-item-456"

    rec = RunRecord(
        id="run-test-001",
        role="librarian",
        provider="claude",
        model=None,
        machine="DeskComputer",
        repo="UpstreamDrift",
        target_kind="prompt",
        target_ref="",
        prompt="Check consistency",
        thread_id="thread-abc-123",
        work_item_id="work-item-456",
    )
    run_store.create_run(rec)

    fetched = run_store.get_run("run-test-001")
    assert fetched is not None
    assert fetched.thread_id == "thread-abc-123"
    assert fetched.work_item_id == "work-item-456"

    by_thread = run_store.list_runs_for_thread("thread-abc-123")
    assert len(by_thread) == 1
    assert by_thread[0].id == "run-test-001"


@pytest.mark.unit
def test_post_run_card_creates_message_and_broadcasts(conv_store: Any) -> None:
    thread = conv_store.create_thread(title="Test Thread", thread_id="thread-t1")
    rec = RunRecord(
        id="run-card-001",
        role="librarian",
        provider="claude",
        model="claude-3-5-sonnet",
        machine="DeskComputer",
        repo="UpstreamDrift",
        target_kind="prompt",
        target_ref="",
        prompt="Review documentation",
        thread_id=thread.id,
        status="running",
    )

    msg = post_run_card(
        rec,
        status="running",
        store=conv_store,
        bus=get_thread_bus(),
    )
    assert msg is not None
    assert msg.thread_id == "thread-t1"
    assert msg.kind == "run_card"
    assert msg.run_id == "run-card-001"
    assert msg.meta["status"] == "running"
    assert msg.meta["role"] == "librarian"
    assert msg.meta["machine"] == "DeskComputer"

    # Verify message persisted in conversation thread
    messages = conv_store.list_messages("thread-t1")
    assert len(messages) == 1
    assert messages[0].kind == "run_card"
    assert messages[0].run_id == "run-card-001"


@pytest.mark.unit
def test_handle_run_status_change_posts_terminal_summary(conv_store: Any) -> None:
    thread = conv_store.create_thread(title="Terminal Test", thread_id="thread-t2")
    rec = RunRecord(
        id="run-term-001",
        role="cartographer",
        provider="claude",
        model=None,
        machine="DeskComputer",
        repo="UpstreamDrift",
        target_kind="issue",
        target_ref="#123",
        prompt="Map architecture",
        thread_id=thread.id,
        status="succeeded",
        outcome="Mapped 14 modules into ARCHITECTURE.md (PR #456)",
    )

    handle_run_status_change(rec, status="succeeded", store=conv_store, bus=get_thread_bus())
    messages = conv_store.list_messages("thread-t2")
    assert len(messages) >= 1
    card = messages[-1]
    assert card.kind == "run_card"
    assert card.meta["status"] == "succeeded"
    assert "Mapped 14 modules" in card.body_md


@pytest.mark.unit
def test_needs_input_creates_question_card_and_continuation_run(conv_store: Any, run_store: RunStore) -> None:
    thread = conv_store.create_thread(title="Needs Input Thread", thread_id="thread-input-1")
    rec = RunRecord(
        id="run-input-001",
        role="ad-hoc",
        provider="claude",
        model=None,
        machine="DeskComputer",
        repo="UpstreamDrift",
        target_kind="prompt",
        target_ref="",
        prompt="Clean stale files",
        thread_id=thread.id,
        status="needs_input",
        failure_class="needs_input",
    )
    run_store.create_run(rec)

    handle_run_status_change(
        rec,
        status="needs_input",
        question="Should I delete unused test fixtures in tests/fixtures/?",
        store=conv_store,
        bus=get_thread_bus(),
    )

    messages = conv_store.list_messages("thread-input-1")
    card = messages[-1]
    assert card.kind == "run_card"
    assert card.meta["status"] == "needs_input"
    assert card.meta["question"] == "Should I delete unused test fixtures in tests/fixtures/?"

    # Answering the question via answer_needs_input
    continuation = answer_needs_input(
        thread_id="thread-input-1",
        run_id="run-input-001",
        answer="Yes, delete files older than 30 days.",
        caller_id="test-user",
        conv_store=conv_store,
        run_store=run_store,
    )
    assert continuation is not None
    assert continuation.thread_id == "thread-input-1"
    assert "Yes, delete files older than 30 days." in continuation.prompt


@pytest.mark.unit
def test_hub_proxies_remote_run_detail(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    remote_data = {
        "run": {
            "id": "run-remote-999",
            "role": "librarian",
            "provider": "claude",
            "machine": "OGLaptop",
            "status": "running",
            "repo": "UpstreamDrift",
            "thread_id": "thread-rem-1",
        },
        "events": [{"seq": 1, "kind": "start", "text": "Started on OGLaptop", "ts": "2026-09-24T00:00:00Z"}],
        "attempts": [],
    }

    async def fake_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
        if "og:8321/api/staff/runs/run-remote-999" in url:
            return remote_data
        raise ConnectionError("not found")

    monkeypatch.setattr("staff.fleet.get_json", fake_get_json)
    monkeypatch.setattr("staff.fleet.peer_nodes", lambda: {"OGLaptop": "http://og:8321"})

    resp = client.get("/api/staff/runs/run-remote-999")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run"]["id"] == "run-remote-999"
    assert data["run"]["machine"] == "OGLaptop"
    assert data["events"][0]["text"] == "Started on OGLaptop"


@pytest.mark.unit
def test_hub_proxies_remote_run_not_found(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
        raise ConnectionError("connection failed")

    monkeypatch.setattr("staff.fleet.get_json", fake_get_json)
    monkeypatch.setattr("staff.fleet.peer_nodes", lambda: {"OGLaptop": "http://og:8321"})

    resp = client.get("/api/staff/runs/run-nonexistent")
    assert resp.status_code == 404


@pytest.mark.unit
def test_hub_proxies_remote_cancel(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    remote_data = {
        "run": {"id": "run-rem-cancel", "machine": "OGLaptop", "status": "running"},
        "events": [],
    }

    async def fake_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
        if "og:8321/api/staff/runs/run-rem-cancel" in url:
            return remote_data
        raise ConnectionError("not found")

    async def fake_post_json(url: str, data: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        if "og:8321/api/staff/runs/run-rem-cancel/cancel" in url:
            return 200, {"ok": True, "run_id": "run-rem-cancel", "cancelled": True}
        raise ConnectionError("cancel failed")

    monkeypatch.setattr("staff.fleet.get_json", fake_get_json)
    monkeypatch.setattr("staff.fleet.post_json", fake_post_json)
    monkeypatch.setattr("staff.fleet.peer_nodes", lambda: {"OGLaptop": "http://og:8321"})

    resp = client.post("/api/staff/runs/run-rem-cancel/cancel")
    assert resp.status_code == 200
    assert resp.json()["cancelled"] is True


@pytest.mark.unit
def test_hub_proxies_remote_stream(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    remote_data = {
        "run": {"id": "run-rem-stream", "machine": "OGLaptop", "status": "running"},
        "events": [],
    }

    async def fake_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
        if "og:8321/api/staff/runs/run-rem-stream" in url:
            return remote_data
        raise ConnectionError("not found")

    class FakeStreamResponse:
        status_code = 200

        async def aiter_lines(self) -> AsyncIterator[str]:
            yield "event: progress"
            yield 'data: {"msg": "working"}'
            yield ""

        async def __aenter__(self) -> FakeStreamResponse:
            return self

        async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            pass

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def stream(self, method: str, url: str, **kwargs: Any) -> FakeStreamResponse:
            return FakeStreamResponse()

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            pass

    monkeypatch.setattr("staff.fleet.get_json", fake_get_json)
    monkeypatch.setattr("staff.fleet.peer_nodes", lambda: {"OGLaptop": "http://og:8321"})
    monkeypatch.setattr("httpx.AsyncClient", FakeClient)

    resp = client.get("/api/staff/runs/run-rem-stream/stream")
    assert resp.status_code == 200
    assert "event: progress" in resp.text


@pytest.mark.unit
def test_answer_thread_run_endpoint(client: TestClient, conv_store: Any, run_store: RunStore) -> None:
    thread = conv_store.create_thread(title="Answer Endpoint Thread", thread_id="thread-ans-1")
    rec = RunRecord(
        id="run-ans-001",
        role="ad-hoc",
        provider="claude",
        model=None,
        machine="DeskComputer",
        repo="UpstreamDrift",
        target_kind="prompt",
        target_ref="",
        prompt="Clean stale files",
        thread_id=thread.id,
        status="needs_input",
    )
    run_store.create_run(rec)

    resp = client.post(
        f"/api/v1/staff/threads/{thread.id}/runs/{rec.id}/answer",
        json={"answer": "Proceed with cleanup"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "continuation_run_id" in data
    assert data["thread_id"] == thread.id
