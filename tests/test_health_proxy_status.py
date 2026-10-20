"""Health opts into successful upstream HTTP responses without forwarding credentials."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import proxy_utils
import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 401, 403, 500, 503])
async def test_health_proxy_requires_success_and_strips_caller_credentials(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    captured: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        # Even a healthy-shaped body cannot make an HTTP failure healthy.
        return httpx.Response(status, json={"status": "healthy", "github_api": "connected", "runners_registered": 23})

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(proxy_utils.httpx, "AsyncClient", lambda **_kwargs: client)
    monkeypatch.setattr(proxy_utils, "HUB_URL", "http://hub.invalid")
    monkeypatch.setenv("HUB_FLEET_TOKEN", "test-fleet-credential")  # pragma: allowlist secret
    reset = MagicMock()
    monkeypatch.setattr(proxy_utils, "reset_hub_circuit", reset)
    request = MagicMock()
    request.url.path = "/api/health"
    request.url.query = ""
    request.method = "GET"
    request.body = AsyncMock(return_value=b"")
    request.headers.items.return_value = [
        ("Authorization", "Bearer test-operator"),
        ("Cookie", "test-session=example"),
        ("X-API-Key", "test-api-credential"),
        ("X-CSRF-Token", "test-csrf"),
    ]

    if status == 200:
        body = await proxy_utils.proxy_to_hub(request, require_success=True)
        assert body["status"] == "healthy"
        reset.assert_called_once()
    else:
        with pytest.raises(HTTPException) as error:
            await proxy_utils.proxy_to_hub(request, require_success=True)
        assert error.value.status_code == 502
        reset.assert_not_called()

    assert len(captured) == 1
    assert captured[0].headers["authorization"] == "Bearer test-fleet-credential"
    for key in ("cookie", "x-api-key", "x-csrf-token"):
        assert key not in captured[0].headers
