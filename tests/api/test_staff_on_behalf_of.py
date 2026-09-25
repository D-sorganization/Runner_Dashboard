"""Tests for forwarded staff run caller identity preservation (SC-F2, Issue #1311).

When the hub forwards a staff run to a peer node, the original caller's identity
is preserved via a signed X-Staff-On-Behalf-Of header.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal
from staff import adapters as adapters_mod
from staff import audit as audit_mod
from staff import fleet as fleet_mod
from staff import runner as runner_mod
from staff import store as store_mod

_XHR = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def tmp_audit_db(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> store_mod.RunStore:
    db_file = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    return store_mod.RunStore(db_file)


@pytest.fixture
def client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch, tmp_audit_db: store_mod.RunStore) -> TestClient:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "test-fleet-secret-key")
    from routers import staff as staff_router

    store = tmp_audit_db
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir(parents=True, exist_ok=True)
    (roles_dir / "ad-hoc.yml").write_text("name: ad-hoc\nstrategy:\n  provider: claude\n", encoding="utf-8")
    adapters = dict(adapters_mod.ADAPTERS)
    adapters["claude"] = adapters_mod.ProviderAdapter(
        provider_id="claude",
        label="Claude",
        executable="python",
        argv=("-c", "pass"),
    )
    monkeypatch.setenv("STAFF_ROLES_DIR", str(roles_dir))
    runner = runner_mod.StaffRunner(
        machine="Desk",
        store=store,
        adapters=adapters,
    )
    monkeypatch.setattr(runner_mod, "_runner", runner)
    app = FastAPI()
    app.include_router(staff_router.router)
    app.dependency_overrides[staff_router.get_runner] = lambda: runner
    app.dependency_overrides[staff_router.get_audit_store] = lambda: audit_mod.StaffAuditStore(store.path)
    return TestClient(app)


# ── 1. Pure unit tests for signing & verification ────────────────────────────


@pytest.mark.unit
def test_sign_and_verify_on_behalf_of_roundtrip() -> None:
    secret = "secret-123"
    header = fleet_mod.sign_on_behalf_of(
        principal="user:dieter",
        surface="thread",
        thread_id="thr-123",
        request_id="req-abc",
        secret=secret,
    )
    assert "." in header

    verified = fleet_mod.verify_on_behalf_of(header, secret=secret)
    assert verified is not None
    assert verified["principal"] == "user:dieter"
    assert verified["surface"] == "thread"
    assert verified["thread_id"] == "thr-123"
    assert verified["request_id"] == "req-abc"
    assert "iat" in verified


@pytest.mark.unit
def test_verify_on_behalf_of_tampered_signature_rejected() -> None:
    secret = "secret-123"
    header = fleet_mod.sign_on_behalf_of(
        principal="user:dieter",
        surface="thread",
        thread_id="thr-123",
        secret=secret,
    )
    payload_b64, _ = header.split(".", 1)
    tampered = f"{payload_b64}.deadbeefbadcafe"
    assert fleet_mod.verify_on_behalf_of(tampered, secret=secret) is None


@pytest.mark.unit
def test_verify_on_behalf_of_tampered_payload_rejected() -> None:
    secret = "secret-123"
    header = fleet_mod.sign_on_behalf_of(
        principal="user:dieter",
        surface="thread",
        secret=secret,
    )
    _, sig = header.split(".", 1)
    forged_payload = base64.urlsafe_b64encode(json.dumps({"principal": "user:root"}).encode()).decode()
    tampered = f"{forged_payload}.{sig}"
    assert fleet_mod.verify_on_behalf_of(tampered, secret=secret) is None


@pytest.mark.unit
def test_verify_on_behalf_of_expired_rejected() -> None:
    secret = "secret-123"
    old_time = int(time.time()) - 400  # 400 seconds ago (> default 300s TTL)
    payload = {
        "principal": "user:dieter",
        "surface": "thread",
        "thread_id": "thr-123",
        "request_id": "req-123",
        "iat": old_time,
    }
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).decode()
    import hashlib
    import hmac

    sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    header = f"{payload_b64}.{sig}"
    assert fleet_mod.verify_on_behalf_of(header, secret=secret, ttl_seconds=300) is None


@pytest.mark.unit
def test_verify_on_behalf_of_missing_principal_rejected() -> None:
    secret = "secret-123"
    payload = {"principal": "", "surface": "thread", "iat": int(time.time())}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).decode()
    import hashlib
    import hmac

    sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    header = f"{payload_b64}.{sig}"
    assert fleet_mod.verify_on_behalf_of(header, secret=secret) is None


# ── 2. Forwarding caller identity from hub to peer ───────────────────────────


@pytest.mark.unit
def test_forward_run_includes_signed_on_behalf_of_header(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from routers import staff as staff_router

    monkeypatch.setattr(fleet_mod, "peer_nodes", lambda: {"OGLaptop": "http://og:8321"})
    monkeypatch.setenv("HUB_FLEET_TOKEN", "test-fleet-token")

    seen: dict[str, Any] = {}

    async def fake_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        seen["url"] = url
        seen["body"] = body
        seen["headers"] = headers
        return 200, {"dry_run": False, "run": {"id": "peer-run-1"}, "machine": "OGLaptop"}

    monkeypatch.setattr(fleet_mod, "post_json", fake_post)

    # Dispatched by user:dieter
    client.app.dependency_overrides[staff_router.require_scope("staff.dispatch")] = lambda: Principal(
        id="user:dieter", type="user", name="Dieter", roles=["operator"]
    )

    resp = client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "test forward", "machine": "oglaptop", "thread_id": "thread-99", "surface": "thread"},
        headers=_XHR,
    )
    assert resp.status_code == 200, resp.text
    assert "X-Staff-On-Behalf-Of" in seen["headers"]

    obo_header = seen["headers"]["X-Staff-On-Behalf-Of"]
    verified = fleet_mod.verify_on_behalf_of(obo_header, secret="test-fleet-token")
    assert verified is not None
    assert verified["principal"] == "user:dieter"
    assert verified["thread_id"] == "thread-99"
    assert verified["surface"] == "thread"


# ── 3. Peer receiving signed header ──────────────────────────────────────────


@pytest.mark.unit
def test_peer_dispatch_with_valid_on_behalf_of_records_caller_and_audit(
    client: TestClient, tmp_audit_db: store_mod.RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from routers import staff as staff_router

    secret = "test-fleet-secret-key"
    monkeypatch.setenv("HUB_FLEET_TOKEN", secret)

    obo_header = fleet_mod.sign_on_behalf_of(
        principal="user:alice",
        surface="thread",
        thread_id="thr-777",
        request_id="req-abc",
        secret=secret,
    )

    # Caller is authenticated peer
    client.app.dependency_overrides[staff_router.require_scope("staff.dispatch")] = lambda: Principal(
        id="fleet-peer", type="bot", name="Fleet Peer", roles=["fleet-peer"]
    )

    resp = client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "peer task", "machine": "local"},
        headers={**_XHR, "X-Staff-On-Behalf-Of": obo_header},
    )
    assert resp.status_code == 200, resp.text
    run_data = resp.json()["run"]

    # Run record preserves original caller
    assert run_data["requested_by"] == "user:alice"
    assert run_data["on_behalf_of"] == "user:alice"

    # Audit log records peer as principal, user:alice as on_behalf_of, and thread context
    audit_store = audit_mod.StaffAuditStore(tmp_audit_db.path)
    entries = audit_store.list_entries(limit=10)
    assert len(entries) >= 1
    dispatch_entry = next(e for e in entries if e.action == "dispatch")
    assert dispatch_entry.principal == "fleet-peer"
    assert dispatch_entry.on_behalf_of == "user:alice"
    assert dispatch_entry.surface == "thread"
    assert dispatch_entry.thread_id == "thr-777"


@pytest.mark.unit
def test_peer_dispatch_with_tampered_header_falls_back_to_fleet_peer(
    client: TestClient, tmp_audit_db: store_mod.RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from routers import staff as staff_router

    secret = "test-fleet-secret-key"
    monkeypatch.setenv("HUB_FLEET_TOKEN", secret)

    # Tampered header
    tampered_header = "eyJhbGciOiJIUzI1NiJ9.invalid.signature"

    client.app.dependency_overrides[staff_router.require_scope("staff.dispatch")] = lambda: Principal(
        id="fleet-peer", type="bot", name="Fleet Peer", roles=["fleet-peer"]
    )

    resp = client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "peer task", "machine": "local"},
        headers={**_XHR, "X-Staff-On-Behalf-Of": tampered_header},
    )
    assert resp.status_code == 200, resp.text
    run_data = resp.json()["run"]

    # Falls back to fleet-peer
    assert run_data["requested_by"] == "fleet-peer"
    assert run_data.get("on_behalf_of", "") == ""

    # Audit log reflects fleet-peer
    audit_store = audit_mod.StaffAuditStore(tmp_audit_db.path)
    entries = audit_store.list_entries(limit=10)
    dispatch_entry = next(e for e in entries if e.action == "dispatch")
    assert dispatch_entry.principal == "fleet-peer"
    assert dispatch_entry.on_behalf_of == ""


@pytest.mark.unit
def test_non_peer_cannot_spoof_on_behalf_of(
    client: TestClient, tmp_audit_db: store_mod.RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from routers import staff as staff_router

    secret = "test-fleet-secret-key"
    monkeypatch.setenv("HUB_FLEET_TOKEN", secret)

    obo_header = fleet_mod.sign_on_behalf_of(
        principal="user:admin",
        surface="thread",
        secret=secret,
    )

    # Normal user caller (not fleet-peer)
    client.app.dependency_overrides[staff_router.require_scope("staff.dispatch")] = lambda: Principal(
        id="user:bob", type="user", name="Bob", roles=["operator"]
    )

    resp = client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "bob task", "machine": "local"},
        headers={**_XHR, "X-Staff-On-Behalf-Of": obo_header},
    )
    assert resp.status_code == 200, resp.text
    run_data = resp.json()["run"]

    # Must NOT adopt user:admin
    assert run_data["requested_by"] == "user:bob"
    assert run_data.get("on_behalf_of", "") == ""
