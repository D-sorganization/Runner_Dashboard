"""Federated staff run lookup regression tests."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from staff.store import RunRecord, RunStore

from tests.api._staff_thread_run_fixtures import (
    client as _client_fixture,  # noqa: F401
)
from tests.api._staff_thread_run_fixtures import (
    conv_store as _conv_store_fixture,  # noqa: F401
)
from tests.api._staff_thread_run_fixtures import (
    run_store as _run_store_fixture,  # noqa: F401
)


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
