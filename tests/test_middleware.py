"""Tests for backend/middleware.py — issue #386.

Acceptance criteria from the issue:
- CSRF blocks POST /api/foo without CSRF token.
- Webhook path is exempt from CSRF.
- Security headers present on every response.
- MaxBodySizeMiddleware rejects oversized Content-Length.
"""

from __future__ import annotations

import middleware as mw
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# csrf_check middleware
# ---------------------------------------------------------------------------


def _make_app_with_csrf() -> FastAPI:
    """Small FastAPI app with the csrf_check middleware applied."""
    app = FastAPI()
    app.middleware("http")(mw.csrf_check)

    @app.post("/api/data")
    async def post_data():
        return {"ok": True}

    @app.post("/api/linear/webhook")
    async def webhook():
        return {"ok": True}

    @app.get("/api/data")
    async def get_data():
        return {"ok": True}

    return app


def test_csrf_blocks_post_without_header() -> None:
    """POST to /api/* without X-Requested-With must return 403."""
    app = _make_app_with_csrf()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/api/data", json={})
    assert resp.status_code == 403
    assert "CSRF" in resp.json().get("error", "")


def test_csrf_allows_post_with_header() -> None:
    """POST to /api/* with X-Requested-With: XMLHttpRequest must pass."""
    app = _make_app_with_csrf()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/api/data", json={}, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200


def test_csrf_webhook_exempt() -> None:
    """Webhook path must be exempt from CSRF enforcement."""
    app = _make_app_with_csrf()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/api/linear/webhook", json={})
    # Webhook should NOT be blocked by CSRF (status != 403)
    assert resp.status_code != 403


def test_csrf_get_not_blocked() -> None:
    """GET requests must never be blocked by CSRF."""
    app = _make_app_with_csrf()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/data")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# add_security_headers middleware
# ---------------------------------------------------------------------------


def _make_app_with_security_headers() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(mw.add_security_headers)

    @app.get("/api/anything")
    async def handler():
        return {"ok": True}

    @app.post("/api/anything")
    async def post_handler():
        return {"ok": True}

    return app


def test_security_headers_present_on_get() -> None:
    app = _make_app_with_security_headers()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/anything")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "Content-Security-Policy" in resp.headers


def test_security_headers_csp_has_default_src() -> None:
    app = _make_app_with_security_headers()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/anything")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "default-src" in csp


def test_security_headers_csp_allows_static_vite_entrypoint() -> None:
    app = _make_app_with_security_headers()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/anything")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "script-src 'self';" in csp
    assert "strict-dynamic" not in csp


def test_security_headers_present_on_post() -> None:
    """Security headers must be injected on POST responses too."""
    app = _make_app_with_security_headers()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/api/anything", json={})
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"


def test_hsts_absent_in_http_mode(monkeypatch) -> None:
    """Issue #930: no HSTS header in the default plain-HTTP deployment mode."""
    import dashboard_config

    monkeypatch.setattr(dashboard_config, "TLS_ENABLED", False)
    app = _make_app_with_security_headers()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/anything")
    assert "Strict-Transport-Security" not in resp.headers


def test_hsts_present_in_tls_mode(monkeypatch) -> None:
    """Issue #930: HSTS is sent only when DASHBOARD_TLS is enabled."""
    import dashboard_config

    monkeypatch.setattr(dashboard_config, "TLS_ENABLED", True)
    app = _make_app_with_security_headers()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/anything")
    assert resp.headers.get("Strict-Transport-Security") == "max-age=31536000; includeSubDomains"


# ---------------------------------------------------------------------------
# MaxBodySizeMiddleware
# ---------------------------------------------------------------------------


def _make_app_with_max_body() -> FastAPI:
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def endpoint(request):
        return JSONResponse({"ok": True})

    app = Starlette(
        routes=[Route("/api/data", endpoint, methods=["POST"])],
        middleware=[Middleware(mw.MaxBodySizeMiddleware, default_limit=100)],
    )
    return app  # type: ignore[return-value]


def test_max_body_size_middleware_blocks_oversized() -> None:
    app = _make_app_with_max_body()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/data",
        content=b"x" * 200,
        headers={"Content-Length": "200", "Content-Type": "application/octet-stream"},
    )
    assert resp.status_code == 413


def test_max_body_size_middleware_allows_within_limit() -> None:
    app = _make_app_with_max_body()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/data",
        content=b"x" * 50,
        headers={"Content-Length": "50", "Content-Type": "application/octet-stream"},
    )
    assert resp.status_code == 200


def test_max_body_size_webhook_smaller_limit() -> None:
    """Webhook path uses the smaller 256 KB override."""
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def webhook(request):
        return JSONResponse({"ok": True})

    app = Starlette(
        routes=[Route("/api/linear/webhook", webhook, methods=["POST"])],
        middleware=[Middleware(mw.MaxBodySizeMiddleware, default_limit=1024 * 1024)],
    )
    client = TestClient(app, raise_server_exceptions=False)
    # Simulate a request larger than 256 KB
    big = 300 * 1024
    resp = client.post(
        "/api/linear/webhook",
        content=b"x" * big,
        headers={"Content-Length": str(big), "Content-Type": "application/octet-stream"},
    )
    assert resp.status_code == 413


# ---------------------------------------------------------------------------
# max_body_size_check (FastAPI middleware function)
# ---------------------------------------------------------------------------


def _make_app_with_body_check() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(mw.max_body_size_check)

    @app.post("/api/small")
    async def small():
        return {"ok": True}

    return app


def test_body_check_allows_within_default() -> None:
    app = _make_app_with_body_check()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/small",
        content=b"x" * 100,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert resp.status_code == 200


def test_body_check_blocks_over_default() -> None:
    app = _make_app_with_body_check()
    client = TestClient(app, raise_server_exceptions=False)
    big = mw.DEFAULT_MAX_BODY_SIZE + 1
    resp = client.post(
        "/api/small",
        content=b"x" * big,
        headers={
            "Content-Length": str(big),
            "Content-Type": "application/octet-stream",
        },
    )
    assert resp.status_code == 413


def test_body_check_skips_get() -> None:
    app = _make_app_with_body_check()

    @app.get("/api/get-endpoint")
    async def get_handler():
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/get-endpoint")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# limit_body_size decorator
# ---------------------------------------------------------------------------


def test_limit_body_size_sets_attribute() -> None:
    @mw.limit_body_size(512)
    async def handler():
        pass

    assert handler.__max_body_size__ == 512


def test_max_body_size_decorator_sets_attribute() -> None:
    @mw.max_body_size(1024)
    async def handler():
        pass

    assert handler._max_body_size == 1024
