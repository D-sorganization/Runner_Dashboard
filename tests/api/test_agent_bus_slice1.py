"""Slice 1 (#2155): principal registry and dashboard-local agent inbox API."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import Principal, TokenRecord, identity_manager, principal_has_scope
from staff import store as store_mod

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.9", 51000)


def _principal(agent: str) -> Principal:
    pid = f"agent-{agent}"
    return Principal(id=pid, type="bot", name=agent, roles=["bot"])


@pytest.fixture
def bus_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    id_dir = tmp_path / "identity"
    id_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(id_dir))
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "roles"))
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-bootstrap-token")
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")

    store_mod.reset_store()
    from agent_bus.store import reset_store as reset_agent_bus_store

    reset_agent_bus_store()
    identity_manager.config_dir = id_dir
    identity_manager.principals_path = id_dir / "principals.yml"
    identity_manager.tokens_path = id_dir / "tokens.yml"
    identity_manager._ephemeral_principals.clear()
    identity_manager._ephemeral_tokens.clear()

    principals = {
        "pr-remediator": _principal("pr-remediator"),
        "issue-remediator": _principal("issue-remediator"),
    }
    tokens: dict[str, str] = {}
    records: list[TokenRecord] = []
    for agent, prin in principals.items():
        raw = f"token-{agent}"
        tokens[agent] = raw
        records.append(
            TokenRecord(
                token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                principal_id=prin.id,
                created_at=0.0,
                name=prin.id,
            )
        )
    identity_manager.principals = {p.id: p for p in principals.values()}
    identity_manager.tokens = records
    identity_manager.save_principals()
    identity_manager.save_tokens()

    yield {**tokens, "fleet": "fleet-bootstrap-token"}


@pytest.fixture
def client(bus_env: dict[str, str]) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=_REMOTE)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **_XHR}


def _register(
    client: TestClient,
    *,
    token: str,
    session: str,
    role: str,
    provider: str,
    node: str,
) -> dict:
    r = client.post(
        "/api/v1/agent/register",
        headers=_auth(token),
        json={
            "session": session,
            "role": role,
            "provider": provider,
            "node": node,
            "lease_seconds": 600,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.unit
def test_handoff_affine_drift_5117_between_remediators(client: TestClient, bus_env: dict[str, str]) -> None:
    """pr-remediator hands AffineDrift#5117 to issue-remediator; thread inherits the message."""
    pr_sess = "pr-remediator-20261022-a"
    issue_sess = "issue-remediator-20261022-b"
    _register(
        client,
        token=bus_env["pr-remediator"],
        session=pr_sess,
        role="pr-remediator",
        provider="cursor",
        node="Hub",
    )
    _register(
        client,
        token=bus_env["issue-remediator"],
        session=issue_sess,
        role="issue-remediator",
        provider="codex",
        node="Hub",
    )

    send = client.post(
        "/api/v1/agent/messages",
        headers=_auth(bus_env["pr-remediator"]),
        json={
            "session": pr_sess,
            "to": "issue-remediator",
            "type": "handoff",
            "repo": "AffineDrift",
            "work_kind": "issue",
            "work_number": 5117,
            "body": "PR remediation done; please close the issue loop.",
            "idempotency_key": "handoff-affine-5117-v1",
        },
    )
    assert send.status_code == 200, send.text
    msg = send.json()["message"]
    assert msg["type"] == "handoff" and msg["repo"] == "AffineDrift" and msg["work_number"] == 5117

    inbox = client.get(
        f"/api/v1/agent/inbox?session={issue_sess}&since_cursor=0&limit=20",
        headers=_auth(bus_env["issue-remediator"]),
    )
    assert inbox.status_code == 200
    items = inbox.json()["messages"]
    assert len(items) == 1 and items[0]["id"] == msg["id"]

    from staff.conversations import get_conversation_store

    thread_msgs = get_conversation_store().list_messages(msg["thread_id"])
    assert any(m.kind == "handoff" for m in thread_msgs)

    dup = client.post(
        "/api/v1/agent/messages",
        headers=_auth(bus_env["pr-remediator"]),
        json={
            "session": pr_sess,
            "to": "issue-remediator",
            "type": "handoff",
            "repo": "AffineDrift",
            "work_kind": "issue",
            "work_number": 5117,
            "body": "PR remediation done; please close the issue loop.",
            "idempotency_key": "handoff-affine-5117-v1",
        },
    )
    assert dup.status_code == 200
    assert dup.json()["message"]["id"] == msg["id"]

    ack = client.post(
        "/api/v1/agent/messages/ack",
        headers=_auth(bus_env["issue-remediator"]),
        json={"session": issue_sess, "message_id": msg["id"]},
    )
    assert ack.status_code == 200 and ack.json()["acked"] is True


@pytest.mark.unit
def test_local_cursor_on_node_c_registers_with_fleet_peer(client: TestClient, bus_env: dict[str, str]) -> None:
    """An unregistered local Cursor session on node node-c can register and heartbeat."""
    sess = "cursor-node-c-20261022-night"
    reg = client.post(
        "/api/v1/agent/register",
        headers=_auth(bus_env["fleet"]),
        json={
            "session": sess,
            "role": "local",
            "provider": "cursor",
            "node": "node-c",
            "lease_seconds": 120,
        },
    )
    assert reg.status_code == 200, reg.text
    body = reg.json()
    assert body["session"] == sess and body.get("token")
    assert principal_has_scope(identity_manager.verify_token(body["token"]), "coordination.write")

    session_token = body["token"]
    hb = client.post(
        "/api/v1/agent/heartbeat",
        headers=_auth(session_token),
        json={"session": sess, "lease_seconds": 120},
    )
    assert hb.status_code == 200 and hb.json()["session"] == sess

    bad = client.post(
        "/api/v1/agent/messages",
        headers=_auth(session_token),
        json={
            "session": sess,
            "to": "issue-remediator",
            "type": "status",
            "repo": "Runner_Dashboard",
            "body": "missing work number",
            "idempotency_key": "bad-1",
        },
    )
    assert bad.status_code == 422


@pytest.mark.unit
def test_send_requires_live_lease(client: TestClient, bus_env: dict[str, str]) -> None:
    pr_sess = "pr-remediator-expired"
    _register(
        client,
        token=bus_env["pr-remediator"],
        session=pr_sess,
        role="pr-remediator",
        provider="cursor",
        node="Hub",
    )
    from agent_bus import store as bus_store_mod  # noqa: PLC0415

    bus_store_mod.get_store().expire_session(pr_sess)
    denied = client.post(
        "/api/v1/agent/messages",
        headers=_auth(bus_env["pr-remediator"]),
        json={
            "session": pr_sess,
            "to": "issue-remediator",
            "type": "request",
            "repo": "Tools",
            "work_kind": "pr",
            "work_number": 99,
            "body": "ping",
            "idempotency_key": "request-pr-99",
        },
    )
    assert denied.status_code == 409
