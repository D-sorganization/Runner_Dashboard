"""API endpoint tests for Agent Profiles (/api/agent-profiles) (CR-3, Issue #1283)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from code_requests.profiles import AgentProfileStore  # noqa: E402
from routers import agent_profiles  # noqa: E402
from server import app  # noqa: E402


@pytest.fixture
def profile_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AgentProfileStore:
    p_path = tmp_path / "agent_profiles.json"
    store = AgentProfileStore(p_path)
    monkeypatch.setattr(agent_profiles, "_store", store)
    return store


@pytest.fixture
def client(mock_auth: object, profile_store: AgentProfileStore) -> TestClient:  # noqa: ARG001
    return TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})


def test_list_agent_profiles_with_provider_availability(client: TestClient) -> None:
    """GET /api/agent-profiles returns seeded profiles and provider availability map."""
    mock_avail = {
        "codex_cli": type("_A", (), {"available": True, "detail": "ready"})(),
        "claude_code_cli": type("_A", (), {"available": True, "detail": "ready"})(),
    }
    with patch("routers.agent_profiles.probe_provider_availability", return_value=mock_avail):
        resp = client.get("/api/agent-profiles")
    assert resp.status_code == 200
    data = resp.json()
    assert "profiles" in data
    assert "providers" in data
    assert len(data["profiles"]) >= 2
    prov_ids = {p["id"] for p in data["providers"]}
    assert "codex_cli" in prov_ids
    codex_info = next(p for p in data["providers"] if p["id"] == "codex_cli")
    assert codex_info["available"] is True


def test_get_agent_profile_by_id(client: TestClient) -> None:
    """GET /api/agent-profiles/{id} returns profile or 404."""
    resp = client.get("/api/agent-profiles/planner-strong")
    assert resp.status_code == 200
    assert resp.json()["id"] == "planner-strong"
    assert resp.json()["role"] == "planner"

    # Missing profile
    not_found = client.get("/api/agent-profiles/non-existent-profile")
    assert not_found.status_code == 404


def test_create_and_update_agent_profile(client: TestClient) -> None:
    """POST /api/agent-profiles creates profile, PUT updates it."""
    payload = {
        "id": "gemini-planner",
        "name": "Gemini Planner",
        "description": "Uses Gemini CLI for planning",
        "provider": "gemini_cli",
        "model": "gemini-2.5-pro",
        "role": "planner",
        "standards": ["tdd", "dbc"],
        "budget": {"max_cost": 1.0},
    }
    create_resp = client.post("/api/agent-profiles", json=payload)
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["id"] == "gemini-planner"
    assert created["provider"] == "gemini_cli"

    # Invalid payload (e.g. unknown provider)
    bad_resp = client.post("/api/agent-profiles", json={"name": "No provider", "role": "planner"})
    assert bad_resp.status_code == 422

    # PUT update
    update_payload = {"name": "Updated Gemini Planner", "model": "gemini-2.5-ultra"}
    put_resp = client.put("/api/agent-profiles/gemini-planner", json=update_payload)
    assert put_resp.status_code == 200
    updated = put_resp.json()
    assert updated["name"] == "Updated Gemini Planner"
    assert updated["model"] == "gemini-2.5-ultra"

    # PUT non-existent
    missing_put = client.put("/api/agent-profiles/unknown", json={"name": "test"})
    assert missing_put.status_code == 404


def test_delete_agent_profile(client: TestClient) -> None:
    """DELETE /api/agent-profiles/{id} deletes profile or returns 404."""
    # First create a profile to delete
    client.post(
        "/api/agent-profiles",
        json={"id": "to-delete", "name": "To Delete", "provider": "codex_cli", "role": "executor"},
    )
    del_resp = client.delete("/api/agent-profiles/to-delete")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    # Second delete returns 404
    del_again = client.delete("/api/agent-profiles/to-delete")
    assert del_again.status_code == 404
