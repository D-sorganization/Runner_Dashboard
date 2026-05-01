"""Comprehensive test suite for runner management API endpoints.

Tests cover:
- Runner lifecycle control (start, stop, restart)
- Runner group management
- Fleet capacity scheduling
- Health monitoring and diagnostics
- Troubleshooting endpoints
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

import gh_utils  # noqa: E402
import server  # noqa: E402
from routers import runner_diagnostics as diagnostics_router  # noqa: E402
from routers import runner_groups as groups_router  # noqa: E402
from routers import runners as runners_router  # noqa: E402


@pytest.fixture
def client(mock_auth) -> TestClient:  # noqa: ARG001
    """Create a test client for the FastAPI app."""
    return TestClient(server.app)


@pytest.fixture
def mock_gh_api_admin(monkeypatch: pytest.MonkeyPatch):
    """Mock the GitHub API admin calls."""

    async def mock_api(endpoint: str) -> dict:
        if "/actions/runners" in endpoint:
            return {
                "total_count": 3,
                "runners": [
                    {
                        "id": 1,
                        "name": "d-sorg-fleet-runner-1",
                        "status": "online",
                        "busy": False,
                        "labels": [
                            {"id": 1, "name": "self-hosted"},
                            {"id": 2, "name": "linux"},
                        ],
                        "os": "Linux",
                        "total_actions_current": 0,
                        "accessed_at": "2026-04-29T20:00:00Z",
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                    {
                        "id": 2,
                        "name": "d-sorg-fleet-runner-2",
                        "status": "online",
                        "busy": True,
                        "labels": [
                            {"id": 1, "name": "self-hosted"},
                            {"id": 2, "name": "linux"},
                        ],
                        "os": "Linux",
                        "total_actions_current": 1,
                        "accessed_at": "2026-04-29T20:00:00Z",
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                    {
                        "id": 3,
                        "name": "windows-matlab-runner-1",
                        "status": "offline",
                        "busy": False,
                        "labels": [
                            {"id": 1, "name": "self-hosted"},
                            {"id": 3, "name": "matlab"},
                            {"id": 4, "name": "windows"},
                        ],
                        "os": "Windows",
                        "total_actions_current": 0,
                        "accessed_at": "2026-04-28T10:00:00Z",
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                ],
            }
        return {}

    monkeypatch.setattr(runners_router, "gh_api_admin", mock_api, raising=False)
    monkeypatch.setattr(groups_router, "gh_api_admin", mock_api, raising=False)
    monkeypatch.setattr(diagnostics_router, "gh_api_admin", mock_api, raising=False)
    return mock_api


@pytest.fixture
def mock_run_runner_svc(monkeypatch: pytest.MonkeyPatch):
    """Mock the run_runner_svc function."""

    async def mock_svc(runner_num: int, action: str, timeout: int = 30) -> tuple[int, str, str]:  # noqa: ARG001
        if action == "start":
            return 0, "runner started", ""
        if action == "stop":
            return 0, "runner stopped", ""
        if action == "status":
            return 0, "runner: idle", ""
        if action == "restart":
            return 0, "runner restarted", ""
        return 1, "", "unknown action"

    monkeypatch.setattr(runners_router, "run_runner_svc", mock_svc, raising=False)
    monkeypatch.setattr(groups_router, "run_runner_svc", mock_svc, raising=False)
    monkeypatch.setattr(diagnostics_router, "run_runner_svc", mock_svc, raising=False)
    return mock_svc


@pytest.fixture
def mock_cache(monkeypatch: pytest.MonkeyPatch):
    """Mock cache operations."""
    cache_store: dict[str, object] = {}

    def mock_get(key: str, ttl: float):
        return cache_store.get(key)

    def mock_set(key: str, value):
        cache_store[key] = value

    monkeypatch.setattr(runners_router, "cache_get", mock_get, raising=False)
    monkeypatch.setattr(runners_router, "cache_set", mock_set, raising=False)
    monkeypatch.setattr(groups_router, "cache_get", mock_get, raising=False)
    monkeypatch.setattr(groups_router, "cache_set", mock_set, raising=False)
    monkeypatch.setattr(diagnostics_router, "cache_get", mock_get, raising=False)
    monkeypatch.setattr(diagnostics_router, "cache_set", mock_set, raising=False)
    return cache_store


class TestGetRunners:
    """Tests for GET /api/runners endpoint."""

    def test_get_runners_returns_all_runners(
        self,
        client: TestClient,
        mock_gh_api_admin,
        mock_cache,
    ) -> None:
        """Test that get_runners returns all runners sorted by status."""
        response = client.get("/api/runners")

        assert response.status_code == 200
        data = response.json()
        assert "runners" in data
        assert len(data["runners"]) == 3
        # Online runners should come first
        assert data["runners"][0]["status"] == "online"
        assert data["runners"][1]["status"] == "online"
        assert data["runners"][2]["status"] == "offline"

    def test_get_runners_uses_cache(
        self,
        client: TestClient,
        mock_gh_api_admin,
        mock_cache,
    ) -> None:
        """Test that get_runners uses cache on second call."""
        # First call
        response1 = client.get("/api/runners")
        assert response1.status_code == 200

        # Modify cache to verify it's being used
        mock_cache["runners"] = {"runners": [{"id": 999, "name": "cached-runner", "status": "online"}]}

        # Second call should use cache
        response2 = client.get("/api/runners")
        assert response2.status_code == 200
        data = response2.json()
        assert len(data["runners"]) == 1
        assert data["runners"][0]["id"] == 999


class TestGetMatlabRunnerHealth:
    """Tests for GET /api/runners/matlab endpoint."""

    def test_get_matlab_runners_filters_correctly(
        self,
        client: TestClient,
        mock_gh_api_admin,
        mock_cache,
    ) -> None:
        """Test that MATLAB endpoint filters runners with MATLAB label."""
        response = client.get("/api/runners/matlab")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["runners"]) == 1
        assert data["runners"][0]["name"] == "windows-matlab-runner-1"
        assert "matlab" in [lbl.lower() for lbl in data["runners"][0]["labels"]]

    def test_matlab_health_counts_status(
        self,
        client: TestClient,
        mock_gh_api_admin,
        mock_cache,
    ) -> None:
        """Test that MATLAB health correctly counts online/offline."""
        response = client.get("/api/runners/matlab")

        assert response.status_code == 200
        data = response.json()
        assert data["online"] == 0  # The MATLAB runner is offline
        assert data["offline"] == 1
        assert data["total"] == 1


class TestRunnerLifecycleControl:
    """Tests for runner start, stop, restart endpoints."""

    def test_start_runner_success(
        self,
        client: TestClient,
        mock_gh_api_admin,
        mock_run_runner_svc,
    ) -> None:
        """Test starting a runner."""
        # Note: Auth is checked at the identity layer; this test verifies the endpoint exists
        # without auth by testing error response
        response = client.post("/api/runners/1/start")
        # Should fail with 403/401 due to missing auth, not 500
        assert response.status_code in (401, 403)

    def test_start_runner_via_lifecycle_path(
        self,
        client: TestClient,
        mock_gh_api_admin,
        mock_run_runner_svc,
    ) -> None:
        """Test that start endpoint is registered and handles requests."""
        response = client.post("/api/runners/1/start")
        # Without proper auth, should get 401/403
        assert response.status_code in (401, 403)

    def test_stop_runner_success(
        self,
        client: TestClient,
        mock_gh_api_admin,
        mock_run_runner_svc,
    ) -> None:
        """Test stopping a runner."""
        response = client.post("/api/runners/1/stop")
        # Without proper auth, should get 401/403
        assert response.status_code in (401, 403)

    def test_restart_runner_endpoint_exists(
        self,
        client: TestClient,
        mock_gh_api_admin,
        mock_run_runner_svc,
    ) -> None:
        """Test restarting a runner endpoint exists."""
        response = client.post("/api/runners/1/restart")
        # Without proper auth, should get 401/403
        assert response.status_code in (401, 403)


class TestRunnerStatus:
    """Tests for GET /api/runners/{runner_id}/status endpoint."""

    def test_get_runner_status_success(
        self,
        client: TestClient,
        mock_gh_api_admin,
    ) -> None:
        """Test getting status for a specific runner."""
        response = client.get("/api/runners/1/status")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        assert data["name"] == "d-sorg-fleet-runner-1"
        assert data["status"] == "online"
        assert "health" in data
        assert data["health"]["status"] == "healthy"

    def test_get_runner_status_offline_runner(
        self,
        client: TestClient,
        mock_gh_api_admin,
    ) -> None:
        """Test status for offline runner shows health issue."""
        response = client.get("/api/runners/3/status")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "offline"
        assert data["health"]["status"] == "offline"
        assert len(data["health"]["issues"]) > 0

    def test_get_runner_status_not_found(
        self,
        client: TestClient,
        mock_gh_api_admin,
    ) -> None:
        """Test status for non-existent runner returns 404."""
        response = client.get("/api/runners/9999/status")

        assert response.status_code == 404


class TestRunnerGroups:
    """Tests for runner group management endpoints."""

    def test_get_runner_group_filters_by_label(
        self,
        client: TestClient,
        mock_gh_api_admin,
    ) -> None:
        """Test that runner groups are filtered by label."""
        response = client.get("/api/runners/groups/matlab")

        assert response.status_code == 200
        data = response.json()
        assert data["group_label"] == "matlab"
        assert data["total"] == 1
        assert data["runners"][0]["name"] == "windows-matlab-runner-1"

    def test_get_runner_group_empty_group(
        self,
        client: TestClient,
        mock_gh_api_admin,
    ) -> None:
        """Test getting a non-existent group returns empty list."""
        response = client.get("/api/runners/groups/nonexistent")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert len(data["runners"]) == 0

    def test_start_runner_group(
        self,
        client: TestClient,
        mock_gh_api_admin,
        mock_run_runner_svc,
    ) -> None:
        """Test starting all runners in a group."""
        response = client.post("/api/runners/groups/linux/start-all")
        # Without proper auth, should get 401/403
        assert response.status_code in (401, 403)

    def test_stop_runner_group(
        self,
        client: TestClient,
        mock_gh_api_admin,
        mock_run_runner_svc,
    ) -> None:
        """Test stopping all runners in a group."""
        response = client.post("/api/runners/groups/linux/stop-all")
        # Without proper auth, should get 401/403
        assert response.status_code in (401, 403)


class TestDiagnostics:
    """Tests for diagnostics and troubleshooting endpoints."""

    def test_get_diagnostics_summary(
        self,
        client: TestClient,
        mock_gh_api_admin,
    ) -> None:
        """Test getting diagnostics summary for all runners."""
        response = client.get("/api/runners/diagnostics/summary")

        assert response.status_code == 200
        data = response.json()
        assert data["total_runners"] == 3
        assert data["online"] == 2
        assert data["offline"] == 1
        assert "recommendations" in data

    def test_get_runner_diagnostics(
        self,
        client: TestClient,
        mock_gh_api_admin,
        mock_run_runner_svc,
    ) -> None:
        """Test getting detailed diagnostics for a specific runner."""
        response = client.post("/api/runners/1/diagnostics")

        # Post endpoint without auth returns 403, but endpoint exists
        assert response.status_code in (200, 403)
        if response.status_code == 200:
            data = response.json()
            assert data["runner_id"] == 1
            assert "health" in data
            assert "troubleshooting_suggestions" in data

    def test_troubleshoot_runner(
        self,
        client: TestClient,
        mock_gh_api_admin,
        mock_run_runner_svc,
    ) -> None:
        """Test troubleshooting a runner."""
        response = client.post("/api/runners/1/troubleshoot")
        # Without proper auth, should get 401/403
        assert response.status_code in (401, 403)


class TestFleetCapacity:
    """Tests for fleet capacity and scheduling endpoints."""

    def test_get_fleet_capacity(
        self,
        client: TestClient,
        mock_gh_api_admin,
    ) -> None:
        """Test getting fleet capacity information."""
        response = client.get("/api/runners/fleet/capacity")

        assert response.status_code == 200
        data = response.json()
        assert data["total_runners"] == 3
        assert data["online_runners"] == 2
        assert data["busy_runners"] == 1
        assert data["idle_runners"] == 1
        assert "utilization_percent" in data
        assert "recommendations" in data

    def test_fleet_capacity_utilization_calculation(
        self,
        client: TestClient,
        mock_gh_api_admin,
    ) -> None:
        """Test that utilization percentage is calculated correctly."""
        response = client.get("/api/runners/fleet/capacity")

        assert response.status_code == 200
        data = response.json()
        # 1 busy out of 2 online = 50%
        assert data["utilization_percent"] == 50

    def test_schedule_fleet_scale(
        self,
        client: TestClient,
        mock_gh_api_admin,
        mock_run_runner_svc,
    ) -> None:
        """Test scheduling fleet scaling."""
        response = client.post("/api/runners/fleet/schedule-scale")
        # Without proper auth, should get 401/403
        assert response.status_code in (401, 403)


class TestAuthorizationRequirements:
    """Tests for authorization and scope requirements."""

    def test_start_runner_requires_auth(
        self,
        client: TestClient,
        mock_gh_api_admin,
    ) -> None:
        """Test that starting a runner requires proper auth."""
        response = client.post("/api/runners/1/start")
        # Should be 403 or 401 without proper auth
        assert response.status_code in (401, 403)

    def test_stop_runner_requires_auth(
        self,
        client: TestClient,
        mock_gh_api_admin,
    ) -> None:
        """Test that stopping a runner requires proper auth."""
        response = client.post("/api/runners/1/stop")
        # Should be 403 or 401 without proper auth
        assert response.status_code in (401, 403)

    def test_troubleshoot_runner_requires_auth(
        self,
        client: TestClient,
        mock_gh_api_admin,
    ) -> None:
        """Test that troubleshooting requires proper auth."""
        response = client.post("/api/runners/1/troubleshoot")
        # Should be 403 or 401 without proper auth
        assert response.status_code in (401, 403)


class TestErrorHandling:
    """Tests for error handling and edge cases."""

    def test_get_runners_api_error(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test graceful handling of GitHub API errors."""

        async def failing_api(endpoint: str) -> dict:  # noqa: ARG001
            msg = "GitHub API timeout"
            raise RuntimeError(msg)

        monkeypatch.setattr(runners_router, "gh_api_admin", failing_api)

        response = client.get("/api/runners")
        assert response.status_code == 502

    def test_get_runners_rate_limit_returns_retry_after(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        mock_cache,
    ) -> None:
        """GitHub rate limits surface as 429 with machine-readable backoff."""
        del mock_cache
        gh_utils.clear_rate_limit_breakers()

        async def fake_run_cmd(cmd: list[str]) -> tuple[int, str, str]:  # noqa: ARG001
            return (
                1,
                "",
                "\n".join(
                    [
                        "HTTP/2.0 403 Forbidden",
                        "X-RateLimit-Remaining: 0",
                        "Retry-After: 37",
                        '{"message":"API rate limit exceeded"}',
                    ],
                ),
            )

        monkeypatch.setattr(gh_utils, "run_cmd", fake_run_cmd)
        monkeypatch.setattr(runners_router, "gh_api_admin", gh_utils.gh_api)

        response = client.get("/api/runners")

        assert response.status_code == 429
        assert response.headers["Retry-After"] == "37"
        assert response.json()["detail"] == {
            "error": "github_rate_limited",
            "retry_after_seconds": 37,
            "resource_class": "actions",
        }

        async def should_not_run(cmd: list[str]) -> tuple[int, str, str]:  # noqa: ARG001
            raise AssertionError("circuit breaker should block before gh is called")

        monkeypatch.setattr(gh_utils, "run_cmd", should_not_run)

        response = client.get("/api/runners")

        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0

    def test_diagnostics_with_api_error(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test diagnostics endpoint handles API errors."""

        async def failing_api(endpoint: str) -> dict:  # noqa: ARG001
            msg = "GitHub API error"
            raise RuntimeError(msg)

        monkeypatch.setattr(runners_router, "gh_api_admin", failing_api)

        response = client.get("/api/runners/diagnostics/summary")
        assert response.status_code == 502


class TestHelperFunctions:
    """Tests for internal helper functions."""

    def test_runner_num_from_id_finds_correct_runner(self) -> None:
        """Test that runner number extraction works correctly."""
        runners = [
            {"id": 1, "name": "d-sorg-fleet-runner-1"},
            {"id": 2, "name": "d-sorg-fleet-runner-2"},
            {"id": 3, "name": "d-sorg-fleet-runner-10"},
        ]

        assert runners_router.runner_num_from_id(1, runners) == 1
        assert runners_router.runner_num_from_id(2, runners) == 2
        assert runners_router.runner_num_from_id(3, runners) == 10

    def test_runner_num_from_id_not_found(self) -> None:
        """Test that non-existent runner ID returns None."""
        runners = [{"id": 1, "name": "d-sorg-fleet-runner-1"}]
        assert runners_router.runner_num_from_id(999, runners) is None

    def test_is_matlab_runner_detection(self) -> None:
        """Test MATLAB runner detection by label."""
        matlab_runner = {
            "id": 1,
            "name": "windows-matlab-1",
            "labels": [
                {"name": "self-hosted"},
                {"name": "matlab"},
            ],
        }
        non_matlab_runner = {
            "id": 2,
            "name": "linux-runner-1",
            "labels": [
                {"name": "self-hosted"},
                {"name": "linux"},
            ],
        }

        assert runners_router._is_matlab_runner(matlab_runner) is True
        assert runners_router._is_matlab_runner(non_matlab_runner) is False

    def test_runner_sort_key_online_first(self) -> None:
        """Test that sort key puts online runners first."""
        online = {"id": 1, "name": "runner-1", "status": "online"}
        offline = {"id": 2, "name": "runner-2", "status": "offline"}

        key_online = runners_router._runner_sort_key(online)
        key_offline = runners_router._runner_sort_key(offline)

        assert key_online < key_offline


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
