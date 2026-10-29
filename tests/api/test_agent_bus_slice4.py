"""Slice 4 (#2155): dashboard overview API for live agents and activity."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.api.test_agent_bus_slice1 import _auth, bus_env as _bus_env_fixture

_REMOTE = ("100.64.0.9", 51000)


@pytest.fixture
def bus_env(_bus_env_fixture: dict[str, str]) -> dict[str, str]:
    return _bus_env_fixture


@pytest.fixture
def client(bus_env: dict[str, str]) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=_REMOTE)


@pytest.mark.unit
def test_overview_lists_live_agent_and_recent_activity(client: TestClient, bus_env: dict[str, str]) -> None:
    sess = "local-overview-20261022"
    reg = client.post(
        "/api/v1/agent/register",
        headers=_auth(bus_env["fleet"]),
        json={
            "session": sess,
            "role": "local",
            "provider": "cursor",
            "node": "node-c",
            "lease_seconds": 600,
        },
    )
    assert reg.status_code == 200
    token = reg.json()["token"]
    client.post(
        "/api/v1/agent/messages",
        headers=_auth(token),
        json={
            "session": sess,
            "to": "issue-remediator",
            "type": "status",
            "repo": "Runner_Dashboard",
            "work_kind": "issue",
            "work_number": 12,
            "body": "Working slice 4 view.",
            "idempotency_key": "status-12-overview",
        },
    )
    overview = client.get("/api/v1/agent/overview", headers=_auth(bus_env["fleet"]))
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert any(a["session"] == sess for a in body["agents"])
    assert body["activity"]
    assert any(t["thread_id"].startswith("wi-") for t in body["threads"])
    # A live agent exists, so there is nothing to explain away as empty (#3504).
    assert body["empty_reason"] is None


@pytest.mark.unit
def test_overview_empty_reason_is_complete_empty_when_nothing_ever_registered(
    client: TestClient, bus_env: dict[str, str]
) -> None:
    """No principal has ever registered: #3504 must not conflate this with stale sessions."""
    overview = client.get("/api/v1/agent/overview", headers=_auth(bus_env["fleet"]))
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["agents"] == []
    assert body["empty_reason"] == "complete_empty"


@pytest.mark.unit
def test_overview_empty_reason_is_local_empty_when_all_known_sessions_are_stale(
    client: TestClient, bus_env: dict[str, str]
) -> None:
    """A session registered and then expired: zero live agents must read as stale, not absent (#3504)."""
    sess = "local-overview-stale-20261029"
    reg = client.post(
        "/api/v1/agent/register",
        headers=_auth(bus_env["fleet"]),
        json={
            "session": sess,
            "role": "local",
            "provider": "cursor",
            "node": "node-c",
            "lease_seconds": 600,
        },
    )
    assert reg.status_code == 200, reg.text

    from agent_bus.store import get_store

    get_store().expire_session(sess)

    overview = client.get("/api/v1/agent/overview", headers=_auth(bus_env["fleet"]))
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["agents"] == []
    assert body["empty_reason"] == "local_empty"
    assert any(s["session"] == sess for s in body["backlog"]["stale_sessions"])
