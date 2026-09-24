"""API integration tests for staff audit log (SC-A8, Issue #1298)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import Principal, TokenRecord, identity_manager
from server import app
from staff.audit import get_audit_store, record_audit, reset_audit_store
from staff.store import reset_store

_REMOTE = ("100.64.0.15", 52000)


@pytest.fixture
def auth_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    """Configure identity directory with tokens for admin, operator, and viewer."""
    id_dir = tmp_path / "identity"
    id_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(id_dir))
    monkeypatch.setenv("RUNNER_DASHBOARD_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-peer-secret-12345")
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "roles"))

    reset_store()
    reset_audit_store()

    identity_manager.config_dir = id_dir
    identity_manager.principals_path = id_dir / "principals.yml"
    identity_manager.tokens_path = id_dir / "tokens.yml"

    principals = [
        Principal(id="admin-user", type="human", name="Admin", roles=["admin"]),
        Principal(id="operator-user", type="human", name="Operator", roles=["operator"]),
        Principal(id="viewer-user", type="human", name="Viewer", roles=["viewer"]),
    ]
    tokens = {
        "admin-token": "admin-user",
        "operator-token": "operator-user",
        "viewer-token": "viewer-user",
    }

    import time

    token_records = [
        TokenRecord(
            token_hash=hashlib.sha256(raw_tok.encode("utf-8")).hexdigest(),
            principal_id=p_id,
            name=f"test-{p_id}",
            created_at=time.time(),
        )
        for raw_tok, p_id in tokens.items()
    ]

    identity_manager.principals = {p.id: p for p in principals}
    identity_manager.tokens = token_records
    identity_manager.save_principals()
    identity_manager.save_tokens()

    yield {
        "admin": "admin-token",
        "operator": "operator-token",
        "viewer": "viewer-token",
    }

    reset_store()
    reset_audit_store()


@pytest.fixture
def remote_client(auth_env: dict[str, str]) -> Iterator[TestClient]:
    yield TestClient(app, raise_server_exceptions=False, client=_REMOTE)


_XHR = {"X-Requested-With": "XMLHttpRequest"}


def test_get_staff_audit_requires_auth(remote_client: TestClient) -> None:
    """GET /api/staff/audit without credentials returns 401."""
    resp = remote_client.get("/api/staff/audit", headers=_XHR)
    assert resp.status_code == 401


def test_get_staff_audit_requires_staff_audit_read_scope(remote_client: TestClient, auth_env: dict[str, str]) -> None:
    """A principal lacking staff.audit.read returns 403."""
    headers = {**_XHR, "Authorization": f"Bearer {auth_env['viewer']}"}
    resp = remote_client.get("/api/staff/audit", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["detail"]["required_scope"] == "staff.audit.read"


def test_get_staff_audit_allows_operator(remote_client: TestClient, auth_env: dict[str, str]) -> None:
    """Operator principal can read staff audit log."""
    record_audit(
        action="dispatch",
        target="role:night-watch",
        principal="op-user",
        surface="ui",
        detail={"repo": "Tools"},
    )

    headers = {**_XHR, "Authorization": f"Bearer {auth_env['operator']}"}
    resp = remote_client.get("/api/staff/audit", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "entries" in data
    assert data["count"] == 1
    assert data["total"] == 1
    assert data["entries"][0]["action"] == "dispatch"
    assert data["entries"][0]["target"] == "role:night-watch"


def test_get_staff_audit_filtering_and_formats(remote_client: TestClient, auth_env: dict[str, str]) -> None:
    """GET /api/staff/audit supports filters, CSV, and NDJSON exports."""
    record_audit(
        action="dispatch",
        principal="alice",
        thread_id="th-99",
        surface="mcp",
        target="role:t1",
    )
    record_audit(
        action="cancel",
        principal="bob",
        thread_id="th-100",
        surface="api",
        target="run:r1",
    )

    headers = {**_XHR, "Authorization": f"Bearer {auth_env['operator']}"}

    # Filter by principal
    r_filter = remote_client.get("/api/staff/audit?principal=alice", headers=headers)
    assert r_filter.status_code == 200
    assert r_filter.json()["count"] == 1
    assert r_filter.json()["entries"][0]["principal"] == "alice"

    # Export CSV
    r_csv = remote_client.get("/api/staff/audit?format=csv", headers=headers)
    assert r_csv.status_code == 200
    assert "text/csv" in r_csv.headers["content-type"]
    assert "action" in r_csv.text
    assert "alice" in r_csv.text

    # Export NDJSON
    r_ndjson = remote_client.get("/api/staff/audit?format=ndjson", headers=headers)
    assert r_ndjson.status_code == 200
    assert "application/x-ndjson" in r_ndjson.headers["content-type"]
    lines = [json.loads(line) for line in r_ndjson.text.strip().splitlines()]
    assert len(lines) == 2


def test_write_points_generate_audit_records(remote_client: TestClient, auth_env: dict[str, str]) -> None:
    """Mutating actions (cancel, holds, schedule toggle) record audit rows."""
    headers = {**_XHR, "Authorization": f"Bearer {auth_env['operator']}"}

    # 1. Holds write point
    holds_resp = remote_client.put(
        "/api/staff/holds",
        json={"holds": [{"text": "Safety hold", "active": True}]},
        headers=headers,
    )
    assert holds_resp.status_code == 200

    # Verify hold_set audit entry exists
    store = get_audit_store()
    hold_entries = store.list_entries(action="hold_set")
    assert len(hold_entries) == 1
    assert hold_entries[0].target == "holds"

    # 2. Schedule toggle write point
    toggle_resp = remote_client.post(
        "/api/staff/schedule/toggle",
        json={"enabled": False},
        headers=headers,
    )
    assert toggle_resp.status_code == 200

    toggle_entries = store.list_entries(action="schedule_toggle")
    assert len(toggle_entries) == 1
    assert toggle_entries[0].target == "scheduler"
    assert toggle_entries[0].detail.get("enabled") is False
