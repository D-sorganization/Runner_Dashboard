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
    result_summary,
)
from staff.store import RunRecord, RunStore, get_store, reset_store
from staff.thread_bus import ThreadEventBus, get_thread_bus, reset_thread_bus

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


# ─── One run card per run, published as a message (#1547) ──────────────────────


def _thread_run(conv_store: Any, run_id: str = "run-live-001", status: str = "queued") -> RunRecord:
    thread = conv_store.create_thread(title="Live card", thread_id=f"thread-{run_id}")
    return RunRecord(
        id=run_id,
        role="e2e-analyst",
        provider="claude",
        model=None,
        machine="DeskComputer",
        repo="",
        target_kind="prompt",
        target_ref="",
        prompt="Summarise the queue",
        thread_id=thread.id,
        status=status,
    )


class _RecordingBus(ThreadEventBus):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, str, dict[str, Any], Any]] = []

    def publish_sync(self, thread_id: str, event_type: str, data: dict[str, Any], event_id: Any = None) -> int:
        self.events.append((thread_id, event_type, data, event_id))
        return 1


@pytest.mark.unit
def test_status_changes_update_one_run_card_in_place(conv_store: Any) -> None:
    rec = _thread_run(conv_store)

    first = post_run_card(rec, status="queued", store=conv_store, bus=_RecordingBus())
    last = post_run_card(rec, status="succeeded", summary="fake run finished", store=conv_store, bus=_RecordingBus())

    assert first is not None and last is not None
    assert last.id == first.id
    cards = [m for m in conv_store.list_messages(rec.thread_id) if m.kind == "run_card"]
    assert [c.id for c in cards] == [first.id]
    assert cards[0].meta["status"] == "succeeded"
    assert "fake run finished" in cards[0].body_md


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "card_status"),
    [
        ("queued", "queued"),
        ("preparing", "running"),
        ("running", "running"),
        ("needs_input", "needs_input"),
        ("succeeded", "completed"),
        ("failed", "failed"),
        ("cancelled", "cancelled"),
    ],
)
def test_run_card_meta_carries_the_console_run_shape(conv_store: Any, status: str, card_status: str) -> None:
    rec = _thread_run(conv_store)

    msg = post_run_card(rec, status=status, question="Which repo?", store=conv_store, bus=_RecordingBus())

    assert msg is not None
    run = msg.meta["run"]
    assert run["id"] == rec.id
    assert run["status"] == card_status
    assert run["node"] == "DeskComputer"
    assert run["provider"] == "claude"
    assert run["role"] == "e2e-analyst"
    assert run["thread_id"] == rec.thread_id
    assert run["question"] == ("Which repo?" if status == "needs_input" else None)


@pytest.mark.unit
def test_run_card_is_published_as_a_message_event(conv_store: Any) -> None:
    rec = _thread_run(conv_store)
    bus = _RecordingBus()

    msg = post_run_card(rec, status="running", store=conv_store, bus=bus)

    assert msg is not None
    assert len(bus.events) == 1
    thread_id, event_type, data, event_id = bus.events[0]
    assert (thread_id, event_type) == (rec.thread_id, "message")
    assert data["message"]["id"] == msg.id
    assert data["message"]["meta"]["run"]["status"] == "running"
    assert event_id == msg.seq


@pytest.mark.unit
def test_answering_marks_the_question_card_answered(conv_store: Any, run_store: RunStore) -> None:
    rec = _thread_run(conv_store, run_id="run-ask-001", status="needs_input")
    run_store.create_run(rec)
    card = post_run_card(rec, status="needs_input", question="Which repo?", store=conv_store, bus=_RecordingBus())
    assert card is not None

    class _Runner:
        def submit(self, req: RunRequest) -> RunRecord:
            return RunRecord(
                id="run-ask-002",
                role=req.role,
                provider=req.provider or "claude",
                model=None,
                machine="DeskComputer",
                repo="",
                target_kind="prompt",
                target_ref="",
                prompt=req.prompt,
                thread_id=req.thread_id,
                status="queued",
            )

    continuation = answer_needs_input(
        thread_id=rec.thread_id,
        run_id=rec.id,
        answer="Runner_Dashboard",
        caller_id="human:alice",
        conv_store=conv_store,
        run_store=run_store,
        runner=_Runner(),
    )

    assert continuation is not None
    answered = conv_store.get_message(card.id).meta["run"]
    assert answered["status"] == "needs_input"
    assert answered["answered_by"] == "human:alice"
    assert answered["continued_by"] == "run-ask-002"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publish_sync_from_a_worker_thread_reaches_an_async_subscriber() -> None:
    import asyncio
    import threading

    reset_thread_bus()
    bus = get_thread_bus()
    q = await bus.subscribe("thread-x")
    # Debug mode makes a cross-thread call_soon raise instead of silently not waking the loop.
    asyncio.get_running_loop().set_debug(True)
    getter = asyncio.ensure_future(q.get())
    await asyncio.sleep(0)  # the subscriber is now waiting, as the SSE generator is

    worker = threading.Thread(target=bus.publish_sync, args=("thread-x", "message", {"message": {"id": "m1"}}, 7))
    worker.start()
    worker.join()

    ev = await asyncio.wait_for(getter, timeout=2)
    assert ev == {"id": 7, "event": "message", "data": {"message": {"id": "m1"}}}
    reset_thread_bus()


@pytest.mark.parametrize(
    ("result_line", "expected"),
    [
        ("STAFF_RESULT: fake run finished\n", "fake run finished"),
        ("STAFF_RESULT:   opened PR #12  \nmore output", "opened PR #12"),
        ("", None),
        ("STAFF_RESULT:", None),
    ],
)
def test_result_summary_is_the_staff_result_text(result_line: str, expected: str | None) -> None:
    """A completed run's card shows what its STAFF_RESULT line said (#1547)."""
    assert result_summary(result_line) == expected
