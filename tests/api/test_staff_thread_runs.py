"""Tests for linking runs to conversation threads, remote run proxying, and needs_input (SC-B7, Issue #1314)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
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
def test_needs_input_creates_question_card_and_continuation_run(
    conv_store: Any, run_store: RunStore, staff_launches: list[str], staff_cli_installed
) -> None:
    staff_cli_installed("claude")
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
    assert staff_launches == [continuation.id]


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

    requests: list[str] = []

    async def fake_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
        requests.append(url)
        if "og:8321/api/staff/local-runs/run-remote-999" in url:
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
    assert len(requests) == 2
    assert "/api/staff/local-runs/run-remote-999?events=0" in requests[0]
    assert "/api/staff/local-runs/run-remote-999?events=200" in requests[1]


@pytest.mark.unit
def test_hub_proxies_remote_run_not_found(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
        raise ConnectionError("connection failed")

    monkeypatch.setattr("staff.fleet.get_json", fake_get_json)
    monkeypatch.setattr("staff.fleet.peer_nodes", lambda: {"OGLaptop": "http://og:8321"})

    resp = client.get("/api/staff/runs/run-nonexistent")
    assert resp.status_code == 503


@pytest.mark.unit
def test_missing_run_lookup_uses_local_only_peer_path(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[str] = []

    async def fake_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
        requests.append(url)
        request = httpx.Request("GET", url)
        response = httpx.Response(
            404,
            request=request,
            json={"detail": {"code": "local_run_not_found", "run_id": "run-missing-cycle"}},
        )
        raise httpx.HTTPStatusError("peer response", request=request, response=response)

    monkeypatch.setattr("staff.fleet.get_json", fake_get_json)
    monkeypatch.setattr("staff.fleet.peer_nodes", lambda: {"Peer": "http://peer:8321"})

    resp = client.get("/api/staff/runs/run-missing-cycle")

    assert resp.status_code == 404
    assert len(requests) == 1
    assert "/api/staff/local-runs/run-missing-cycle?events=0" in requests[0]
    assert "/api/staff/runs/" not in requests[0]

    local_missing = client.get("/api/staff/local-runs/run-confirmed-local-miss")
    assert local_missing.status_code == 404
    assert local_missing.json()["detail"] == {
        "code": "local_run_not_found",
        "run_id": "run-confirmed-local-miss",
    }

    # Old peers ignore local=1. An authenticated fleet request must therefore
    # remain local-only even when the query parameter is absent.
    fleet_peer = Principal(id="fleet-peer", type="bot", name="Fleet Peer", roles=["fleet-peer"])
    client.app.dependency_overrides.update(
        {dep: (lambda: fleet_peer) for dep in client.app.dependency_overrides if dep is not require_principal}
    )
    monkeypatch.setattr("staff.fleet.peer_nodes", lambda: pytest.fail("fleet peer must not fan out"))
    legacy_peer_resp = client.get("/api/staff/runs/run-legacy-peer-miss")
    assert legacy_peer_resp.status_code == 404
    assert client.get("/api/staff/runs/run-legacy-peer-stream/stream").status_code == 404
    assert client.post("/api/staff/runs/run-legacy-peer-cancel/cancel").status_code == 404


@pytest.mark.unit
def test_old_peer_without_safe_local_route_is_unavailable_without_recursive_lookup(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[str] = []

    async def fake_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
        requests.append(url)
        request = httpx.Request("GET", url)
        # An old router returns its generic 404 for the unknown local-runs path.
        # It must never be queried at /runs/{id}?local=1, which it may recurse on.
        response = httpx.Response(404, request=request, json={"detail": "Not Found"})
        raise httpx.HTTPStatusError("peer response", request=request, response=response)

    monkeypatch.setattr("staff.fleet.get_json", fake_get_json)
    monkeypatch.setattr("staff.fleet.peer_nodes", lambda: {"OldPeer": "http://old-peer:8321"})

    response = client.get("/api/staff/runs/run-old-peer-miss")

    assert response.status_code == 503
    assert requests == ["http://old-peer:8321/api/staff/local-runs/run-old-peer-miss?events=0"]
    assert all("/api/staff/runs/" not in url for url in requests)


@pytest.mark.unit
@pytest.mark.parametrize(
    "body",
    [
        {"run": {"id": "some-other-run"}, "events": [], "attempts": []},
        {"run": {"id": "run-malformed-peer-response"}, "events": []},
        {"run": None, "events": [], "attempts": []},
    ],
)
def test_malformed_local_peer_response_is_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    async def fake_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
        return body

    monkeypatch.setattr("staff.fleet.get_json", fake_get_json)
    monkeypatch.setattr("staff.fleet.peer_nodes", lambda: {"Peer": "http://peer:8321"})

    response = client.get("/api/staff/runs/run-malformed-peer-response")

    assert response.status_code == 503


@pytest.mark.unit
def test_authenticated_fleet_peer_can_read_local_run(
    client: TestClient,
    run_store: RunStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_store.create_run(
        RunRecord(
            id="run-peer-local-200",
            role="librarian",
            provider="claude",
            model="haiku",
            machine="Peer",
            repo="Runner_Dashboard",
            target_kind="prompt",
            target_ref="",
            prompt="safe local test run",
        )
    )
    fleet_peer = Principal(id="fleet-peer", type="bot", name="Fleet Peer", roles=["fleet-peer"])
    client.app.dependency_overrides.update(
        {dep: (lambda: fleet_peer) for dep in client.app.dependency_overrides if dep is not require_principal}
    )
    monkeypatch.setattr("staff.fleet.peer_nodes", lambda: pytest.fail("fleet peer must not fan out"))

    response = client.get("/api/staff/runs/run-peer-local-200")

    assert response.status_code == 200
    assert response.json()["run"]["id"] == "run-peer-local-200"


@pytest.mark.unit
def test_stale_remote_run_owner_is_evicted_after_remote_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import staff.remote_runs as remote_runs  # noqa: PLC0415

    run_id = "run-stale-owner-404"
    remote_runs._remember_peer(run_id, "OldPeer", "http://old-peer:8321")

    async def fake_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
        request = httpx.Request("GET", url)
        response = httpx.Response(
            404,
            request=request,
            json={"detail": {"code": "local_run_not_found", "run_id": run_id}},
        )
        raise httpx.HTTPStatusError("run no longer exists", request=request, response=response)

    monkeypatch.setattr("staff.fleet.get_json", fake_get_json)

    response = client.get(f"/api/staff/runs/{run_id}")

    assert response.status_code == 404
    assert remote_runs._cached_peer(run_id) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body", "stall", "max_bytes", "expected_event", "expect_cached"),
    [
        (
            b'{"detail":{"code":"local_run_not_found","run_id":"run-stream-error"}}',
            False,
            4096,
            "error",
            False,
        ),
        (
            b'{"detail":{"code":"local_run_not_found","run_id":"run-stream-error"}}',
            True,
            4096,
            "node_unreachable",
            True,
        ),
        (b"x" * 128, False, 64, "node_unreachable", True),
    ],
)
def test_remote_stream_bounds_error_body_without_limiting_live_streams(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
    stall: bool,
    max_bytes: int,
    expected_event: str,
    expect_cached: bool,
) -> None:
    import staff.remote_runs as remote_runs  # noqa: PLC0415

    run_id = "run-stream-error"
    remote_runs._remember_peer(run_id, "Peer", "http://peer:8321")
    monkeypatch.setattr(remote_runs, "_STREAM_ERROR_BODY_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(remote_runs, "_STREAM_ERROR_BODY_MAX_BYTES", max_bytes)

    class FakeResponse:
        status_code = 404

        async def aiter_bytes(self, chunk_size: int) -> AsyncIterator[bytes]:
            yield body
            if stall:
                await asyncio.sleep(1)

    class StreamContext:
        async def __aenter__(self) -> FakeResponse:
            return FakeResponse()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class FakeClient:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            del timeout

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def stream(self, *_args: object, **_kwargs: object) -> StreamContext:
            return StreamContext()

    monkeypatch.setattr(remote_runs.httpx, "AsyncClient", FakeClient)

    async def collect() -> list[str]:
        return [
            event
            async for event in remote_runs.stream_remote_run(
                run_id,
                after=0,
                peer_name="Peer",
                peer_url="http://peer:8321",
            )
        ]

    started = time.monotonic()
    events = asyncio.run(collect())

    assert time.monotonic() - started < 0.3
    assert len(events) == 1
    assert events[0].startswith(f"event: {expected_event}\n")
    assert (remote_runs._cached_peer(run_id) is not None) is expect_cached


@pytest.mark.unit
def test_remote_run_lookup_bounds_peer_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    import staff.remote_runs as remote_runs  # noqa: PLC0415

    monkeypatch.setattr(remote_runs, "_LOOKUP_SEMAPHORE", asyncio.Semaphore(remote_runs._LOOKUP_CONCURRENCY))
    current = 0
    peak = 0

    async def fake_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        try:
            await asyncio.sleep(0.01)
            return {"run": {"id": "run-concurrency-bound"}, "events": [], "attempts": []}
        finally:
            current -= 1

    async def lookup() -> None:
        peers = {f"Peer{i}": f"http://peer{i}" for i in range(20)}
        await remote_runs.find_remote_run("run-concurrency-bound", peers=peers)

    monkeypatch.setattr("staff.fleet.get_json", fake_get_json)
    asyncio.run(lookup())
    assert peak <= remote_runs._LOOKUP_CONCURRENCY
    assert peak > 1


@pytest.mark.unit
def test_remote_run_lookup_has_a_finite_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    import staff.remote_runs as remote_runs  # noqa: PLC0415

    monkeypatch.setattr(remote_runs, "_LOOKUP_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(remote_runs, "_LOOKUP_SEMAPHORE", asyncio.Semaphore(remote_runs._LOOKUP_CONCURRENCY))

    async def slow_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
        await asyncio.sleep(1)
        return {"run": {"id": "run-too-late"}}

    async def lookup() -> None:
        await remote_runs.find_remote_run("run-timeout-bound", peers={"SlowPeer": "http://slow-peer"})

    monkeypatch.setattr("staff.fleet.get_json", slow_get_json)
    started = time.monotonic()
    with pytest.raises(remote_runs.RemoteRunLookupUnavailable):
        asyncio.run(lookup())
    assert time.monotonic() - started < 0.3


@pytest.mark.unit
def test_remote_run_lookup_cancellation_joins_child_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    import staff.remote_runs as remote_runs  # noqa: PLC0415

    monkeypatch.setattr(remote_runs, "_LOOKUP_SEMAPHORE", asyncio.Semaphore(remote_runs._LOOKUP_CONCURRENCY))
    started = asyncio.Event()
    active = 0
    cancelled = 0

    async def slow_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
        nonlocal active, cancelled
        active += 1
        if active == 2:
            started.set()
        try:
            await asyncio.sleep(1)
            return {"run": {"id": "run-too-late"}}
        except asyncio.CancelledError:
            cancelled += 1
            raise
        finally:
            active -= 1

    async def cancel_lookup() -> None:
        task = asyncio.create_task(
            remote_runs.find_remote_run(
                "run-cancel-bound",
                peers={"SlowOne": "http://slow-one", "SlowTwo": "http://slow-two"},
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    monkeypatch.setattr("staff.fleet.get_json", slow_get_json)
    asyncio.run(cancel_lookup())
    assert active == 0
    assert cancelled == 2


@pytest.mark.unit
def test_remote_run_peer_cache_is_ttl_and_size_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    import staff.remote_runs as remote_runs  # noqa: PLC0415

    now = [100.0]
    monkeypatch.setattr(remote_runs.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(remote_runs, "_RUN_PEER_CACHE_LIMIT", 2)
    monkeypatch.setattr(remote_runs, "_RUN_PEER_CACHE_TTL_SECONDS", 10.0)
    remote_runs._RUN_PEER_CACHE.clear()
    remote_runs._remember_peer("run-cache-a", "A", "http://a")
    remote_runs._remember_peer("run-cache-b", "B", "http://b")
    remote_runs._remember_peer("run-cache-c", "C", "http://c")

    assert len(remote_runs._RUN_PEER_CACHE) == 2
    assert remote_runs._cached_peer("run-cache-a") is None
    assert remote_runs._cached_peer("run-cache-b") == ("B", "http://b")

    now[0] = 111.0
    assert remote_runs._cached_peer("run-cache-b") is None


@pytest.mark.unit
def test_hub_proxies_remote_cancel(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    remote_data = {
        "run": {"id": "run-rem-cancel", "machine": "OGLaptop", "status": "running"},
        "events": [],
        "attempts": [],
    }
    requests: list[str] = []

    async def fake_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
        requests.append(url)
        if "og:8321/api/staff/local-runs/run-rem-cancel" in url:
            return remote_data
        raise ConnectionError("not found")

    async def fake_post_json(url: str, data: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        requests.append(url)
        if "og:8321/api/staff/local-runs/run-rem-cancel/cancel" in url:
            return 200, {"ok": True, "run_id": "run-rem-cancel", "cancelled": True}
        raise ConnectionError("cancel failed")

    monkeypatch.setattr("staff.fleet.get_json", fake_get_json)
    monkeypatch.setattr("staff.fleet.post_json", fake_post_json)
    monkeypatch.setattr("staff.fleet.peer_nodes", lambda: {"OGLaptop": "http://og:8321"})

    resp = client.post("/api/staff/runs/run-rem-cancel/cancel")
    assert resp.status_code == 200
    assert resp.json()["cancelled"] is True
    assert len(requests) == 2
    assert all("/api/staff/runs/" not in url for url in requests)


@pytest.mark.unit
def test_hub_proxies_remote_stream(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    remote_data = {
        "run": {"id": "run-rem-stream", "machine": "OGLaptop", "status": "running"},
        "events": [],
        "attempts": [],
    }
    requests: list[str] = []

    async def fake_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
        requests.append(url)
        if "og:8321/api/staff/local-runs/run-rem-stream" in url:
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
            requests.append(url)
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
    assert len(requests) == 2
    assert all("/api/staff/runs/" not in url for url in requests)


@pytest.mark.unit
def test_answer_thread_run_endpoint(
    client: TestClient, conv_store: Any, run_store: RunStore, staff_launches: list[str], staff_cli_installed
) -> None:
    staff_cli_installed("claude")
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
    assert staff_launches == [data["continuation_run_id"]]


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
