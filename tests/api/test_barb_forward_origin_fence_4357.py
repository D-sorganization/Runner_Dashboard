"""Forwarded Barb-fenced dispatches and load-aware ``machine: auto`` (#4357)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from identity import Principal
from staff import fleet as fleet_mod
from staff import runner as runner_mod
from staff.barb_continuity import BarbContinuityStore
from staff.barb_lease_fence import (
    BarbClaim,
    EPOCH_HEADER,
    HOLDER_HEADER,
    SCOPE_HEADER,
    barb_write_fence,
)
from staff.barb_origin_fence import (
    ORIGIN_FENCE_HEADER,
    apply_origin_fence_trust,
    sign_barb_origin_fence,
)
from staff.dispatch_auth import require_staff_run_dispatch

from tests.api.test_staff_fleet import peers, staff  # noqa: F401,F811
from tests.api.test_staff_on_behalf_of import _XHR

SCOPE = "barb:dispatch-coordinator"


def _claim(epoch: int) -> BarbClaim:
    return BarbClaim(scope=SCOPE, epoch=epoch, caller_id="agent-grok", holder="agent-grok")


@pytest.fixture
def origin_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BarbContinuityStore:
    from staff import barb_continuity  # noqa: PLC0415

    s = BarbContinuityStore(tmp_path / "origin-barb.sqlite3")
    monkeypatch.setattr(barb_continuity, "_DEFAULT_STORE", s)
    return s


@pytest.fixture
def peer_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BarbContinuityStore:
    from staff import barb_continuity  # noqa: PLC0415

    s = BarbContinuityStore(tmp_path / "peer-barb.sqlite3")
    monkeypatch.setattr(barb_continuity, "_DEFAULT_STORE", s)
    return s


@pytest.fixture
def peer_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    staff: runner_mod.StaffRunner,  # noqa: F811
    peer_store: BarbContinuityStore,
) -> TestClient:
    from routers import staff as staff_router  # noqa: PLC0415

    token_key = "test-fleet-token-4357"  # pragma: allowlist secret
    monkeypatch.setenv("HUB_FLEET_TOKEN", token_key)
    monkeypatch.setattr(staff, "launch", lambda rec, plan: None)
    app = FastAPI()
    app.include_router(staff_router.router)
    app.dependency_overrides[require_staff_run_dispatch()] = lambda: Principal(
        id="fleet-peer", type="bot", name="Fleet Peer", roles=["fleet-peer"]
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.unit
def test_origin_fence_trust_skips_local_store_fence(
    origin_store: BarbContinuityStore,
    peer_store: BarbContinuityStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "unit-test-origin-fence")  # pragma: allowlist secret
    origin_store.checkout_lease(scope=SCOPE, holder="agent-grok")
    claim = _claim(1)
    header = sign_barb_origin_fence(claim, origin_node="Desk")
    trusted = apply_origin_fence_trust(claim, header, trusted_forwarder=True)
    assert trusted is not None and trusted.lease_validated_at_origin
    with barb_write_fence(trusted, surface="t", target="t", principal="p", store=peer_store):
        pass


@pytest.mark.unit
def test_non_forwarder_cannot_use_origin_fence_attestation(
    peer_store: BarbContinuityStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "unit-test-origin-fence")  # pragma: allowlist secret
    claim = _claim(99)
    header = sign_barb_origin_fence(claim, origin_node="Desk")
    untrusted = apply_origin_fence_trust(claim, header, trusted_forwarder=False)
    assert untrusted is not None and not untrusted.lease_validated_at_origin
    with pytest.raises(HTTPException) as err:
        with barb_write_fence(untrusted, surface="t", target="t", principal="p", store=peer_store):
            pass
    assert err.value.status_code == 409


@pytest.mark.unit
async def test_forward_run_attaches_signed_origin_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    token_key = "test-fleet-token-4357"  # pragma: allowlist secret
    monkeypatch.setenv("HUB_FLEET_TOKEN", token_key)
    seen: dict[str, str] = {}

    async def fake_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        seen.update(headers)
        return 200, {"dry_run": False, "run": {"id": "r1"}}

    monkeypatch.setattr(fleet_mod, "post_json", fake_post)
    claim = _claim(3)
    await fleet_mod.forward_run(
        "http://peer:8321",
        "ad-hoc",
        {"prompt": "hi"},
        on_behalf_of="signed",
        barb_claim=claim,
        origin_node="Desk",
    )
    assert ORIGIN_FENCE_HEADER in seen or "X-Barb-Origin-Fence" in seen
    fence_val = seen.get(ORIGIN_FENCE_HEADER) or seen.get("X-Barb-Origin-Fence")
    assert fence_val
    assert apply_origin_fence_trust(claim, fence_val, trusted_forwarder=True) is not None


@pytest.mark.unit
def test_peer_accepts_forwarded_dispatch_with_valid_origin_fence(
    peer_client: TestClient,
    origin_store: BarbContinuityStore,
    peer_store: BarbContinuityStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = origin_store.checkout_lease(scope=SCOPE, holder="agent-grok")["lease"]
    claim = _claim(lease["epoch"])
    token_key = "test-fleet-token-4357"  # pragma: allowlist secret
    obo = fleet_mod.sign_on_behalf_of("agent-grok", surface="api", origin_node="Desk", secret=token_key)
    fence = sign_barb_origin_fence(claim, origin_node="Desk")
    resp = peer_client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "ship", "machine": "local", "dry_run": False},
        headers={
            **_XHR,
            "Authorization": f"Bearer {token_key}",
            "X-Staff-On-Behalf-Of": obo,
            SCOPE_HEADER: SCOPE,
            EPOCH_HEADER: str(lease["epoch"]),
            HOLDER_HEADER: "agent-grok",
            ORIGIN_FENCE_HEADER: fence,
        },
    )
    assert resp.status_code == 200, resp.text
    assert peer_store._conn.execute("SELECT COUNT(*) AS c FROM barb_scoped_leases").fetchone()["c"] == 0


@pytest.mark.unit
def test_peer_direct_call_without_lease_still_409(peer_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BARB_FENCE_MODE", "enforce")
    resp = peer_client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "hi", "machine": "local"},
        headers={
            **_XHR,
            "Authorization": "Bearer test-fleet-token-4357",
            SCOPE_HEADER: SCOPE,
            EPOCH_HEADER: "1",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "stale_barb_lease"


@pytest.mark.unit
def test_operator_cannot_spoof_origin_fence_on_peer(peer_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from routers import staff as staff_router  # noqa: PLC0415

    token_key = "test-fleet-token-4357"  # pragma: allowlist secret
    claim = _claim(2)
    fence = sign_barb_origin_fence(claim, origin_node="Desk")
    peer_client.app.dependency_overrides[require_staff_run_dispatch()] = lambda: Principal(
        id="user:bob", type="user", name="Bob", roles=["operator"], scopes=["staff.dispatch"]
    )
    peer_client.app.dependency_overrides[staff_router.get_runner] = peer_client.app.dependency_overrides.get(
        staff_router.get_runner
    )
    resp = peer_client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "hi", "machine": "local"},
        headers={
            **_XHR,
            SCOPE_HEADER: SCOPE,
            EPOCH_HEADER: "2",
            ORIGIN_FENCE_HEADER: fence,
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "stale_barb_lease"


@pytest.mark.unit
def test_choose_machine_prefers_idle_peer_over_busy_local() -> None:
    board = {
        "machines": {
            "origin-host": {
                "status": "online",
                "providers": {"claude": True},
                "running": [{"id": "r1"}],
                "queued": [],
                "dispatch_resources": {"cpu_percent": 90.0, "mem_percent": 80.0},
            },
            "peer-a": {
                "status": "online",
                "providers": {"claude": True},
                "running": [],
                "queued": [],
                "dispatch_resources": {"cpu_percent": 5.0, "mem_percent": 20.0},
            },
            "peer-b": {
                "status": "online",
                "providers": {"claude": True},
                "running": [],
                "queued": [],
                "dispatch_resources": {"cpu_percent": 8.0, "mem_percent": 25.0},
            },
        }
    }
    pick = fleet_mod.choose_machine(board, "claude", "origin-host")
    assert pick == "peer-a"
