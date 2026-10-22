"""Slice 3 (#2155): stale-claim reaper, backlog surfacing, blocked→Barb, handoff documents."""

from __future__ import annotations

from unittest import mock

import pytest
from fastapi.testclient import TestClient

from tests.api.test_agent_bus_slice1 import _auth, _register, bus_env as _bus_env_fixture

_REMOTE = ("100.64.0.9", 51000)


@pytest.fixture
def bus_env(_bus_env_fixture: dict[str, str]) -> dict[str, str]:
    return _bus_env_fixture


@pytest.fixture
def client(bus_env: dict[str, str]) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=_REMOTE)


@pytest.mark.unit
def test_blocked_message_duplicates_to_barb_inbox(client: TestClient, bus_env: dict[str, str]) -> None:
    sess = "local-brick-20261022-blocked"
    reg = client.post(
        "/api/v1/agent/register",
        headers=_auth(bus_env["fleet"]),
        json={
            "session": sess,
            "role": "local",
            "provider": "cursor",
            "node": "Brick",
            "lease_seconds": 600,
        },
    )
    assert reg.status_code == 200, reg.text
    token = reg.json()["token"]
    send = client.post(
        "/api/v1/agent/messages",
        headers=_auth(token),
        json={
            "session": sess,
            "to": "issue-remediator",
            "type": "blocked",
            "repo": "Runner_Dashboard",
            "work_kind": "issue",
            "work_number": 2155,
            "body": "Need human decision on scope.",
            "idempotency_key": "blocked-2155-v1",
        },
    )
    assert send.status_code == 200, send.text
    from agent_bus.store import get_store

    rows = get_store().list_inbox_for_address("barb", since_cursor=0, limit=20)
    assert any(m["type"] == "blocked" and m["work_number"] == 2155 for m in rows["messages"])


@pytest.mark.unit
def test_reaper_releases_tracked_claim_when_lease_expires(client: TestClient, bus_env: dict[str, str]) -> None:
    sess = "issue-remediator-stale-20261022"
    _register(
        client,
        token=bus_env["issue-remediator"],
        session=sess,
        role="issue-remediator",
        provider="codex",
        node="Hub",
    )
    track = client.post(
        "/api/v1/agent/claims",
        headers=_auth(bus_env["issue-remediator"]),
        json={"session": sess, "repo": "Tools", "issue": 42},
    )
    assert track.status_code == 200, track.text

    from agent_bus.store import get_store

    get_store().expire_session(sess)
    released: list[tuple[str, int, str]] = []

    def fake_release(repo: str, issue: int, *, agent: str, session: str, reason: str) -> dict:
        released.append((repo, issue, reason))
        return {"released": True, "result": {}, "warnings": []}

    with mock.patch("coordination.claims.release", side_effect=fake_release):
        from agent_bus.reaper import reap_once

        summary = reap_once()
    assert summary["released_claims"] >= 1
    assert ("Tools", 42, "agent_bus lease expired") in released


@pytest.mark.unit
def test_backlog_lists_blocked_and_stale_sessions(client: TestClient, bus_env: dict[str, str]) -> None:
    sess = "local-stale-backlog"
    reg = client.post(
        "/api/v1/agent/register",
        headers=_auth(bus_env["fleet"]),
        json={
            "session": sess,
            "role": "local",
            "provider": "cursor",
            "node": "Brick",
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
            "to": "barb",
            "type": "blocked",
            "repo": "Runner_Dashboard",
            "work_kind": "issue",
            "work_number": 99,
            "body": "Blocked on credentials.",
            "idempotency_key": "blocked-99-direct",
        },
    )
    from agent_bus.store import get_store

    get_store().expire_session(sess)
    backlog = client.get("/api/v1/agent/backlog", headers=_auth(bus_env["fleet"]))
    assert backlog.status_code == 200, backlog.text
    body = backlog.json()
    assert body["blocked"]
    assert any(s["session"] == sess for s in body["stale_sessions"])


@pytest.mark.unit
def test_handoff_document_revision_on_work_item_thread(client: TestClient, bus_env: dict[str, str]) -> None:
    sess = "pr-remediator-handoff-doc"
    _register(
        client,
        token=bus_env["pr-remediator"],
        session=sess,
        role="pr-remediator",
        provider="cursor",
        node="Hub",
    )
    first = client.put(
        "/api/v1/agent/handoff",
        headers=_auth(bus_env["pr-remediator"]),
        json={
            "session": sess,
            "repo": "AffineDrift",
            "work_kind": "issue",
            "work_number": 5117,
            "body": {"summary": "PR merged; verify on main.", "next_step": "Close loop"},
        },
    )
    assert first.status_code == 200, first.text
    doc = first.json()["handoff_document"]
    assert doc["revision"] == 1 and doc["schema_version"] == 1

    from staff.conversations import get_conversation_store

    thread_id = first.json()["thread_id"]
    thread = get_conversation_store().get_thread(thread_id)
    assert thread is not None and thread.meta.get("handoff_document", {}).get("revision") == 1
