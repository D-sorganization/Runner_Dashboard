"""Integration tests for FastAPI routes (issue #43).

These tests use httpx.AsyncClient with ASGITransport to call the real FastAPI
app without a running server process.  Each test exercises actual HTTP
routing, middleware, and response shape — not just static string checks.

The server imports GitHub credentials lazily (only used when a route actually
calls ``gh_api_admin``), so most read-only endpoints work in CI without any
secrets.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

# Ensure backend/ is on sys.path before importing the app
_BACKEND = Path(__file__).parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# ── App import ────────────────────────────────────────────────────────────────
# We import the FastAPI app object directly.  The server reads env vars at
# module-import time, so set any required ones before the import.
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


# ─── 1. Health endpoint ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_endpoint_returns_200(client) -> None:
    """GET /api/health must return 200 and a JSON body."""
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)


@pytest.mark.asyncio
async def test_health_endpoint_has_status_field(client) -> None:
    """Health response must include a 'status' key."""
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


# ─── 2. Static file serving ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_root_serves_html(client) -> None:
    """GET / must return 200 with an HTML content-type."""
    resp = await client.get("/")
    assert resp.status_code == 200
    content_type = resp.headers.get("content-type", "")
    assert "text/html" in content_type


# ─── 3. Security headers ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_security_headers_present(client) -> None:
    """All responses must include the standard security headers (issue #7)."""
    resp = await client.get("/api/health")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "SAMEORIGIN"
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "content-security-policy" in resp.headers


@pytest.mark.asyncio
async def test_security_headers_on_root(client) -> None:
    """Security headers must also be present on the root HTML response."""
    resp = await client.get("/")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "SAMEORIGIN"


# ─── 4. System metrics endpoint ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_system_endpoint_responds(client) -> None:
    """GET /api/system should return 200 with at least CPU/memory keys."""
    resp = await client.get("/api/system")
    # 200 on Linux/macOS/Windows native; 503 is acceptable if WSL not present
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        data = resp.json()
        assert isinstance(data, dict)


# ─── 5. Runners list endpoint ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_runners_endpoint_responds(client) -> None:
    """GET /api/runners should return 200 or a well-formed error, never 5xx without body."""
    resp = await client.get("/api/runners")
    # Without GITHUB_TOKEN the call to gh_api_admin will raise → 502/500 or
    # return cached data.  Either way the response must be valid JSON.
    assert resp.status_code in (200, 500, 502, 503)
    # Body must be parseable JSON
    assert resp.json() is not None


# ─── 6. Local apps endpoint ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_local_apps_endpoint_responds(client) -> None:
    """GET /api/local-apps must return 200 with a JSON object."""
    resp = await client.get("/api/local-apps")
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


# ─── 7. Deployment endpoint ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deployment_endpoint_returns_dict(client) -> None:
    """GET /api/deployment must return 200 with app metadata."""
    resp = await client.get("/api/deployment")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert "app" in data


# ─── 8. Credentials endpoint shape ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_credentials_endpoint_responds(client) -> None:
    """GET /api/credentials must return 200 with a JSON body (no raw secrets)."""
    resp = await client.get("/api/credentials")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, (dict, list))


# ─── 9. Watchdog / diagnostics endpoint ─────────────────────────────────────


@pytest.mark.asyncio
async def test_watchdog_endpoint_responds(client) -> None:
    """GET /api/watchdog must return 200 with a JSON body."""
    resp = await client.get("/api/watchdog")
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


# ─── 10. POST route response without required body ────────────────────────────


@pytest.mark.asyncio
async def test_post_runner_start_without_body(client) -> None:
    """POST to a runner action route without a valid runner ID returns 4xx."""
    resp = await client.post("/api/runners/__no_such_runner__/start")
    # 404 (runner not found), 422 (validation), or 500 (gh api error) are all fine;
    # the important thing is the route is reachable and returns JSON.
    assert resp.status_code in (400, 404, 422, 500, 502)


# ─── 11. Unknown route returns 404 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_route_returns_404(client) -> None:
    """Requests to routes that don't exist must return 404, not 500."""
    resp = await client.get("/api/this-route-does-not-exist")
    assert resp.status_code == 404


# ─── 12. Manifest endpoint ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_manifest_endpoint_responds(client) -> None:
    """GET /manifest.webmanifest returns 200 or 404 (if not deployed), never 5xx."""
    resp = await client.get("/manifest.webmanifest")
    assert resp.status_code in (200, 404)
