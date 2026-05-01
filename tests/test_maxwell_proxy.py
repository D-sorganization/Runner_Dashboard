"""Contract tests for Maxwell-Daemon proxy routes (rd#102)."""

from __future__ import annotations  # noqa: E402

import os  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

# Ensure backend/ is on sys.path before importing the app
_BACKEND = Path(__file__).parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")


@pytest.fixture(scope="module")
def app():
    """Import and return the FastAPI app (module-scoped to pay import cost once)."""
    import server  # noqa: PLC0415

    return server.app


@pytest_asyncio.fixture
async def client(app, mock_auth):  # noqa: ARG001
    """Async HTTP client wired directly to the ASGI app."""
    from httpx import ASGITransport, AsyncClient  # noqa: PLC0415

    headers = {
        "Authorization": "Bearer test-key",
        "X-Requested-With": "XMLHttpRequest",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers=headers,
    ) as ac:
        yield ac


def _mock_httpx_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """Build a mock httpx.Response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json.return_value = json_data
    return mock_resp


def test_maxwell_router_paths_are_mounted_once(app) -> None:
    """The canonical Maxwell APIRouter is mounted and not shadowed by server.py routes."""
    expected_paths = {
        "/api/maxwell/status",
        "/api/maxwell/control",
        "/api/maxwell/version",
        "/api/maxwell/daemon-status",
        "/api/maxwell/tasks",
        "/api/maxwell/tasks/{task_id}",
        "/api/maxwell/dispatch",
        "/api/maxwell/chat",
        "/api/maxwell/pipeline-control/{action}",
        "/api/maxwell/backends",
        "/api/maxwell/workers",
        "/api/maxwell/cost",
        "/api/maxwell/pipeline-state",
    }
    maxwell_routes = [route for route in app.routes if getattr(route, "path", "").startswith("/api/maxwell/")]
    route_paths = [route.path for route in maxwell_routes]

    for path in expected_paths:
        assert route_paths.count(path) == 1, f"{path} route count was {route_paths.count(path)}"

    assert all(route.endpoint.__module__ == "routers.maxwell" for route in maxwell_routes)


def _make_mock_client(get_return=None, post_return=None, get_side_effect=None, post_side_effect=None):
    """Build a mock AsyncClient context manager that yields a mock client."""
    mock_client = MagicMock()
    if get_side_effect is not None:
        mock_client.get = AsyncMock(side_effect=get_side_effect)
    elif get_return is not None:
        mock_client.get = AsyncMock(return_value=get_return)
    if post_side_effect is not None:
        mock_client.post = AsyncMock(side_effect=post_side_effect)
    elif post_return is not None:
        mock_client.post = AsyncMock(return_value=post_return)

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


# ─── GET /api/maxwell/version ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_maxwell_version_returns_200_with_contract(client) -> None:
    """GET /api/maxwell/version proxies daemon response through the contract model."""
    payload = {"version": "1.0.0", "build": "abc123"}
    mock_cm = _make_mock_client(get_return=_mock_httpx_response(payload))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/version")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == "1.0.0"
    assert data["build"] == "abc123"


@pytest.mark.asyncio
async def test_get_maxwell_version_daemon_unreachable_returns_503(client) -> None:
    """When daemon is unreachable, the mounted router returns a proxy error."""
    mock_cm = _make_mock_client(get_side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/version")
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"] == "maxwell connection error"


# ─── GET /api/maxwell/tasks ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_maxwell_tasks_returns_200_with_tasks_key(client) -> None:
    """GET /api/maxwell/tasks proxies daemon response and exposes 'tasks' key."""
    payload = {"tasks": [], "total": 0}
    mock_cm = _make_mock_client(get_return=_mock_httpx_response(payload))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert "tasks" in data


@pytest.mark.asyncio
async def test_get_maxwell_tasks_daemon_unreachable_returns_503(client) -> None:
    """When daemon is unreachable, the mounted router returns a proxy error."""
    mock_cm = _make_mock_client(get_side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/tasks")
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"] == "maxwell connection error"


# ─── GET /api/maxwell/tasks/{task_id} ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_maxwell_task_detail_returns_200(client) -> None:
    """GET /api/maxwell/tasks/{task_id} proxies daemon task detail."""
    payload = {"id": "abc123", "status": "completed", "repo": "my-repo"}
    mock_cm = _make_mock_client(get_return=_mock_httpx_response(payload))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/tasks/abc123")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("id") == "abc123"


@pytest.mark.asyncio
async def test_get_maxwell_task_detail_daemon_unreachable_returns_503(client) -> None:
    """When daemon is unreachable, the mounted router returns a proxy error."""
    mock_cm = _make_mock_client(get_side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/tasks/abc123")
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"] == "maxwell connection error"


# ─── GET /api/maxwell/daemon-status ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_maxwell_daemon_status_returns_200(client) -> None:
    """GET /api/maxwell/daemon-status proxies pipeline state from daemon."""
    payload = {"state": "idle", "active_tasks": 0, "queued_tasks": 0}
    mock_cm = _make_mock_client(get_return=_mock_httpx_response(payload))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/daemon-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "idle"


@pytest.mark.asyncio
async def test_get_maxwell_daemon_status_unreachable_returns_503(client) -> None:
    """When daemon is unreachable, the mounted router returns a proxy error."""
    mock_cm = _make_mock_client(get_side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/daemon-status")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "maxwell connection error"


# ─── POST /api/maxwell/pipeline-control/{action} ─────────────────────────────


@pytest.mark.asyncio
async def test_maxwell_pipeline_control_pause_returns_200(client) -> None:
    """POST /api/maxwell/pipeline-control/pause returns 200 when daemon responds."""
    payload = {"action": "pause", "status": "paused"}
    mock_cm = _make_mock_client(post_return=_mock_httpx_response(payload))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post("/api/maxwell/pipeline-control/pause", json={})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_maxwell_pipeline_control_resume_returns_200(client) -> None:
    """POST /api/maxwell/pipeline-control/resume returns 200 when daemon responds."""
    payload = {"action": "resume", "status": "resumed"}
    mock_cm = _make_mock_client(post_return=_mock_httpx_response(payload))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post("/api/maxwell/pipeline-control/resume", json={})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_maxwell_pipeline_control_badaction_returns_422(client) -> None:
    """POST /api/maxwell/pipeline-control/badaction must return 422."""
    resp = await client.post("/api/maxwell/pipeline-control/badaction", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_maxwell_pipeline_control_injects_token(client, monkeypatch) -> None:
    """Pipeline-control injects the configured token into the canonical router request."""
    monkeypatch.setattr("routers.maxwell.MAXWELL_API_TOKEN", "test-maxwell-token")
    mock_cm = _make_mock_client(post_return=_mock_httpx_response({"action": "abort", "status": "aborted"}))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post("/api/maxwell/pipeline-control/abort", json={})
    assert resp.status_code == 200
    sent_body = mock_cm.__aenter__.return_value.post.call_args.kwargs["content"]
    sent_headers = mock_cm.__aenter__.return_value.post.call_args.kwargs["headers"]
    assert '"confirmation_token": "test-maxwell-token"' in sent_body
    assert sent_headers["Authorization"] == "Bearer test-maxwell-token"


@pytest.mark.asyncio
async def test_maxwell_pipeline_control_daemon_unreachable_returns_503(client) -> None:
    """When daemon is unreachable, the mounted router returns a proxy error."""
    mock_cm = _make_mock_client(post_side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post("/api/maxwell/pipeline-control/abort", json={})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "maxwell connection error"


# ─── POST /api/maxwell/dispatch ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_maxwell_dispatch_daemon_unreachable_returns_503(client) -> None:
    """When daemon is unreachable, the mounted router returns a proxy error."""
    mock_cm = _make_mock_client(post_side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post("/api/maxwell/dispatch", json={"repo": "test-repo"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "maxwell connection error"


# ─── POST /api/maxwell/chat ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_maxwell_chat_daemon_unreachable_streams_fallback(client) -> None:
    """When daemon is unreachable, chat streams a readable fallback instead of breaking the tab."""
    mock_client = MagicMock()
    mock_client.stream.side_effect = httpx.ConnectError("connection refused")
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post("/api/maxwell/chat", json={"message": "status"})
    assert resp.status_code == 200
    assert "Maxwell-Daemon is unreachable" in resp.text
