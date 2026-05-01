from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

import server  # noqa: E402
from linear_client import LinearAPIError  # noqa: E402
from routers import linear as linear_router  # noqa: E402


@pytest.fixture
def client(mock_auth) -> TestClient:  # noqa: ARG001
    return TestClient(server.app)


def test_get_workspaces_returns_configured_list(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        linear_router,
        "list_workspace_summaries",
        AsyncMock(return_value=[{"id": "personal", "auth_status": "ok", "auth_kind": "api_key"}]),
    )

    response = client.get("/api/linear/workspaces")

    assert response.status_code == 200
    assert response.json()["workspaces"][0]["id"] == "personal"


def test_get_workspaces_auth_status_missing_env_when_key_unset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {"workspaces": [{"id": "personal"}], "mappings": {}}
    monkeypatch.setattr(linear_router, "load_linear_config", lambda: config)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    linear_router._auth_status_cache.clear()

    response = client.get("/api/linear/workspaces")

    assert response.status_code == 200
    assert response.json()["workspaces"][0]["auth_status"] == "missing_env"


def test_get_workspaces_auth_status_auth_failed_on_401(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"workspaces": [{"id": "personal"}], "mappings": {}}
    monkeypatch.setattr(linear_router, "load_linear_config", lambda: config)
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")

    class FakeClient:
        async def fetch_workspace(self, workspace_id: str) -> dict:
            raise LinearAPIError("unauthorized", status_code=401)

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(linear_router, "build_linear_client", lambda _config: FakeClient())
    linear_router._auth_status_cache.clear()

    response = client.get("/api/linear/workspaces")

    assert response.status_code == 200
    assert response.json()["workspaces"][0]["auth_status"] == "auth_failed"


def test_get_teams_filters_by_workspace_param(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"workspaces": [{"id": "personal"}, {"id": "ops"}], "mappings": {}}
    monkeypatch.setattr(linear_router, "load_linear_config", lambda: config)
    monkeypatch.setattr(linear_router, "has_configured_linear_key", lambda *_args, **_kwargs: True)

    class FakeClient:
        async def fetch_teams(self, workspace_id: str) -> list[dict]:
            return [{"id": workspace_id + "-team", "key": workspace_id.upper(), "name": workspace_id.title()}]

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(linear_router, "build_linear_client", lambda _config: FakeClient())

    response = client.get("/api/linear/teams?workspace=ops")

    assert response.status_code == 200
    assert response.json() == {"teams": [{"id": "ops-team", "key": "OPS", "name": "Ops"}]}


def test_get_issues_503_when_linear_api_key_unset(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        linear_router,
        "load_linear_config",
        lambda: {"workspaces": [{"id": "personal", "mapping": "default"}], "mappings": {"default": {}}},
    )
    monkeypatch.setattr(linear_router, "has_configured_linear_key", lambda *_args, **_kwargs: False)

    response = client.get("/api/linear/issues")

    assert response.status_code == 503
    assert response.json()["detail"] == linear_router.LINEAR_NOT_CONFIGURED_DETAIL


def test_get_issues_returns_canonical_shape(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"workspaces": [{"id": "personal", "mapping": "default"}], "mappings": {"default": {}}}
    monkeypatch.setattr(linear_router, "load_linear_config", lambda: config)
    monkeypatch.setattr(linear_router, "has_configured_linear_key", lambda *_args, **_kwargs: True)

    class FakeClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(linear_router, "build_linear_client", lambda _config: FakeClient())
    monkeypatch.setattr(
        linear_router.linear_inventory,
        "fetch_all_issues",
        AsyncMock(return_value={"items": [{"title": "Linear issue"}], "errors": []}),
    )

    response = client.get("/api/linear/issues")

    assert response.status_code == 200
    assert response.json() == {
        "items": [{"title": "Linear issue"}],
        "errors": [],
        "stats": {"linear_total": 1},
    }
