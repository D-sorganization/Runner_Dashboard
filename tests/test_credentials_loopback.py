"""Tests for credentials endpoint loopback bypass fix (issue #321)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def test_require_local_rejects_x_forwarded_for(mock_auth) -> None:  # noqa: ARG001
    """Requests with X-Forwarded-For header must be rejected even from loopback."""
    import server  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    client = TestClient(server.app, headers={"X-Requested-With": "XMLHttpRequest"})
    response = client.get(
        "/api/credentials",
        headers={"X-Forwarded-For": "127.0.0.1"},
    )
    assert response.status_code == 403


def test_require_local_rejects_x_real_ip(mock_auth) -> None:  # noqa: ARG001
    """Requests with X-Real-IP header must be rejected (issue #321)."""
    import server  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    client = TestClient(server.app, headers={"X-Requested-With": "XMLHttpRequest"})
    response = client.get(
        "/api/credentials",
        headers={"X-Real-IP": "127.0.0.1"},
    )
    assert response.status_code == 403


def test_require_local_rejects_forwarded_header(mock_auth) -> None:  # noqa: ARG001
    """Requests with RFC7239 Forwarded header must be rejected (issue #321)."""
    import server  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    client = TestClient(server.app, headers={"X-Requested-With": "XMLHttpRequest"})
    response = client.get(
        "/api/credentials",
        headers={"Forwarded": "for=127.0.0.1"},
    )
    assert response.status_code == 403


def _make_mock_request(host: str, headers: dict[str, str]):
    """Build a minimal mock request object for _require_local_request tests."""
    from unittest.mock import MagicMock  # noqa: PLC0415

    # Use a case-insensitive-like mapping via MagicMock so .get() works for
    # the lowercase keys used in _PROXY_HEADERS.
    lowered = {k.lower(): v for k, v in headers.items()}

    req = MagicMock()
    req.client.host = host
    req.headers.get = lambda key, default=None: lowered.get(key.lower(), default)
    return req


def test_require_local_request_function_rejects_proxy_headers() -> None:
    """Unit test for _require_local_request: proxy headers are rejected."""
    from fastapi import HTTPException  # noqa: PLC0415
    from routers.credentials import _require_local_request  # noqa: PLC0415

    req = _make_mock_request("127.0.0.1", {"X-Forwarded-For": "10.0.0.1"})

    with pytest.raises(HTTPException) as exc_info:
        _require_local_request(req)

    assert exc_info.value.status_code == 403
    assert "proxy headers" in exc_info.value.detail


def test_require_local_request_function_passes_clean_local() -> None:
    """Unit test for _require_local_request: clean loopback request passes."""
    from routers.credentials import _require_local_request  # noqa: PLC0415

    req = _make_mock_request("127.0.0.1", {})
    # Should not raise
    _require_local_request(req)


def test_require_local_request_function_rejects_non_local() -> None:
    """Unit test: non-loopback client is rejected."""
    from fastapi import HTTPException  # noqa: PLC0415
    from routers.credentials import _require_local_request  # noqa: PLC0415

    req = _make_mock_request("192.168.1.5", {})

    with pytest.raises(HTTPException) as exc_info:
        _require_local_request(req)

    assert exc_info.value.status_code == 403
