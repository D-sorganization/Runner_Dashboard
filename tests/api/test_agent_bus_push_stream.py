"""Agent-bus push: per-session SSE inbox stream + doorbell wake."""

from __future__ import annotations

import asyncio

import pytest
from agent_bus import doorbell
from fastapi.testclient import TestClient

from tests.api.test_agent_bus_slice1 import _auth, _register, bus_env, client  # noqa: F401


def _send(tc: TestClient, token: str, session: str, key: str) -> dict:
    r = tc.post(
        "/api/v1/agent/messages",
        headers=_auth(token),
        json={
            "session": session,
            "to": "issue-remediator",
            "type": "status",
            "repo": "Runner_Dashboard",
            "work_kind": "issue",
            "work_number": 1,
            "body": f"ping {key}",
            "idempotency_key": key,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["message"]


def _setup(tc: TestClient, env: dict[str, str]) -> tuple[str, str]:
    a, b = "pr-remediator-push-a", "issue-remediator-push-b"
    _register(tc, token=env["pr-remediator"], session=a, role="pr-remediator", provider="cursor", node="Hub")
    _register(tc, token=env["issue-remediator"], session=b, role="issue-remediator", provider="codex", node="Hub")
    return a, b


@pytest.mark.unit
def test_stream_replays_pending_and_resumes(client: TestClient, bus_env: dict[str, str]) -> None:  # noqa: F811
    a, b = _setup(client, bus_env)
    gen_before = doorbell.generation()
    m1 = _send(client, bus_env["pr-remediator"], a, "push-key-1")
    assert doorbell.generation() > gen_before
    r = client.get(f"/api/v1/agent/inbox/stream?session={b}&follow=false", headers=_auth(bus_env["issue-remediator"]))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert f"id: {m1['cursor']}" in r.text and "event: message" in r.text
    m2 = _send(client, bus_env["pr-remediator"], a, "push-key-2")
    r2 = client.get(
        f"/api/v1/agent/inbox/stream?session={b}&follow=false",
        headers={**_auth(bus_env["issue-remediator"]), "Last-Event-ID": str(m1["cursor"])},
    )
    assert "ping push-key-1" not in r2.text and "ping push-key-2" in r2.text and f"id: {m2['cursor']}" in r2.text


@pytest.mark.unit
def test_stream_rejects_foreign_session(client: TestClient, bus_env: dict[str, str]) -> None:  # noqa: F811
    _a, b = _setup(client, bus_env)
    r = client.get(f"/api/v1/agent/inbox/stream?session={b}&follow=false", headers=_auth(bus_env["pr-remediator"]))
    assert r.status_code == 403


@pytest.mark.unit
def test_doorbell_wakes_waiter_from_other_thread() -> None:
    async def run() -> float:
        loop = asyncio.get_running_loop()
        gen = doorbell.generation()
        start = loop.time()
        loop.call_later(0.05, lambda: loop.run_in_executor(None, doorbell.ring))
        woke = await doorbell.wait(gen, 5.0)
        assert woke
        return loop.time() - start

    assert asyncio.run(run()) < 1.0
    assert doorbell.subscriber_count() == 0


@pytest.mark.unit
def test_doorbell_times_out_without_ring() -> None:
    gen = doorbell.generation()
    assert asyncio.run(doorbell.wait(gen, 0.05)) is False
