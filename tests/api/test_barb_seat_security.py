"""Barb seat security: holder bound to caller, gated takeover, heartbeat, memory.read for agents."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import Principal, identity_manager

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.31", 52031)
pytestmark = pytest.mark.integration

_TOKENS = {
    "alpha": Principal(id="agent-alpha", type="bot", name="Alpha", roles=["bot"]),
    "beta": Principal(id="agent-beta", type="bot", name="Beta", roles=["bot"]),
    "oper": Principal(id="op-1", type="human", name="Operator", roles=["operator"]),
}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    import orchestration_audit  # noqa: PLC0415
    from memory.store import reset_memory_store  # noqa: PLC0415
    from staff import barb_continuity  # noqa: PLC0415

    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-secret-token")
    monkeypatch.setenv("ASSISTANT_MEMORY_DB", str(tmp_path / "memory.sqlite3"))
    monkeypatch.setattr(orchestration_audit, "ORCHESTRATION_AUDIT_PATH", tmp_path / "audit.ndjson")
    monkeypatch.setattr(
        barb_continuity, "_DEFAULT_STORE", barb_continuity.BarbContinuityStore(tmp_path / "barb.sqlite3")
    )
    reset_memory_store()
    for name, prin in _TOKENS.items():
        identity_manager.add_ephemeral_token(hashlib.sha256(f"tok-{name}".encode()).hexdigest(), prin, name=name)
    from server import app  # noqa: PLC0415

    yield TestClient(app, raise_server_exceptions=False, client=_REMOTE)
    reset_memory_store()


def _h(name: str) -> dict[str, str]:
    return {**_XHR, "Authorization": f"Bearer tok-{name}"}


def _checkout(client: TestClient, who: str, **body):
    return client.post("/api/v1/staff/barb/leases/checkout", headers=_h(who), json={"scope": "barb:pr-queue", **body})


def test_bot_reads_memory_but_cannot_review(client: TestClient) -> None:
    assert client.get("/api/v1/memory/context-pack", headers=_h("alpha")).status_code == 200
    r = client.patch("/api/v1/memory/nope/review", headers=_h("alpha"), json={"status": "accepted"})
    assert r.status_code == 403


def test_fleet_bearer_reaches_barb_routes_and_memory(client: TestClient) -> None:
    hdr = {**_XHR, "Authorization": "Bearer fleet-secret-token"}
    assert client.get("/api/v1/staff/barb/continuity", headers=hdr).status_code == 200
    assert client.get("/api/v1/memory/context-pack", headers=hdr).status_code == 200
    r = client.post("/api/v1/staff/barb/leases/checkout", headers=hdr, json={"scope": "barb:night-watch"})
    assert r.status_code == 200
    assert r.json()["lease"]["holder"] == "fleet-peer"


def test_holder_defaults_to_caller_and_spoof_rejected(client: TestClient) -> None:
    ok = _checkout(client, "alpha")
    assert ok.status_code == 200
    assert ok.json()["lease"]["holder"] == "agent-alpha"
    spoof = _checkout(client, "beta", holder="agent-alpha")
    assert spoof.status_code == 403
    suffixed = _checkout(client, "beta", holder="agent-beta:seat2", scope="barb:ci-shepherd")
    assert suffixed.status_code == 200


def test_cannot_release_or_renew_other_lease(client: TestClient) -> None:
    _checkout(client, "alpha")
    rel = client.post(
        "/api/v1/staff/barb/leases/release",
        headers=_h("beta"),
        json={"scope": "barb:pr-queue", "holder": "agent-alpha", "force": True},
    )
    assert rel.status_code == 403
    own = client.post(
        "/api/v1/staff/barb/leases/release", headers=_h("beta"), json={"scope": "barb:pr-queue", "force": True}
    )
    assert own.status_code == 409  # holder_mismatch against the real holder
    assert _checkout(client, "beta").status_code == 409  # renew path is not a back door


def test_takeover_requires_operator_and_reason_and_audits(client: TestClient, tmp_path: Path) -> None:
    first = _checkout(client, "alpha").json()["lease"]
    client.post(
        "/api/v1/staff/barb/turnover",
        headers=_h("alpha"),
        json={"scope": "barb:pr-queue", "summary": "s", "next_actions": ["do x"]},
    )
    assert _checkout(client, "beta", takeover=True, reason="stuck").status_code == 403
    assert _checkout(client, "oper", takeover=True).status_code == 422
    ok = _checkout(client, "oper", takeover=True, reason="alpha wedged")
    assert ok.status_code == 200, ok.text
    lease = ok.json()["lease"]
    assert lease["holder"] == "op-1"
    assert lease["epoch"] == first["epoch"] + 1
    assert lease["last_turnover_id"]  # inherited
    rows = [json.loads(x) for x in (tmp_path / "audit.ndjson").read_text().splitlines()]
    assert rows[-1]["action"] == "barb.lease.takeover"
    assert rows[-1]["reason"] == "alpha wedged"


def test_ttl_over_cap_rejected(client: TestClient) -> None:
    assert _checkout(client, "alpha", ttl_sec=86400).status_code == 422
    assert _checkout(client, "alpha", ttl_sec=14400).status_code == 200


def test_heartbeat_extends_own_lease_only(client: TestClient) -> None:
    lease = _checkout(client, "alpha", ttl_sec=60).json()["lease"]
    url = "/api/v1/staff/barb/leases/heartbeat"
    ok = client.post(
        url, headers=_h("alpha"), json={"scope": "barb:pr-queue", "epoch": lease["epoch"], "ttl_sec": 3600}
    )
    assert ok.status_code == 200
    assert ok.json()["lease"]["expires_at"] > lease["expires_at"]
    stranger = client.post(url, headers=_h("beta"), json={"scope": "barb:pr-queue", "epoch": lease["epoch"]})
    assert stranger.status_code == 403
    assert (
        client.post(
            url, headers=_h("beta"), json={"scope": "barb:pr-queue", "epoch": 1, "holder": "agent-alpha"}
        ).status_code
        == 403
    )
    assert client.post(url, headers=_h("alpha"), json={"scope": "barb:pr-queue", "epoch": 99}).status_code == 409
    assert client.post(url, headers=_h("alpha"), json={"scope": "barb:none", "epoch": 1}).status_code == 409


def test_handoff_requires_expected_epoch(client: TestClient) -> None:
    r = client.post(
        "/api/v1/staff/barb/handoff",
        headers=_h("alpha"),
        json={"task_id": "t1", "action": "request_desktop", "actor": "a"},
    )
    assert r.status_code == 422
