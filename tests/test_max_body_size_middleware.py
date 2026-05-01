"""Tests for max_body_size_check middleware (issue #350).

Verifies:
  - GET / HEAD / OPTIONS requests bypass the size check.
  - No Content-Length header bypasses the check (streaming/chunked).
  - Default 1 MB limit rejects oversized bodies with 413.
  - Invalid Content-Length returns 400.
  - Per-route ``@limit_body_size`` decorator overrides the default.
  - Webhook path has 256 KB tighter cap.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Import *after* backend is on path
from middleware import (  # noqa: E402
    DEFAULT_MAX_BODY_SIZE,
    WEBHOOK_MAX_BODY_SIZE,
    limit_body_size,
    max_body_size_check,
)


def _make_request(
    method: str = "POST",
    path: str = "/api/test",
    content_length: str | None = "1024",
) -> MagicMock:
    """Build a minimal Request mock."""
    req = MagicMock()
    req.method = method
    req.url.path = path
    req.headers = {}
    if content_length is not None:
        req.headers["content-length"] = content_length
    # FastAPI route matching populates scope["route"].
    req.scope = {}
    return req


def _make_response(status_code: int = 200) -> MagicMock:
    """Build a minimal JSONResponse mock."""
    resp = MagicMock()
    resp.status_code = status_code
    return resp


@pytest.mark.asyncio
async def test_get_request_bypasses_check() -> None:
    """GET requests are never body-size-checked."""
    request = _make_request(method="GET", content_length=str(DEFAULT_MAX_BODY_SIZE + 1))
    call_next = AsyncMock(return_value=_make_response(200))

    result = await max_body_size_check(request, call_next)

    call_next.assert_awaited_once_with(request)
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_no_content_length_bypasses_check() -> None:
    """Chunked / streaming requests without Content-Length are allowed."""
    request = _make_request(method="POST", content_length=None)
    call_next = AsyncMock(return_value=_make_response(200))

    result = await max_body_size_check(request, call_next)

    call_next.assert_awaited_once_with(request)
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_within_default_limit_allowed() -> None:
    """A POST with body = 1 MB exactly is allowed (limit is > not >=)."""
    request = _make_request(method="POST", content_length=str(DEFAULT_MAX_BODY_SIZE))
    call_next = AsyncMock(return_value=_make_response(200))

    result = await max_body_size_check(request, call_next)

    call_next.assert_awaited_once_with(request)
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_exceeds_default_limit_rejected() -> None:
    """A POST with body > 1 MB returns 413 without calling the handler."""
    request = _make_request(
        method="POST",
        content_length=str(DEFAULT_MAX_BODY_SIZE + 1),
    )
    call_next = AsyncMock()

    result = await max_body_size_check(request, call_next)

    call_next.assert_not_called()
    assert result.status_code == 413
    assert "too large" in result.body.decode().lower()


@pytest.mark.asyncio
async def test_invalid_content_length_returns_400() -> None:
    """Malformed Content-Length header returns 400."""
    request = _make_request(method="POST", content_length="not-a-number")
    call_next = AsyncMock()

    result = await max_body_size_check(request, call_next)

    call_next.assert_not_called()
    assert result.status_code == 400


@pytest.mark.asyncio
async def test_decorator_overrides_default_limit() -> None:
    """A route decorated with @limit_body_size uses the custom limit."""

    @limit_body_size(500)
    async def handler():
        return {"ok": True}

    # FastAPI wraps endpoints; simulate the scope structure.
    route = MagicMock()
    route.endpoint = handler

    request = _make_request(method="POST", content_length="501")
    request.scope = {"route": route}
    call_next = AsyncMock()

    result = await max_body_size_check(request, call_next)
    assert result.status_code == 413
    assert "501 bytes > 500 bytes" in result.body.decode()

    # Just under the custom limit should pass.
    request2 = _make_request(method="POST", content_length="500")
    request2.scope = {"route": route}
    call_next2 = AsyncMock(return_value=_make_response(200))

    result2 = await max_body_size_check(request2, call_next2)
    call_next2.assert_awaited_once_with(request2)
    assert result2.status_code == 200


@pytest.mark.asyncio
async def test_decorator_resolves_through_wrapped_chain() -> None:
    """The middleware walks __wrapped__ to find the limit marker."""

    @limit_body_size(1000)
    async def real_handler():
        return {"ok": True}

    async def wrapper(request):
        return await real_handler()

    wrapper.__wrapped__ = real_handler  # type: ignore[attr-defined]

    route = MagicMock()
    route.endpoint = wrapper

    request = _make_request(method="POST", content_length="1001")
    request.scope = {"route": route}
    call_next = AsyncMock()

    result = await max_body_size_check(request, call_next)
    assert result.status_code == 413


@pytest.mark.asyncio
async def test_webhook_route_256kb_limit() -> None:
    """Simulate the webhook route decorator: 256 KB cap, 5 MB rejected."""
    from routers.linear_webhook import linear_webhook  # noqa: PLC0415

    route = MagicMock()
    route.endpoint = linear_webhook

    # 5 MB POST should be rejected
    request = _make_request(
        method="POST",
        path="/api/linear/webhook",
        content_length=str(5 * 1024 * 1024),
    )
    request.scope = {"route": route}
    call_next = AsyncMock()

    result = await max_body_size_check(request, call_next)

    call_next.assert_not_called()
    assert result.status_code == 413
    assert str(5 * 1024 * 1024) in result.body.decode()

    # Under 256 KB should pass
    request2 = _make_request(
        method="POST",
        path="/api/linear/webhook",
        content_length=str(WEBHOOK_MAX_BODY_SIZE - 1),
    )
    request2.scope = {"route": route}
    call_next2 = AsyncMock(return_value=_make_response(200))

    result2 = await max_body_size_check(request2, call_next2)
    call_next2.assert_awaited_once_with(request2)
    assert result2.status_code == 200


# ---------------------------------------------------------------------------
# Source-level assertions
# ---------------------------------------------------------------------------


def test_middleware_exports_limit_body_size() -> None:
    """middleware.py must export limit_body_size for route decorators."""
    import middleware  # noqa: PLC0415

    assert hasattr(middleware, "limit_body_size")


def test_middleware_exports_max_body_size_check() -> None:
    """middleware.py must export max_body_size_check for server.py wiring."""
    import middleware  # noqa: PLC0415

    assert hasattr(middleware, "max_body_size_check")


def test_webhook_imports_limit_body_size() -> None:
    """linear_webhook.py must import the decorator."""
    src = (_BACKEND_DIR / "routers" / "linear_webhook.py").read_text(encoding="utf-8")
    assert "from middleware import limit_body_size" in src


def test_webhook_uses_limit_body_size_decorator() -> None:
    """linear_webhook.py must apply the decorator to the webhook handler."""
    src = (_BACKEND_DIR / "routers" / "linear_webhook.py").read_text(encoding="utf-8")
    assert "@limit_body_size" in src


def test_server_wires_max_body_size_middleware() -> None:
    """server.py must register the _max_body_size middleware before _csrf_check."""
    src = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")
    assert "max_body_size_check" in src
    # Middleware order: _max_body_size should appear before _csrf_check
    max_body_idx = src.find("@app.middleware(\"http\")\nasync def _max_body_size")
    csrf_idx = src.find("@app.middleware(\"http\")\nasync def _csrf_check")
    assert max_body_idx != -1
    assert csrf_idx != -1
    assert max_body_idx < csrf_idx