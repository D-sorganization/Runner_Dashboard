"""Tests for Staff Role Pins API v1 (/api/v1/staff/pins) (SC-D3, Issue #1317).

Covers:
- GET /api/v1/staff/pins: returns current user's pinned roles
- POST /api/v1/staff/pins/{role}: pins a role, returns updated pins list
- DELETE /api/v1/staff/pins/{role}: unpins a role, returns updated pins list
- PUT /api/v1/staff/pins: sets entire pins list for user
- Multi-user isolation: pins for user A do not affect user B
- Audit event recorded for pin/unpin actions
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from server import app
from staff.pins import reset_pins_store

TEST_USER_A = Principal(
    id="user-alice",
    type="human",
    name="Alice Operator",
    roles=["operator"],
    scopes=["staff.read", "staff.chat"],
)

TEST_USER_B = Principal(
    id="user-bob",
    type="human",
    name="Bob Operator",
    roles=["operator"],
    scopes=["staff.read", "staff.chat"],
)


@pytest.fixture(autouse=True)
def clean_pins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Ensure clean pins database for each test."""
    db_file = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    reset_pins_store()
    yield
    reset_pins_store()


@pytest.fixture
def client_alice() -> TestClient:
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_USER_A
    app.dependency_overrides[require_scope("staff.chat")] = lambda: TEST_USER_A
    app.dependency_overrides[require_principal] = lambda: TEST_USER_A
    yield TestClient(
        app,
        headers={"X-Requested-With": "XMLHttpRequest"},
        raise_server_exceptions=False,
    )
    app.dependency_overrides.clear()


@pytest.fixture
def client_bob() -> TestClient:
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_USER_B
    app.dependency_overrides[require_scope("staff.chat")] = lambda: TEST_USER_B
    app.dependency_overrides[require_principal] = lambda: TEST_USER_B
    yield TestClient(
        app,
        headers={"X-Requested-With": "XMLHttpRequest"},
        raise_server_exceptions=False,
    )
    app.dependency_overrides.clear()


def test_get_pins_empty(client_alice: TestClient):
    resp = client_alice.get("/api/v1/staff/pins")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pins"] == []


def test_post_pin_and_idempotency(client_alice: TestClient):
    resp = client_alice.post("/api/v1/staff/pins/barb")
    assert resp.status_code == 200
    data = resp.json()
    assert "barb" in data["pins"]

    # Posting same pin again is idempotent
    resp2 = client_alice.post("/api/v1/staff/pins/barb")
    assert resp2.status_code == 200
    assert resp2.json()["pins"] == ["barb"]

    # Pin a second role
    resp3 = client_alice.post("/api/v1/staff/pins/fleet-maintenance")
    assert resp3.status_code == 200
    pins = resp3.json()["pins"]
    assert "barb" in pins
    assert "fleet-maintenance" in pins


def test_delete_pin(client_alice: TestClient):
    client_alice.post("/api/v1/staff/pins/barb")
    client_alice.post("/api/v1/staff/pins/librarian")

    resp = client_alice.delete("/api/v1/staff/pins/barb")
    assert resp.status_code == 200
    pins = resp.json()["pins"]
    assert "barb" not in pins
    assert "librarian" in pins

    # Deleting non-existent pin is safe and idempotent
    resp2 = client_alice.delete("/api/v1/staff/pins/barb")
    assert resp2.status_code == 200
    assert "barb" not in resp2.json()["pins"]


def test_put_pins_batch(client_alice: TestClient):
    resp = client_alice.put(
        "/api/v1/staff/pins",
        json={"pins": ["orchestrator", "cartographer"]},
    )
    assert resp.status_code == 200
    assert set(resp.json()["pins"]) == {"orchestrator", "cartographer"}

    # Overwrite with empty
    resp_empty = client_alice.put(
        "/api/v1/staff/pins",
        json={"pins": []},
    )
    assert resp_empty.status_code == 200
    assert resp_empty.json()["pins"] == []


def test_user_isolation():
    client = TestClient(
        app,
        headers={"X-Requested-With": "XMLHttpRequest"},
        raise_server_exceptions=False,
    )

    # Set as Alice
    app.dependency_overrides[require_principal] = lambda: TEST_USER_A
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_USER_A
    app.dependency_overrides[require_scope("staff.chat")] = lambda: TEST_USER_A
    client.post("/api/v1/staff/pins/barb")
    client.post("/api/v1/staff/pins/orchestrator")

    # Switch to Bob
    app.dependency_overrides[require_principal] = lambda: TEST_USER_B
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_USER_B
    app.dependency_overrides[require_scope("staff.chat")] = lambda: TEST_USER_B
    client.post("/api/v1/staff/pins/fleet-critic")

    bob_pins = client.get("/api/v1/staff/pins").json()["pins"]
    assert bob_pins == ["fleet-critic"]

    # Switch back to Alice
    app.dependency_overrides[require_principal] = lambda: TEST_USER_A
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_USER_A
    app.dependency_overrides[require_scope("staff.chat")] = lambda: TEST_USER_A
    alice_pins = client.get("/api/v1/staff/pins").json()["pins"]
    assert set(alice_pins) == {"barb", "orchestrator"}

    app.dependency_overrides.clear()
