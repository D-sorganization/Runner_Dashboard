"""Tests for the SPA deep-link fallback route in backend/server.py.

Background: the frontend (frontend/src/main.tsx) uses React Router with
bookmarkable client-side routes — "/" (Fleet), "/t/:tabId" for every nav tab,
and "/settings/push". Before the catch-all was added, a cold HTTP GET to one of
those paths had no matching backend route and fell through to FastAPI's default
404, so shared links and bookmarks broke on first load (they only worked after
landing on "/" and navigating client-side). The service worker doesn't paper
over it either — frontend/public/sw.js serves navigations network-first then
falls back to OFFLINE_URL, not index.html.

This module pins the contract that:
  - client routes (/t/queue, /settings/push) return 200 text/html SPA shell;
  - real API routes (/api/health) still return their JSON;
  - unknown /api/* paths still 404 instead of being masked by the HTML shell.

Marked `unit` so the conftest network-block fixture is active: /api/health
catches its (refused) GitHub call and returns a degraded JSON body, keeping the
test fast and offline.

The SPA-shell assertions point ``server.FRONTEND_DIR`` at a tmp dir holding a
minimal ``index.html``. The real ``dist/`` is a gitignored Vite build artifact
that is absent in fresh checkouts and the CI test job (which runs pytest before
any ``vite build``), so the test must not depend on it existing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

import server  # noqa: E402

# Marker the SPA shell mounts React onto; the fallback must serve a doc with it.
_SHELL_HTML = '<!doctype html><html><body><div id="root"></div></body></html>'


@pytest.fixture
def client() -> TestClient:
    """TestClient for the FastAPI app (deep-link routes need no auth)."""
    return TestClient(server.app, raise_server_exceptions=False)


@pytest.fixture
def fake_frontend_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the fallback at a tmp dir with a minimal SPA shell so the test is
    independent of whether ``dist/`` has been built."""
    (tmp_path / "index.html").write_text(_SHELL_HTML, encoding="utf-8")
    monkeypatch.setattr(server, "FRONTEND_DIR", tmp_path)
    return tmp_path


@pytest.mark.unit
@pytest.mark.usefixtures("fake_frontend_dir")
@pytest.mark.parametrize("route", ["/t/queue", "/settings/push"])
def test_client_route_serves_spa_shell(client: TestClient, route: str) -> None:
    """A cold GET to a React Router client route returns the SPA shell so
    deep-link bookmarks load on first request."""
    resp = client.get(route)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    # The Vite SPA shell mounts React onto <div id="root">.
    assert 'id="root"' in resp.text


@pytest.mark.unit
def test_api_health_still_returns_json(client: TestClient) -> None:
    """The catch-all must not shadow real /api/* routes: /api/health still
    answers with JSON, not the HTML shell."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    assert "status" in resp.json()


@pytest.mark.unit
def test_unknown_api_path_still_404s(client: TestClient) -> None:
    """An unknown /api/* path must 404 (excluded from the fallback), not be
    masked by a 200 HTML shell."""
    resp = client.get("/api/this-endpoint-does-not-exist")
    assert resp.status_code == 404
    assert "text/html" not in resp.headers.get("content-type", "")
