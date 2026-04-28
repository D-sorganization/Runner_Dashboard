from __future__ import annotations

from typing import Any

import httpx
import pytest
from linear_client import ApiKeyAuthProvider, LinearAPIError, LinearClient


class StaticAuthProvider:
    def __init__(self, authorization: str = "lin_api_test") -> None:
        self.authorization = authorization

    async def get_authorization(self, workspace_id: str) -> str:
        return self.authorization


class FakeAsyncClient:
    def __init__(self, responses: list[httpx.Response] | None = None) -> None:
        self.responses = list(responses or [])
        self.requests: list[dict[str, Any]] = []
        self.closed = False

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        self.requests.append({"url": url, "json": json, "headers": headers})
        if not self.responses:
            raise AssertionError("No fake response queued")
        return self.responses.pop(0)

    async def aclose(self) -> None:
        self.closed = True


def json_response(payload: dict[str, Any], status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


@pytest.mark.asyncio
async def test_api_key_auth_provider_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_secret")

    provider = ApiKeyAuthProvider("LINEAR_API_KEY")

    assert await provider.get_authorization("default") == "lin_api_secret"


@pytest.mark.asyncio
async def test_api_key_auth_provider_missing_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)

    provider = ApiKeyAuthProvider("LINEAR_API_KEY")

    with pytest.raises(LinearAPIError) as exc_info:
        await provider.get_authorization("default")

    assert exc_info.value.workspace_id == "default"


@pytest.mark.asyncio
async def test_api_key_auth_provider_per_workspace_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_default")
    monkeypatch.setenv("LINEAR_ENG_API_KEY", "lin_api_eng")
    provider = ApiKeyAuthProvider("LINEAR_API_KEY", {"eng": "LINEAR_ENG_API_KEY"})

    assert await provider.get_authorization("eng") == "lin_api_eng"
    assert await provider.get_authorization("ops") == "lin_api_default"


@pytest.mark.asyncio
async def test_fetch_issues_sends_correct_authorization_header() -> None:
    http_client = FakeAsyncClient(
        [
            json_response(
                {
                    "data": {
                        "issues": {
                            "nodes": [{"identifier": "ENG-1"}],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            )
        ]
    )
    client = LinearClient(StaticAuthProvider("lin_api_raw"), http_client=http_client)  # type: ignore[arg-type]

    issues = await client.fetch_issues("workspace")

    assert issues == [{"identifier": "ENG-1"}]
    assert http_client.requests[0]["headers"]["Authorization"] == "lin_api_raw"


@pytest.mark.asyncio
async def test_fetch_issues_passes_team_filter() -> None:
    http_client = FakeAsyncClient(
        [
            json_response(
                {
                    "data": {
                        "issues": {
                            "nodes": [],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            )
        ]
    )
    client = LinearClient(StaticAuthProvider(), http_client=http_client)  # type: ignore[arg-type]

    await client.fetch_issues(
        "workspace",
        team_keys=["ENG", "OPS"],
        state_types=["started"],
        updated_after="2026-04-28T00:00:00Z",
    )

    assert http_client.requests[0]["json"]["variables"]["filter"] == {
        "team": {"key": {"in": ["ENG", "OPS"]}},
        "state": {"type": {"in": ["started"]}},
        "updatedAt": {"gt": "2026-04-28T00:00:00Z"},
    }


@pytest.mark.asyncio
async def test_fetch_issues_paginates_until_limit() -> None:
    http_client = FakeAsyncClient(
        [
            json_response(
                {
                    "data": {
                        "issues": {
                            "nodes": [{"identifier": "ENG-1"}, {"identifier": "ENG-2"}],
                            "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                        }
                    }
                }
            ),
            json_response(
                {
                    "data": {
                        "issues": {
                            "nodes": [{"identifier": "ENG-3"}, {"identifier": "ENG-4"}],
                            "pageInfo": {"hasNextPage": True, "endCursor": "cursor-2"},
                        }
                    }
                }
            ),
        ]
    )
    client = LinearClient(StaticAuthProvider(), http_client=http_client)  # type: ignore[arg-type]

    issues = await client.fetch_issues("workspace", limit=3)

    assert issues == [{"identifier": "ENG-1"}, {"identifier": "ENG-2"}, {"identifier": "ENG-3"}]
    assert http_client.requests[0]["json"]["variables"]["after"] is None
    assert http_client.requests[1]["json"]["variables"]["after"] == "cursor-1"
    assert http_client.requests[1]["json"]["variables"]["first"] == 1


@pytest.mark.asyncio
async def test_fetch_issues_raises_on_http_500() -> None:
    http_client = FakeAsyncClient([json_response({"error": "boom"}, status_code=500)])
    client = LinearClient(StaticAuthProvider(), http_client=http_client)  # type: ignore[arg-type]

    with pytest.raises(LinearAPIError) as exc_info:
        await client.fetch_issues("workspace")

    assert exc_info.value.status_code == 500
    assert exc_info.value.workspace_id == "workspace"


@pytest.mark.asyncio
async def test_fetch_issues_raises_on_graphql_errors_array() -> None:
    errors = [{"message": "bad query"}]
    http_client = FakeAsyncClient([json_response({"data": None, "errors": errors})])
    client = LinearClient(StaticAuthProvider(), http_client=http_client)  # type: ignore[arg-type]

    with pytest.raises(LinearAPIError) as exc_info:
        await client.fetch_issues("workspace")

    assert exc_info.value.status_code == 200
    assert exc_info.value.errors == errors
    assert exc_info.value.workspace_id == "workspace"


@pytest.mark.asyncio
async def test_fetch_issue_returns_none_on_not_found() -> None:
    http_client = FakeAsyncClient([json_response({"data": {"issue": None}})])
    client = LinearClient(StaticAuthProvider(), http_client=http_client)  # type: ignore[arg-type]

    assert await client.fetch_issue("workspace", "ENG-404") is None


@pytest.mark.asyncio
async def test_fetch_teams_returns_nodes_verbatim() -> None:
    nodes = [{"id": "team-id", "key": "ENG", "name": "Engineering"}]
    http_client = FakeAsyncClient([json_response({"data": {"teams": {"nodes": nodes}}})])
    client = LinearClient(StaticAuthProvider(), http_client=http_client)  # type: ignore[arg-type]

    assert await client.fetch_teams("workspace") == nodes


@pytest.mark.asyncio
async def test_fetch_workspace_returns_organization_record() -> None:
    organization = {"id": "org-id", "name": "D-sorganization", "urlKey": "d-sorganization"}
    http_client = FakeAsyncClient([json_response({"data": {"organization": organization}})])
    client = LinearClient(StaticAuthProvider(), http_client=http_client)  # type: ignore[arg-type]

    assert await client.fetch_workspace("workspace") == organization


def test_client_respects_custom_timeout() -> None:
    client = LinearClient(StaticAuthProvider(), timeout=7.5)

    assert client.timeout == 7.5
    assert client._http_client.timeout == httpx.Timeout(7.5)  # noqa: SLF001


@pytest.mark.asyncio
async def test_client_aclose_releases_owned_http_client() -> None:
    client = LinearClient(StaticAuthProvider())

    await client.aclose()

    assert client._http_client.is_closed  # noqa: SLF001
