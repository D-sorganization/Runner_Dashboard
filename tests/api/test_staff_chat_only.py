"""Tests for chat-only provider reporting on roster, board, and providers endpoints (#1697)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from routers import staff as staff_router
from routers import staff_v1 as staff_v1_router
from staff.adapters import chat_only_providers, unattended_providers


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    app.include_router(staff_router.router)
    app.include_router(staff_v1_router.router)

    # Provide staff.read scope for testing
    test_principal = Principal(id="tester", type="human", name="Tester", roles=["operator"], scopes=["staff.read"])
    app.dependency_overrides[require_scope("staff.read")] = lambda: test_principal

    # Mock runner
    mock_runner = MagicMock()
    mock_runner.machine = "DeskComputer"
    mock_runner.roles.return_value = {}
    mock_runner.store.active_runs.return_value = []
    mock_runner.store.list_runs.return_value = []
    mock_runner.store.spend_since.return_value = {}

    monkeypatch.setattr(staff_router, "get_runner", lambda: mock_runner)
    monkeypatch.setattr(staff_v1_router, "get_runner", lambda: mock_runner)

    return TestClient(app)


@pytest.mark.unit
def test_adapters_chat_only_helpers() -> None:
    chat_only = chat_only_providers()
    assert "antigravity" in chat_only
    assert "claude" not in chat_only
    assert "codex" not in chat_only

    unattended = unattended_providers()
    assert unattended["antigravity"] is False
    assert unattended["claude"] is True
    assert unattended["codex"] is True


@pytest.mark.unit
def test_staff_roster_exposes_chat_only_providers(api_client: TestClient) -> None:
    resp = api_client.get("/api/staff/roster")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "chat_only_providers" in data
    assert "antigravity" in data["chat_only_providers"]
    assert "claude" not in data["chat_only_providers"]


@pytest.mark.unit
def test_staff_providers_endpoint(api_client: TestClient) -> None:
    resp = api_client.get("/api/staff/providers")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "antigravity" in data["chat_only_providers"]
    assert data["unattended_providers"]["antigravity"] is False
    assert data["unattended_providers"]["claude"] is True
    assert "claude" in data["providers"]


@pytest.mark.unit
def test_staff_v1_roster_and_providers(api_client: TestClient) -> None:
    roster_resp = api_client.get("/api/v1/staff/roster")
    assert roster_resp.status_code == 200, roster_resp.text
    roster_data = roster_resp.json()
    assert "antigravity" in roster_data["chat_only_providers"]

    prov_resp = api_client.get("/api/v1/staff/providers")
    assert prov_resp.status_code == 200, prov_resp.text
    prov_data = prov_resp.json()
    assert "antigravity" in prov_data["chat_only_providers"]
    assert prov_data["unattended_providers"]["antigravity"] is False
    assert prov_data["unattended_providers"]["claude"] is True


@pytest.mark.unit
def test_staff_board_exposes_chat_only_providers(api_client: TestClient) -> None:
    resp = api_client.get("/api/staff/board?local=true")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "chat_only_providers" in data
    assert "antigravity" in data["chat_only_providers"]
