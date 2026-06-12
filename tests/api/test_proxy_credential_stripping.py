"""Hub-proxy credential-stripping regression tests (issue #923, re-opens #347).

There used to be TWO proxy_to_hub implementations:
  - proxy_utils.proxy_to_hub — strips Authorization/Cookie/X-API-Key/X-CSRF-Token
    and injects the intra-fleet HUB_FLEET_TOKEN.
  - server.proxy_to_hub — forwarded ALL caller headers except host/content-length,
    laundering operator credentials to the hub. This dead-named twin was the one
    dependency-injected into the deployment / orchestration routers.

This module asserts:
  - There is exactly one proxy implementation: server.proxy_to_hub IS
    proxy_utils.proxy_to_hub (and the shouldproxy predicate likewise), so every
    DI consumer (deployment, orchestration, orchestration_node_routes, fleet)
    uses the header-stripping version.
  - The header builder strips every sensitive header and injects HUB_FLEET_TOKEN.
  - An end-to-end proxy call against a fake upstream never leaks
    Authorization/Cookie/X-API-Key/X-CSRF-Token, and carries the fleet token.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

import proxy_utils  # noqa: E402

_SENSITIVE = ("authorization", "cookie", "x-api-key", "x-csrf-token")


# ── Exactly one implementation (server re-exports proxy_utils) ────────────────


def test_server_proxy_to_hub_is_proxy_utils_implementation() -> None:
    import server  # noqa: PLC0415

    assert server.proxy_to_hub is proxy_utils.proxy_to_hub, (
        "server.proxy_to_hub must be the header-stripping proxy_utils implementation (#923)"
    )
    assert server._should_proxy_fleet_to_hub is proxy_utils.should_proxy_fleet_to_hub


def test_no_second_proxy_definition_in_server_source() -> None:
    """server.py must not define its own proxy_to_hub body (only the re-export)."""
    src = (_BACKEND / "server.py").read_text(encoding="utf-8")
    assert "async def proxy_to_hub" not in src, "server.py must not define a second proxy_to_hub (#923)"
    # The credential-leaking signature must be gone.
    assert 'if k.lower() not in ("host", "content-length")' not in src


def test_di_consumers_receive_stripping_proxy() -> None:
    """The deployment / orchestration routers must be wired with the
    header-stripping proxy (verified via the configured callables)."""
    import server  # noqa: PLC0415
    from routers import deployment, orchestration, orchestration_node_routes

    for mod in (deployment, orchestration, orchestration_node_routes):
        assert mod._proxy_to_hub is proxy_utils.proxy_to_hub, (
            f"{mod.__name__} must use proxy_utils.proxy_to_hub, not a credential-forwarding copy"
        )
    # And the server-level names resolve to the same object.
    assert server.proxy_to_hub is proxy_utils.proxy_to_hub


# ── Header builder strips secrets and injects the fleet token ─────────────────


def _request_with_headers(headers: dict[str, str]) -> MagicMock:
    req = MagicMock()
    req.headers.items = lambda: list(headers.items())
    return req


def test_safe_forward_headers_strips_all_sensitive_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-secret-token")  # pragma: allowlist secret
    req = _request_with_headers(
        {
            "Authorization": "Bearer operator-session",
            "Cookie": "dashboard_session=abc",
            "X-API-Key": "operator-key",
            "X-CSRF-Token": "csrf123",
            "X-Request-Id": "keepme",
            "Accept": "application/json",
        }
    )
    forwarded = proxy_utils._safe_forward_headers(req)
    lowered = {k.lower(): v for k, v in forwarded.items()}

    # The fleet token replaces Authorization; no operator credential survives.
    assert lowered.get("authorization") == "Bearer fleet-secret-token"
    assert "cookie" not in lowered
    assert "x-api-key" not in lowered
    assert "x-csrf-token" not in lowered
    # Non-sensitive headers pass through.
    assert lowered.get("x-request-id") == "keepme"
    assert lowered.get("accept") == "application/json"


def test_safe_forward_headers_no_token_drops_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no HUB_FLEET_TOKEN configured, the caller's Authorization must still
    be stripped (never forwarded), and no Authorization is injected."""
    monkeypatch.delenv("HUB_FLEET_TOKEN", raising=False)
    req = _request_with_headers({"Authorization": "Bearer operator-session", "Cookie": "x=y"})
    forwarded = proxy_utils._safe_forward_headers(req)
    lowered = {k.lower(): v for k, v in forwarded.items()}
    assert "authorization" not in lowered
    assert "cookie" not in lowered


# ── End-to-end: a real proxy_to_hub call never leaks credentials ──────────────


@pytest.mark.asyncio
async def test_proxy_to_hub_does_not_leak_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-secret-token")  # pragma: allowlist secret
    monkeypatch.setattr(proxy_utils, "HUB_URL", "http://hub.internal")

    captured: dict[str, dict[str, str]] = {}

    class _FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self) -> dict:
            return {"ok": True}

    class _FakeClient:
        def __init__(self, *a, **k) -> None:  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *a) -> None:  # noqa: ANN002
            return None

        def build_request(self, method, url, headers, content):  # noqa: ANN001
            captured["headers"] = dict(headers)
            return MagicMock()

        async def send(self, req):  # noqa: ANN001
            return _FakeResponse()

    req = MagicMock()
    req.url.path = "/api/fleet/status"
    req.url.query = ""
    req.method = "GET"
    req.body = AsyncMock(return_value=b"")
    req.headers.items = lambda: [
        ("Authorization", "Bearer operator-session"),
        ("Cookie", "dashboard_session=abc"),
        ("X-API-Key", "operator-key"),
        ("X-CSRF-Token", "csrf123"),
    ]

    with patch.object(proxy_utils.httpx, "AsyncClient", _FakeClient):
        result = await proxy_utils.proxy_to_hub(req)

    assert result == {"ok": True}
    sent = {k.lower(): v for k, v in captured["headers"].items()}
    # The only Authorization is the fleet token; no operator credential leaked.
    assert sent.get("authorization") == "Bearer fleet-secret-token"
    for h in ("cookie", "x-api-key", "x-csrf-token"):
        assert h not in sent, f"{h} must never reach the hub (#347/#923)"
