from __future__ import annotations

import os
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

import server  # noqa: E402
from identity import Principal  # noqa: E402
from queue_cleanup import StaleRun  # noqa: E402


@pytest.fixture
def mock_find_stale_runs() -> Generator[AsyncMock, None, None]:
    with patch("routers.queue.find_stale_runs", new_callable=AsyncMock) as mock:
        yield mock


@pytest.fixture
def mock_purge_stale_runs() -> Generator[AsyncMock, None, None]:
    with patch("routers.queue.purge_stale_runs", new_callable=AsyncMock) as mock:
        yield mock


@pytest.fixture
def mock_cache_delete() -> Generator[MagicMock, None, None]:
    with patch("routers.queue.cache_delete") as mock:
        yield mock


class TestGetStaleQueue:
    """Tests for GET /api/queue/stale."""

    def test_get_stale_queue_success(
        self,
        mock_auth: Any,  # noqa: ARG001
        mock_find_stale_runs: AsyncMock,
    ) -> None:
        client = TestClient(server.app)
        mock_find_stale_runs.return_value = [
            StaleRun(
                repo="test-repo",
                run_id=123,
                workflow="Build",
                branch="patch-1",
                created_at="2026-05-22T10:00:00Z",
                age_minutes=120,
                reason="abandoned-agent-run",
                safe_to_cancel=True,
                url="https://github.com/D-sorganization/test-repo/actions/runs/123",
            )
        ]

        response = client.get("/api/queue/stale")
        assert response.status_code == 200
        data = response.json()
        assert data["stale_count"] == 1
        assert len(data["runs"]) == 1
        run = data["runs"][0]
        assert run["repo"] == "test-repo"
        assert run["run_id"] == 123
        assert run["reason"] == "abandoned-agent-run"
        assert run["safe_to_cancel"] is True
        assert "url" in run

    def test_get_stale_queue_parameters(
        self,
        mock_auth: Any,  # noqa: ARG001
        mock_find_stale_runs: AsyncMock,
    ) -> None:
        client = TestClient(server.app)
        mock_find_stale_runs.return_value = []

        response = client.get("/api/queue/stale?min_age_minutes=90&repo=test-repo&reason=abandoned-agent-run")
        assert response.status_code == 200
        mock_find_stale_runs.assert_called_once_with(
            org="D-sorganization",
            min_age_minutes=90,
            repo="test-repo",
            reason="abandoned-agent-run",
        )

    def test_get_stale_queue_invalid_repo(
        self,
        mock_auth: Any,  # noqa: ARG001
    ) -> None:
        client = TestClient(server.app)
        response = client.get("/api/queue/stale?repo=invalid/repo/format")
        assert response.status_code == 422

    def test_get_stale_queue_error_handling(
        self,
        mock_auth: Any,  # noqa: ARG001
        mock_find_stale_runs: AsyncMock,
    ) -> None:
        client = TestClient(server.app)
        mock_find_stale_runs.side_effect = RuntimeError("GH Connection Error")

        response = client.get("/api/queue/stale")
        assert response.status_code == 502
        assert "GH Connection Error" in response.json()["detail"]["detail"]


class TestPurgeStaleQueue:
    """Tests for POST /api/queue/purge-stale."""

    def test_purge_stale_queue_authorization(
        self,
        make_authed_client: Any,
        mock_purge_stale_runs: AsyncMock,
    ) -> None:
        mock_purge_stale_runs.return_value = {
            "org": "D-sorganization",
            "min_age_minutes": 60,
            "dry_run": True,
            "stale_count": 0,
            "cancelled_count": 0,
            "errors": [],
            "runs": [],
        }

        # 1. Admin should be authorized
        admin = Principal(id="test-admin", type="bot", name="Test Admin", roles=["admin"])
        client = make_authed_client(admin)
        response = client.post("/api/queue/purge-stale", headers={"X-Requested-With": "XMLHttpRequest"})
        assert response.status_code == 200

        # 2. Operator should be authorized
        operator = Principal(id="test-operator", type="bot", name="Test Operator", roles=["operator"])
        client = make_authed_client(operator)
        response = client.post("/api/queue/purge-stale", headers={"X-Requested-With": "XMLHttpRequest"})
        assert response.status_code == 200

        # 3. Viewer should be forbidden (403)
        viewer = Principal(id="test-viewer", type="bot", name="Test Viewer", roles=["viewer"])
        client = make_authed_client(viewer)
        response = client.post("/api/queue/purge-stale", headers={"X-Requested-With": "XMLHttpRequest"})
        assert response.status_code == 403

        # 4. Unauthenticated should be unauthorized (401)
        server.app.dependency_overrides.clear()
        client_unauth = TestClient(server.app)
        response = client_unauth.post("/api/queue/purge-stale", headers={"X-Requested-With": "XMLHttpRequest"})
        assert response.status_code == 401

    def test_purge_stale_queue_dry_run_default(
        self,
        mock_auth: Any,  # noqa: ARG001
        mock_purge_stale_runs: AsyncMock,
    ) -> None:
        client = TestClient(server.app)
        mock_purge_stale_runs.return_value = {"dry_run": True}

        response = client.post("/api/queue/purge-stale", headers={"X-Requested-With": "XMLHttpRequest"})
        assert response.status_code == 200
        mock_purge_stale_runs.assert_called_once_with(
            "D-sorganization",
            min_age_minutes=60,
            dry_run=True,
            superseded_only=True,
            repo=None,
            workflow=None,
            reason=None,
            safe_only=True,
            max_count=None,
        )

    def test_purge_stale_queue_parameters_query(
        self,
        mock_auth: Any,  # noqa: ARG001
        mock_purge_stale_runs: AsyncMock,
    ) -> None:
        client = TestClient(server.app)
        mock_purge_stale_runs.return_value = {"dry_run": False}

        response = client.post(
            "/api/queue/purge-stale?min_age_minutes=45&repo=test-repo&reason=abandoned-agent-run&dry_run=false",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert response.status_code == 200
        mock_purge_stale_runs.assert_called_once_with(
            "D-sorganization",
            min_age_minutes=45,
            dry_run=False,
            superseded_only=True,
            repo="test-repo",
            workflow=None,
            reason="abandoned-agent-run",
            safe_only=True,
            max_count=None,
        )

    def test_purge_stale_queue_parameters_body(
        self,
        mock_auth: Any,  # noqa: ARG001
        mock_purge_stale_runs: AsyncMock,
    ) -> None:
        client = TestClient(server.app)
        mock_purge_stale_runs.return_value = {"dry_run": False}

        response = client.post(
            "/api/queue/purge-stale",
            json={
                "min_age_minutes": 30,
                "repo": "test-repo",
                "reason": "stale-feature-branch",
                "dry_run": False,
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert response.status_code == 200
        mock_purge_stale_runs.assert_called_once_with(
            "D-sorganization",
            min_age_minutes=30,
            repo="test-repo",
            reason="stale-feature-branch",
            dry_run=False,
            superseded_only=True,
            workflow=None,
            safe_only=True,
            max_count=None,
        )

    def test_purge_stale_queue_cache_invalidation_on_purge(
        self,
        mock_auth: Any,  # noqa: ARG001
        mock_purge_stale_runs: AsyncMock,
        mock_cache_delete: MagicMock,
    ) -> None:
        client = TestClient(server.app)
        mock_purge_stale_runs.return_value = {
            "dry_run": False,
            "cancelled_count": 5,
        }

        response = client.post(
            "/api/queue/purge-stale?dry_run=false",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert response.status_code == 200
        assert mock_cache_delete.call_count == 3
        mock_cache_delete.assert_any_call("queue")
        mock_cache_delete.assert_any_call("queue:stale")
        mock_cache_delete.assert_any_call("diagnose")

    def test_purge_stale_queue_no_cache_invalidation_on_dry_run(
        self,
        mock_auth: Any,  # noqa: ARG001
        mock_purge_stale_runs: AsyncMock,
        mock_cache_delete: MagicMock,
    ) -> None:
        client = TestClient(server.app)
        mock_purge_stale_runs.return_value = {
            "dry_run": True,
            "cancelled_count": 5,
        }

        response = client.post(
            "/api/queue/purge-stale?dry_run=true",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert response.status_code == 200
        mock_cache_delete.assert_not_called()

    def test_purge_stale_queue_invalid_repo(
        self,
        mock_auth: Any,  # noqa: ARG001
    ) -> None:
        client = TestClient(server.app)
        response = client.post(
            "/api/queue/purge-stale?repo=invalid/repo/format",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert response.status_code == 422

    def test_purge_stale_queue_error_handling(
        self,
        mock_auth: Any,  # noqa: ARG001
        mock_purge_stale_runs: AsyncMock,
    ) -> None:
        client = TestClient(server.app)
        mock_purge_stale_runs.side_effect = RuntimeError("Failed to cancel")

        response = client.post("/api/queue/purge-stale", headers={"X-Requested-With": "XMLHttpRequest"})
        assert response.status_code == 502
        assert "Failed to purge stale runs: Failed to cancel" in response.json()["detail"]["detail"]
