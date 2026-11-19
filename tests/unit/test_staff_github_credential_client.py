"""Credential refresh boundaries exercised without a live token or server."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from staff import github_credential_client as credentials

URL = "https://credential.invalid/refresh"


@pytest.fixture(autouse=True)
def configured_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_GITHUB_CREDENTIAL_URL", f" {URL} ")
    monkeypatch.setenv("FLEET_API_TOKEN", " fixture-run ")


def reply_with(monkeypatch: pytest.MonkeyPatch, body: Any, status: int = 200) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json=body)

    def post(url: str, **kwargs: Any) -> httpx.Response:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr(credentials.httpx, "post", post)
    return requests


def test_refresh_uses_bearer_xhr_timeout_and_strips_token(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = reply_with(monkeypatch, {"token": " fixture-token "})
    assert credentials.fetch_github_token_for_run() == "fixture-token"
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == URL
    assert request.method == "POST"
    assert request.headers["Authorization"] == "Bearer fixture-run"
    assert request.headers["X-Requested-With"] == "XMLHttpRequest"
    assert request.extensions["timeout"] == {"connect": 10.0, "read": 10.0, "write": 10.0, "pool": 10.0}


@pytest.mark.parametrize("variable", ["STAFF_GITHUB_CREDENTIAL_URL", "FLEET_API_TOKEN"])
@pytest.mark.parametrize("value", [None, "", " \t"])
def test_missing_configuration_fails_before_request(
    variable: str, value: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    if value is None:
        monkeypatch.delenv(variable)
    else:
        monkeypatch.setenv(variable, value)
    requests = reply_with(monkeypatch, {"token": "fixture-token"})
    with pytest.raises(RuntimeError, match="are required"):
        credentials.fetch_github_token_for_run()
    assert requests == []


@pytest.mark.parametrize("body", [{}, {"token": ""}, {"token": " \n"}])
def test_missing_token_is_an_explicit_error(body: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    reply_with(monkeypatch, body)
    with pytest.raises(RuntimeError, match="missing token"):
        credentials.fetch_github_token_for_run()


@pytest.mark.parametrize("token", [None, False, 123, [], {"value": "fixture-token"}])
def test_non_string_token_is_not_coerced_to_a_credential(token: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    reply_with(monkeypatch, {"token": token})
    with pytest.raises(RuntimeError, match="missing token"):
        credentials.fetch_github_token_for_run()


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_http_failure_is_not_masked_by_token_in_error_body(status: int, monkeypatch: pytest.MonkeyPatch) -> None:
    reply_with(monkeypatch, {"token": "fixture-token"}, status)
    with pytest.raises(httpx.HTTPStatusError) as exc:
        credentials.fetch_github_token_for_run()
    assert exc.value.response.status_code == status


def test_timeout_propagates_to_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = httpx.ReadTimeout("fixture timeout")

    def post(*args: Any, **kwargs: Any) -> httpx.Response:
        raise failure

    monkeypatch.setattr(credentials.httpx, "post", post)
    with pytest.raises(httpx.ReadTimeout) as exc:
        credentials.fetch_github_token_for_run()
    assert exc.value is failure
