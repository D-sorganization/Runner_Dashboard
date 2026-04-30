"""Unit tests for backend/proxy_utils.py (issue #155)."""

from __future__ import annotations

import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import proxy_utils  # noqa: E402


def _make_request(query_params: dict[str, str]) -> MagicMock:
    """Return a mocked FastAPI Request with the given query_params."""
    mock = MagicMock()
    mock.query_params.get = lambda key, default="": query_params.get(key, default)
    return mock


def _make_proxy_request(path: str = "/api/fleet/nodes") -> MagicMock:
    """Return a mocked FastAPI Request suitable for proxy_to_hub."""
    mock = MagicMock()
    mock.method = "GET"
    mock.url = SimpleNamespace(path=path, query="")
    mock.headers = {"x-request-id": "req-123"}
    mock.body = AsyncMock(return_value=b"")
    return mock


def test_translate_upstream_response_returns_no_content_status() -> None:
    resp = httpx.Response(204, request=httpx.Request("GET", "http://hub.internal/api/fleet/nodes"))

    assert proxy_utils.translate_upstream_response(
        resp,
        upstream="hub",
        request_id="req-123",
        target_url="http://hub.internal/api/fleet/nodes",
    ) == {"status": "no_content"}


def test_translate_upstream_response_rejects_non_json_2xx() -> None:
    resp = httpx.Response(
        200,
        text="<html>ok</html>",
        headers={"content-type": "text/html"},
        request=httpx.Request("GET", "http://hub.internal/api/fleet/nodes"),
    )

    with pytest.raises(HTTPException) as exc_info:
        proxy_utils.translate_upstream_response(
            resp,
            upstream="hub",
            request_id="req-123",
            target_url="http://hub.internal/api/fleet/nodes",
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == {"upstream": "hub", "status": 200, "detail": "upstream returned non-JSON"}


def test_translate_upstream_response_preserves_maxwell_html_502() -> None:
    resp = httpx.Response(
        502,
        text="<html>oom</html>",
        headers={"content-type": "text/html"},
        request=httpx.Request("GET", "http://maxwell.internal/api/version"),
    )

    with pytest.raises(HTTPException) as exc_info:
        proxy_utils.translate_upstream_response(
            resp,
            upstream="maxwell",
            request_id="req-123",
            target_url="http://maxwell.internal/api/version",
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["upstream"] == "maxwell"
    assert exc_info.value.detail["status"] == 502


@pytest.mark.asyncio
async def test_proxy_to_hub_exposes_html_502_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(502, text="<html>oom</html>", headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: async_client(transport=transport, **kwargs))
    with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
        with pytest.raises(HTTPException) as exc_info:
            await proxy_utils.proxy_to_hub(_make_proxy_request("/api/maxwell/version"))

    assert len(requests) == 1
    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == {"upstream": "hub", "status": 502, "detail": "upstream returned non-JSON"}


class TestShouldProxyFleetToHub:
    def test_not_node_role_returns_false(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "hub"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_no_hub_url_returns_false(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", None):
                req = _make_request({})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_node_with_hub_no_local_params_returns_true(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is True

    def test_local_param_true_returns_false(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"local": "true"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_local_param_yes_returns_false(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"local": "yes"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_local_param_1_returns_false(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"local": "1"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_local_param_local_returns_false(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"local": "local"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_local_param_false_returns_true(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"local": "false"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is True

    def test_local_param_0_returns_true(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"local": "0"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is True

    def test_scope_local_returns_false(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"scope": "local"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_scope_fleet_returns_true(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"scope": "fleet"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is True

    def test_local_case_insensitive(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"local": "TRUE"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_scope_case_insensitive(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"scope": "LOCAL"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_multiple_params_local_wins(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"local": "true", "scope": "fleet"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_multiple_params_scope_wins(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", "http://hub.internal"):
                req = _make_request({"local": "false", "scope": "local"})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False

    def test_empty_hub_url_string_not_configured(self) -> None:
        with patch.object(proxy_utils, "MACHINE_ROLE", "node"):
            with patch.object(proxy_utils, "HUB_URL", ""):
                req = _make_request({})
                assert proxy_utils.should_proxy_fleet_to_hub(req) is False
