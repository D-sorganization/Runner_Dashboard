"""Response compression contract (issue #2990).

Static assets and the SPA shell were served uncompressed, costing ~600 KiB per
cold load on a mobile link. The app now installs Starlette's GZipMiddleware.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

import server  # noqa: E402

_BIG_SHELL = '<!doctype html><html><body><div id="root"></div>' + ("<!-- pad -->" * 400) + "</body></html>"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    (tmp_path / "index.html").write_text(_BIG_SHELL, encoding="utf-8")
    monkeypatch.setattr(server, "FRONTEND_DIR", tmp_path)
    return TestClient(server.app, raise_server_exceptions=False)


@pytest.mark.unit
def test_gzip_middleware_installed() -> None:
    assert any(m.cls is GZipMiddleware for m in server.app.user_middleware)


@pytest.mark.unit
def test_html_shell_gzipped_when_accepted(client: TestClient) -> None:
    resp = client.get("/t/queue", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"
    assert resp.text == _BIG_SHELL  # client transparently decodes


@pytest.mark.unit
def test_html_shell_plain_when_gzip_not_accepted(client: TestClient) -> None:
    resp = client.get("/t/queue", headers={"Accept-Encoding": "identity"})
    assert resp.status_code == 200
    assert "content-encoding" not in resp.headers
