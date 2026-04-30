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
async def client(app):
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


def _mock_httpx_response(json_data: dict, status_code: int = 200) -> httpx.Response:
    """Build a mock httpx.Response."""
    return httpx.Response(status_code, json=json_data, request=httpx.Request("GET", "http://maxwell.test/api/mock"))


def _mock_html_response(status_code: int = 502) -> httpx.Response:
    return httpx.Response(
        status_code,
        text="<html>oom</html>",
        headers={"content-type": "text/html"},
        request=httpx.Request("GET", "http://maxwell.test/api/mock"),
    )


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
    """GET /api/maxwell/version proxies daemon response and exposes 'contract' key."""
    payload = {"daemon": "1.0.0", "contract": "1.0.0"}
    mock_cm = _make_mock_client(get_return=_mock_httpx_response(payload))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/version")
    assert resp.status_code == 200
    data = resp.json()
    assert "contract" in data
    assert data["contract"] == "1.0.0"


@pytest.mark.asyncio
async def test_get_maxwell_version_daemon_unreachable_returns_200(client) -> None:
    """When daemon is unreachable, version endpoint preserves the upstream failure."""
    mock_cm = _make_mock_client(get_side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/version")
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"]["upstream"] == "maxwell"
    assert data["detail"]["status"] == 503


@pytest.mark.asyncio
async def test_get_maxwell_version_html_502_preserves_upstream_status(client) -> None:
    """HTML Maxwell 502 responses surface as structured 502 payloads."""
    mock_cm = _make_mock_client(get_return=_mock_html_response(502))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/version")
    assert resp.status_code == 502
    data = resp.json()
    assert data["detail"]["upstream"] == "maxwell"
    assert data["detail"]["status"] == 502


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
async def test_get_maxwell_tasks_daemon_unreachable_returns_200(client) -> None:
    """When daemon is unreachable, tasks endpoint preserves the upstream failure."""
    mock_cm = _make_mock_client(get_side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/tasks")
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"]["upstream"] == "maxwell"


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
async def test_get_maxwell_task_detail_daemon_unreachable_returns_200(client) -> None:
    """When daemon is unreachable, task detail preserves the upstream failure."""
    mock_cm = _make_mock_client(get_side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/tasks/abc123")
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"]["upstream"] == "maxwell"


# ─── GET /api/maxwell/daemon-status ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_maxwell_daemon_status_returns_200(client) -> None:
    """GET /api/maxwell/daemon-status proxies pipeline state from daemon."""
    payload = {"pipeline_state": "idle"}
    mock_cm = _make_mock_client(get_return=_mock_httpx_response(payload))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/daemon-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("pipeline_state") == "idle"


@pytest.mark.asyncio
async def test_get_maxwell_daemon_status_unreachable_returns_200(client) -> None:
    """When daemon is unreachable, daemon-status preserves the upstream failure."""
    mock_cm = _make_mock_client(get_side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/daemon-status")
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"]["upstream"] == "maxwell"


# ─── POST /api/maxwell/pipeline-control/{action} ─────────────────────────────


@pytest.mark.asyncio
async def test_maxwell_pipeline_control_pause_returns_200(client) -> None:
    """POST /api/maxwell/pipeline-control/pause returns 200 when daemon responds."""
    payload = {"status": "paused"}
    mock_cm = _make_mock_client(post_return=_mock_httpx_response(payload))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post("/api/maxwell/pipeline-control/pause", json={})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_maxwell_pipeline_control_resume_returns_200(client) -> None:
    """POST /api/maxwell/pipeline-control/resume returns 200 when daemon responds."""
    payload = {"status": "resumed"}
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
async def test_maxwell_pipeline_control_daemon_unreachable_returns_200(client) -> None:
    """When daemon is unreachable, pipeline-control preserves the upstream failure."""
    mock_cm = _make_mock_client(post_side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post("/api/maxwell/pipeline-control/abort", json={})
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"]["upstream"] == "maxwell"


# ─── POST /api/maxwell/dispatch ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_maxwell_dispatch_daemon_unreachable_returns_200(client) -> None:
    """When daemon is unreachable, dispatch preserves the upstream failure."""
    mock_cm = _make_mock_client(post_side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post("/api/maxwell/dispatch", json={"repo": "test-repo"})
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"]["upstream"] == "maxwell"


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
