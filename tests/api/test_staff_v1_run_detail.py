"""GET /api/v1/staff/runs/{id} returns the run, its events and its attempts.

The v1 route called ``RunStore.list_events``, which does not exist, so every run
detail read through the versioned API was a 500 (``internal_error``) while the
legacy ``/api/staff/runs/{id}`` route worked. Barb and other agents use v1.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from routers import staff as staff_router
from routers import staff_v1
from staff.store import RunRecord, RunStore

READER = Principal(id="agent-test", type="agent", name="Test", roles=["agent"], scopes=["staff.read"])


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RunStore:
    runs = RunStore(tmp_path / "runs.sqlite3")
    fake_runner = SimpleNamespace(store=runs)
    monkeypatch.setattr(staff_v1, "get_runner", lambda: fake_runner)
    monkeypatch.setattr(staff_router, "get_runner", lambda: fake_runner)
    return runs


@pytest.fixture
def client(store: RunStore) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(staff_v1.router)
    app.dependency_overrides[require_scope("staff.read")] = lambda: READER
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _run(run_id: str) -> RunRecord:
    return RunRecord(
        id=run_id,
        role="cartographer",
        provider="claude",
        model="sonnet",
        machine="TestNode",
        repo="AffineDrift",
        target_kind="prompt",
        target_ref="",
        prompt="map it",
        status="succeeded",
    )


@pytest.mark.unit
def test_v1_run_detail_returns_run_events_and_attempts(client: TestClient, store: RunStore) -> None:
    store.create_run(_run("run-v1detail"))
    store.append_event("run-v1detail", "text", "mapping AffineDrift")
    store.append_event("run-v1detail", "result", "STAFF_RESULT: done")

    res = client.get("/api/v1/staff/runs/run-v1detail")

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["run"]["id"] == "run-v1detail"
    assert [e["kind"] for e in body["events"]] == ["text", "result"]
    assert [a["id"] for a in body["attempts"]] == ["run-v1detail"]


@pytest.mark.unit
def test_v1_run_detail_unknown_run_is_structured_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_remote(run_id: str) -> None:
        return None

    import staff.remote_runs as remote_runs  # noqa: PLC0415

    monkeypatch.setattr(remote_runs, "find_remote_run", no_remote)

    res = client.get("/api/v1/staff/runs/run-missing")

    assert res.status_code == 404
    assert "not_found" in res.text


@pytest.mark.unit
def test_v1_run_stream_replays_events_and_ends(client: TestClient, store: RunStore) -> None:
    """The v1 stream passed ``request=`` to the legacy handler, which takes no such argument (#1734)."""
    store.create_run(_run("run-v1stream"))
    store.append_event("run-v1stream", "text", "mapping AffineDrift")

    res = client.get("/api/v1/staff/runs/run-v1stream/stream")

    assert res.status_code == 200, res.text
    assert "event: text" in res.text
    assert "event: end" in res.text
