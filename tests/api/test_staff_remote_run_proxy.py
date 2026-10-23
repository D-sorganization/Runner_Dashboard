"""Federated staff run lookup regression tests."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

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
