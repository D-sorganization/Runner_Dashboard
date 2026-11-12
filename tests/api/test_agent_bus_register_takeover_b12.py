"""Agent-bus register takeover hardening (WP19 #4274, B-12).

Before B-12, any ``coordination.write`` caller could POST /register for an
existing live session and mint a fresh session token, displacing the owner.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from identity import identity_manager, principal_has_scope

from tests.api.test_agent_bus_slice1 import _auth, bus_env

_REMOTE = ("100.64.0.9", 51000)

__all__ = ["bus_env"]


@pytest.fixture
def client(bus_env: dict[str, str]) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=_REMOTE)


def _register_raw(
    client: TestClient,
    *,
    token: str,
    session: str,
    role: str = "local",
    provider: str = "cursor",
    node: str = "node-c",
):
    return client.post(
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


@pytest.mark.unit
def test_fleet_minted_live_session_rejects_foreign_writer(client: TestClient, bus_env: dict[str, str]) -> None:
    """B-12: a second fleet-peer register must not replace a live minted session."""
    sess = "cursor-node-c-20261112-takeover-a"
    first = _register_raw(client, token=bus_env["fleet"], session=sess)
    assert first.status_code == 200, first.text
    victim_token = first.json().get("token")
    assert isinstance(victim_token, str) and victim_token

    takeover = _register_raw(client, token=bus_env["fleet"], session=sess)
    assert takeover.status_code == 409, takeover.text


@pytest.mark.unit
def test_live_session_owner_may_reregister_with_session_token(client: TestClient, bus_env: dict[str, str]) -> None:
    sess = "cursor-node-c-20261112-rereg-b"
    first = _register_raw(client, token=bus_env["fleet"], session=sess)
    assert first.status_code == 200, first.text
    session_token = first.json()["token"]

    renew = _register_raw(client, token=session_token, session=sess)
    assert renew.status_code == 200, renew.text
    new_token = renew.json().get("token")
    assert isinstance(new_token, str) and new_token
    assert principal_has_scope(identity_manager.verify_token(new_token), "coordination.write")


@pytest.mark.unit
def test_agent_principal_blocks_cross_agent_takeover(client: TestClient, bus_env: dict[str, str]) -> None:
    sess = "pr-remediator-20261112-c"
    assert (
        _register_raw(
            client,
            token=bus_env["pr-remediator"],
            session=sess,
            role="pr-remediator",
            provider="cursor",
            node="Hub",
        ).status_code
        == 200
    )
    blocked = _register_raw(
        client,
        token=bus_env["issue-remediator"],
        session=sess,
        role="issue-remediator",
        provider="cursor",
        node="Hub",
    )
    assert blocked.status_code == 409


@pytest.mark.unit
def test_expired_session_allows_fresh_register(client: TestClient, bus_env: dict[str, str]) -> None:
    sess = "cursor-node-c-20261112-expired-d"
    first = _register_raw(client, token=bus_env["fleet"], session=sess)
    assert first.status_code == 200, first.text

    from agent_bus.store import get_store

    get_store().expire_session(sess)

    again = _register_raw(client, token=bus_env["fleet"], session=sess)
    assert again.status_code == 200, again.text
    assert isinstance(again.json().get("token"), str)
