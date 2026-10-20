"""HTTP-level admission behavior for the server drain switch (issue #2013)."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Response
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import health  # noqa: E402
import server  # noqa: E402


@pytest.fixture
def loopback_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    """Exercise the mounted server app without starting its lifespan tasks."""
    monkeypatch.setattr(server, "_drain_mode", False)
    client = TestClient(server.app, client=("127.0.0.1", 43120), raise_server_exceptions=False)
    try:
        yield client
    finally:
        client.close()


@contextmanager
def _mounted_probe(path: str, *, methods: list[str], action: Callable[[], object]) -> Iterator[None]:
    """Temporarily add a side-effect probe to the actual server application."""
    route = APIRoute(path, action, methods=methods)
    catch_all_index = next(
        (
            index
            for index, existing in enumerate(server.app.router.routes)
            if getattr(existing, "path", None) == "/{full_path:path}"
        ),
        len(server.app.router.routes),
    )
    server.app.router.routes.insert(catch_all_index, route)
    try:
        yield
    finally:
        server.app.router.routes.remove(route)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_drain_rejects_new_mutations_before_handler_side_effects(loopback_client: TestClient, method: str) -> None:
    effects: list[str] = []

    def mutate() -> dict[str, str]:
        effects.append(method)
        return {"status": "mutated"}

    with _mounted_probe("/__test_drain_mutation", methods=[method], action=mutate):
        before = loopback_client.request(method, "/__test_drain_mutation")
        assert before.status_code == 200
        assert effects == [method]
        effects.clear()

        assert loopback_client.post("/_drain").status_code == 200

        response = loopback_client.request(method, "/__test_drain_mutation")

    assert response.status_code == 503
    assert effects == [], "draining must reject mutation before invoking the route handler"


def test_drain_leaves_diagnostics_available_and_marks_readiness_unready(
    loopback_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_aggregate = AsyncMock(return_value=(200, {"status": "ok", "checks": {}}))
    monkeypatch.setattr(health, "aggregate", mock_aggregate)

    assert loopback_client.post("/_drain").status_code == 200

    ready = loopback_client.get("/readyz")
    live = loopback_client.get("/livez")
    assert ready.status_code == 503
    mock_aggregate.assert_not_awaited()
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}


def test_drain_keeps_get_and_head_diagnostic_methods_available(loopback_client: TestClient) -> None:
    calls: list[str] = []

    def diagnostic() -> Response:
        calls.append("diagnostic")
        return Response(content="ok")

    with _mounted_probe("/__test_drain_diagnostic", methods=["GET", "HEAD", "OPTIONS"], action=diagnostic):
        assert loopback_client.post("/_drain").status_code == 200
        get_response = loopback_client.get("/__test_drain_diagnostic")
        head_response = loopback_client.head("/__test_drain_diagnostic")
        options_response = loopback_client.options("/__test_drain_diagnostic")

    assert get_response.status_code == 200
    assert head_response.status_code == 200
    assert options_response.status_code == 200
    assert calls == ["diagnostic", "diagnostic", "diagnostic"]


def test_repeated_loopback_drain_is_safe(loopback_client: TestClient) -> None:
    first = loopback_client.post("/_drain")
    second = loopback_client.post("/_drain")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"status": "draining"}


@pytest.mark.parametrize("initial_state", [False, True])
def test_non_loopback_drain_request_keeps_forbidden_response_and_state(
    monkeypatch: pytest.MonkeyPatch, initial_state: bool
) -> None:
    client = TestClient(server.app, client=("10.0.0.5", 43120), raise_server_exceptions=False)
    monkeypatch.setattr(server, "_drain_mode", initial_state)
    try:
        response = client.post("/_drain")
        assert response.status_code == 403
        assert server._drain_mode is initial_state
    finally:
        client.close()


@pytest.mark.asyncio
async def test_request_admitted_before_drain_finishes_but_later_mutation_is_rejected() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    effects: list[str] = []

    async def slow_mutation() -> dict[str, str]:
        effects.append("started")
        if len(effects) == 1:
            entered.set()
        await release.wait()
        effects.append("finished")
        return {"status": "finished"}

    server._drain_mode = False
    admitted: asyncio.Task[httpx.Response] | None = None
    later: asyncio.Task[httpx.Response] | None = None
    completed: httpx.Response | None = None
    later_response: httpx.Response | None = None
    later_done: set[asyncio.Task[httpx.Response]] = set()
    drain: httpx.Response | None = None
    with _mounted_probe("/__test_drain_slow_mutation", methods=["POST"], action=slow_mutation):
        transport = httpx.ASGITransport(app=server.app, client=("127.0.0.1", 43120))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            try:
                admitted = asyncio.create_task(client.post("/__test_drain_slow_mutation"))
                await asyncio.wait_for(entered.wait(), timeout=2)
                drain = await client.post("/_drain")
                later = asyncio.create_task(client.post("/__test_drain_slow_mutation"))
                later_done, _ = await asyncio.wait({later}, timeout=2)
                release.set()
                completed = await asyncio.wait_for(admitted, timeout=2)
                later_response = await asyncio.wait_for(later, timeout=2)
            finally:
                release.set()
                server._drain_mode = False
                active_tasks = [task for task in (admitted, later) if task is not None]
                if active_tasks:
                    await asyncio.wait_for(asyncio.gather(*active_tasks, return_exceptions=True), timeout=2)

    assert drain is not None
    assert completed is not None
    assert later_response is not None
    assert drain.status_code == 200
    assert later_done, "later mutation was neither rejected nor admitted within the bound"
    assert later_response.status_code == 503
    assert completed.status_code == 200
    assert completed.json() == {"status": "finished"}
    assert effects == ["started", "finished"]
