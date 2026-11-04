"""API tests for the TTL path/issue lease endpoints (#3931 S1).

Clean-room: built from public docs/behavior descriptions only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("DASHBOARD_PATH_LEASE_DB_PATH", str(tmp_path / "path_leases.sqlite3"))
    from fastapi.testclient import TestClient
    from identity import require_fleet_peer, require_scope
    from server import app

    write_dep = require_scope("coordination.write")
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    app.dependency_overrides[write_dep] = lambda: "test-writer"
    try:
        yield TestClient(app, raise_server_exceptions=False, headers={"X-Requested-With": "XMLHttpRequest"})
    finally:
        app.dependency_overrides.pop(require_fleet_peer, None)
        app.dependency_overrides.pop(write_dep, None)


def _acquire(client: Any, scope: str = "issue:1", owner: str = "claude-a", ttl: float = 3600) -> dict[str, Any]:
    resp = client.post(
        "/api/v1/path-leases/acquire",
        json={"repo": "Runner_Dashboard", "scope": scope, "owner": owner, "ttl_seconds": ttl},
    )
    assert resp.status_code == 200
    return resp.json()


def test_check_on_empty_store_is_not_held(client: Any) -> None:
    resp = client.get("/api/v1/path-leases/check", params={"repo": "Runner_Dashboard", "scope": "issue:1"})
    assert resp.status_code == 200
    assert resp.json() == {"held": False, "lease": None}


def test_acquire_then_check_reports_held(client: Any) -> None:
    lease = _acquire(client)
    check = client.get("/api/v1/path-leases/check", params={"repo": "Runner_Dashboard", "scope": "issue:1"})
    assert check.json() == {"held": True, "lease": lease}


def test_acquire_conflict_returns_409(client: Any) -> None:
    _acquire(client, owner="claude-a")
    conflict = client.post(
        "/api/v1/path-leases/acquire",
        json={"repo": "Runner_Dashboard", "scope": "issue:1", "owner": "codex-g", "ttl_seconds": 3600},
    )
    assert conflict.status_code == 409


def test_renew_extends_ttl_but_rejects_non_owner(client: Any) -> None:
    lease = _acquire(client, ttl=60)
    renewed = client.post(
        "/api/v1/path-leases/renew", json={"lease_id": lease["lease_id"], "owner": "claude-a", "ttl_seconds": 3600}
    )
    assert renewed.status_code == 200
    assert renewed.json()["expires_at"] > lease["expires_at"]

    rejected = client.post(
        "/api/v1/path-leases/renew", json={"lease_id": lease["lease_id"], "owner": "codex-g", "ttl_seconds": 3600}
    )
    assert rejected.status_code == 409


def test_release_frees_the_scope_but_rejects_non_owner(client: Any) -> None:
    lease = _acquire(client)
    rejected = client.post("/api/v1/path-leases/release", json={"lease_id": lease["lease_id"], "owner": "codex-g"})
    assert rejected.status_code == 409

    released = client.post("/api/v1/path-leases/release", json={"lease_id": lease["lease_id"], "owner": "claude-a"})
    assert released.status_code == 204
    check = client.get("/api/v1/path-leases/check", params={"repo": "Runner_Dashboard", "scope": "issue:1"})
    assert check.json() == {"held": False, "lease": None}


def test_renew_unknown_lease_is_409(client: Any) -> None:
    resp = client.post(
        "/api/v1/path-leases/renew", json={"lease_id": "not-a-real-id", "owner": "claude-a", "ttl_seconds": 3600}
    )
    assert resp.status_code == 409
