"""Barb lease renew, handoff, TTL caps, and sliding auto-extend on fenced writes."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from identity import Principal, identity_manager

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.40", 52040)
SCOPE = "barb:front-door"
pytestmark = pytest.mark.integration

_TOKENS = {
    "alpha": Principal(id="openbot-barb", type="bot", name="OpenBot", roles=["bot", "barb"]),
    "beta": Principal(id="agent-beta", type="bot", name="Beta", roles=["bot"]),
    "dots": Principal(id="agent-codex-hands", type="bot", name="Dots Barb", roles=["bot", "barb"]),
    "oper": Principal(id="op-1", type="human", name="Operator", roles=["operator"], scopes=["staff.admin"]),
}


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from staff import barb_continuity  # noqa: PLC0415

    s = barb_continuity.BarbContinuityStore(tmp_path / "barb.sqlite3")
    monkeypatch.setattr(barb_continuity, "_DEFAULT_STORE", s)
    return s


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: Any) -> Iterator[TestClient]:
    import orchestration_audit  # noqa: PLC0415
    from memory.store import reset_memory_store  # noqa: PLC0415

    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("ASSISTANT_MEMORY_DB", str(tmp_path / "memory.sqlite3"))
    monkeypatch.setattr(orchestration_audit, "ORCHESTRATION_AUDIT_PATH", tmp_path / "audit.ndjson")
    reset_memory_store()
    for name, prin in _TOKENS.items():
        identity_manager.add_ephemeral_token(hashlib.sha256(f"tok-{name}".encode()).hexdigest(), prin, name=name)
    from server import app  # noqa: PLC0415

    yield TestClient(app, raise_server_exceptions=False, client=_REMOTE)
    reset_memory_store()


def _h(name: str, scope: str | None = None, epoch: int | None = None) -> dict[str, str]:
    hdr = {**_XHR, "Authorization": f"Bearer tok-{name}"}
    if scope is not None:
        hdr["X-Barb-Scope"] = scope
    if epoch is not None:
        hdr["X-Barb-Lease-Epoch"] = str(epoch)
    return hdr


def _renew(client: TestClient, who: str, **body: Any) -> Any:
    return client.post("/api/v1/staff/barb/leases/renew", headers=_h(who), json={"scope": SCOPE, **body})


def test_list_leases_include_expired_alias(client: TestClient, store: Any) -> None:
    past = datetime.now(UTC) - timedelta(hours=1)
    store.checkout_lease(scope=SCOPE, holder="openbot-barb", ttl_seconds=30, now=past)
    live = client.get("/api/v1/staff/barb/leases", headers=_h("alpha"))
    assert live.json()["count"] == 0
    all_rows = client.get("/api/v1/staff/barb/leases", headers=_h("alpha"), params={"include_expired": True})
    assert all_rows.json()["count"] == 1


def test_renew_live_same_holder(client: TestClient) -> None:
    first = _renew(client, "alpha", ttl_sec=7200)
    assert first.status_code == 200, first.text
    assert first.json()["reacquired"] is True
    exp = first.json()["lease"]["expires_at"]
    again = _renew(client, "alpha", ttl_sec=14400)
    assert again.status_code == 200
    assert again.json()["renewed"] is True
    assert again.json()["reacquired"] is False
    assert again.json()["lease"]["expires_at"] >= exp


def test_renew_same_principal_new_suffix_bumps_epoch(client: TestClient) -> None:
    lease = _renew(client, "alpha", holder="openbot-barb:seat2").json()["lease"]
    assert lease["epoch"] == 1
    renewed = _renew(client, "alpha", holder="openbot-barb:laptop")
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["lease"]["holder"] == "openbot-barb:laptop"
    assert renewed.json()["lease"]["epoch"] == 2


def test_renew_expired_reacquire_preserves_turnover(client: TestClient, store: Any) -> None:
    past = datetime.now(UTC) - timedelta(hours=2)
    store.checkout_lease(scope=SCOPE, holder="openbot-barb", ttl_seconds=60, now=past)
    store.append_turnover(scope=SCOPE, author="openbot-barb", summary="earlier shift")
    snap = store.list_leases(include_expired=True)[0]
    assert snap["last_turnover_id"]
    got = _renew(client, "alpha")
    assert got.status_code == 200
    assert got.json()["reacquired"] is True
    assert got.json()["lease"]["last_turnover_id"] == snap["last_turnover_id"]


def test_renew_live_other_principal_409(client: TestClient, store: Any) -> None:
    store.checkout_lease(scope=SCOPE, holder="openbot-barb")
    blocked = store.renew_lease(scope=SCOPE, caller_id="agent-beta", holder="agent-beta")
    assert blocked["renewed"] is False
    assert blocked["reason"] == "held_by_other"
    api = _renew(client, "beta")
    assert api.status_code == 409


def test_renew_cross_principal_holder_spoof_403(client: TestClient) -> None:
    _renew(client, "alpha")
    spoof = _renew(client, "beta", holder="openbot-barb")
    assert spoof.status_code == 403


def test_auto_extend_on_fenced_turnover(client: TestClient, store: Any) -> None:
    now = datetime.now(UTC)
    lease = store.checkout_lease(
        scope=SCOPE,
        holder="openbot-barb",
        ttl_seconds=3600,
        now=now - timedelta(seconds=3500),
    )["lease"]
    assert lease["epoch"] == 1
    before = store.list_leases()[0]["expires_at"]
    ok = client.post(
        "/api/v1/staff/barb/turnover",
        headers=_h("alpha", SCOPE, lease["epoch"]),
        json={"scope": SCOPE, "summary": "mid-shift", "next_actions": ["watch queue"]},
    )
    assert ok.status_code == 200, ok.text
    after = store.list_leases()[0]["expires_at"]
    assert after > before


def test_handoff_push_release(client: TestClient) -> None:
    lease = _renew(client, "alpha").json()["lease"]
    res = client.post(
        "/api/v1/staff/barb/leases/handoff",
        headers=_h("alpha"),
        json={"scope": SCOPE, "summary": "end of shift", "next_actions": ["reconcile"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode"] == "push_release"
    assert body["turnover"]["summary"].startswith("BARB-HANDOFF")
    snap = client.get("/api/v1/staff/barb/leases", headers=_h("alpha"), params={"include_expired": True})
    row = snap.json()["leases"][0]
    assert row["holder"] == ""
    assert row["released_at"]
    assert row["epoch"] == lease["epoch"]


def test_handoff_push_transfer(client: TestClient) -> None:
    _renew(client, "alpha")
    res = client.post(
        "/api/v1/staff/barb/leases/handoff",
        headers=_h("alpha"),
        json={
            "scope": SCOPE,
            "summary": "passing seat",
            "next_holder": "agent-codex-hands",
            "next_persona": "codex-barb",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode"] == "push_transfer"
    assert body["lease"]["holder"] == "agent-codex-hands"
    assert body["lease"]["epoch"] >= 2


def test_handoff_pull_takeover(client: TestClient, tmp_path: Path) -> None:
    _renew(client, "alpha")
    res = client.post(
        "/api/v1/staff/barb/leases/handoff",
        headers=_h("dots"),
        json={"scope": SCOPE, "summary": "contested handoff", "takeover": True, "reason": "alpha wedged"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["mode"] == "pull_takeover"
    assert res.json()["lease"]["holder"] == "agent-codex-hands"
    audit = (tmp_path / "audit.ndjson").read_text().strip().splitlines()
    assert "barb.lease.handoff_takeover" in audit[-1]


def test_barb_ops_registers_new_commands() -> None:
    import argparse  # noqa: PLC0415

    from clients.fleet import barb_ops  # noqa: PLC0415

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    barb_ops.register_ops_commands(sub)
    sub_action = next(a for a in parser._actions if a.choices)
    assert {"heartbeat", "renew", "handoff-lease"}.issubset(set(sub_action.choices))
