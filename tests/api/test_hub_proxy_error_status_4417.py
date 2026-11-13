"""A hub error answer is never relayed to a node's caller as HTTP 200 (#4417)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import dashboard_config
import httpx
import proxy_utils
import pytest
from fastapi import HTTPException


def _node_request(path: str = "/api/runners", method: str = "GET") -> MagicMock:
    request = MagicMock()
    request.url.path = path
    request.url.query = ""
    request.method = method
    request.body = AsyncMock(return_value=b"")
    request.headers.items.return_value = [("Accept", "application/json")]
    return request


def _hub_answers(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> MagicMock:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: response))
    monkeypatch.setattr(proxy_utils.httpx, "AsyncClient", lambda **_kwargs: client)
    monkeypatch.setattr(dashboard_config, "HUB_URL", "http://hub.invalid")
    reset = MagicMock()
    monkeypatch.setattr(proxy_utils, "reset_hub_circuit", reset)
    return reset


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_hub_rejecting_the_node_credential_is_a_gateway_failure(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """The hub refused the node's fleet token; the browser user is not logged out."""
    _hub_answers(monkeypatch, httpx.Response(status, json={"detail": "Not authenticated"}))

    with pytest.raises(HTTPException) as error:
        await proxy_utils.proxy_to_hub(_node_request())

    assert error.value.status_code == 502
    assert str(status) in error.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 404, 409, 422, 429])
async def test_hub_client_error_keeps_its_status_and_detail(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    _hub_answers(monkeypatch, httpx.Response(status, json={"detail": "run already cancelled"}))

    with pytest.raises(HTTPException) as error:
        await proxy_utils.proxy_to_hub(_node_request("/api/queue/cancel", "POST"))

    assert error.value.status_code == status
    assert error.value.detail == "run already cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503])
async def test_hub_server_error_becomes_bad_gateway(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    _hub_answers(monkeypatch, httpx.Response(status, json={"detail": "boom", "trace": "internal"}))

    with pytest.raises(HTTPException) as error:
        await proxy_utils.proxy_to_hub(_node_request())

    assert error.value.status_code == 502
    assert str(status) in error.value.detail
    assert "internal" not in error.value.detail


@pytest.mark.asyncio
async def test_hub_error_without_a_detail_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _hub_answers(monkeypatch, httpx.Response(409, json=["not", "an", "object"]))

    with pytest.raises(HTTPException) as error:
        await proxy_utils.proxy_to_hub(_node_request())

    assert error.value.status_code == 409
    assert "409" in error.value.detail


@pytest.mark.asyncio
async def test_hub_structured_detail_is_relayed_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """FastAPI validation errors carry a list in ``detail``; the caller still gets it."""
    detail = [{"loc": ["body", "repo"], "msg": "field required", "type": "missing"}]
    _hub_answers(monkeypatch, httpx.Response(422, json={"detail": detail}))

    with pytest.raises(HTTPException) as error:
        await proxy_utils.proxy_to_hub(_node_request("/api/queue/cancel", "POST"))

    assert error.value.detail == detail


@pytest.mark.asyncio
async def test_hub_that_answered_with_an_error_is_still_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """An HTTP error is an answer: the circuit breaker closes, as it did before."""
    reset = _hub_answers(monkeypatch, httpx.Response(404, json={"detail": "no such runner"}))

    with pytest.raises(HTTPException):
        await proxy_utils.proxy_to_hub(_node_request())

    reset.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 201, 202])
async def test_hub_success_body_is_returned_unchanged(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    _hub_answers(monkeypatch, httpx.Response(status, json={"runners": [{"id": 1}], "detail": "kept"}))

    assert await proxy_utils.proxy_to_hub(_node_request()) == {"runners": [{"id": 1}], "detail": "kept"}


@pytest.mark.asyncio
async def test_hub_no_content_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _hub_answers(monkeypatch, httpx.Response(204))

    assert await proxy_utils.proxy_to_hub(_node_request()) == {"status": "no_content"}
