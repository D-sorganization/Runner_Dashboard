"""Bounded GitHub Actions artifact transport tests (#2115)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], error: Exception | None = None) -> None:
        self.chunks = chunks
        self.error = error
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.error:
            raise self.error

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def artifact_client(monkeypatch: pytest.MonkeyPatch):
    import gh_client

    gh_client.clear_token_cache()
    monkeypatch.setenv("GH_TOKEN", "test-artifact-token")
    clients: list[httpx.AsyncClient] = []

    def install(handler):
        client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Authorization": "Basic client-default",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            auth=httpx.BasicAuth("client-user", "client-password"),
            cookies={"jar-session": "jar-secret"},
            transport=httpx.MockTransport(handler),
        )
        clients.append(client)
        monkeypatch.setattr(gh_client, "_get_client", lambda: client)
        return client

    yield gh_client, install
    for client in clients:
        import asyncio

        asyncio.get_event_loop().run_until_complete(client.aclose())
    gh_client.clear_token_cache()


@pytest.mark.unit
async def test_list_run_artifacts_reads_bounded_pages(artifact_client) -> None:
    gh_client, install = artifact_client
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        page = int(request.url.params["page"])
        artifact = {"id": page, "name": f"artifact-{page}"}
        return httpx.Response(200, json={"total_count": 2, "artifacts": [artifact]})

    install(handler)
    artifacts = await gh_client.list_run_artifacts("owner/repo", 42, max_pages=2)

    assert [item["id"] for item in artifacts] == [1, 2]
    assert [httpx.URL(url).params["page"] for url in calls] == ["1", "2"]


@pytest.mark.unit
@pytest.mark.parametrize("repo,run_id,max_pages", [("../repo", 1, 2), ("owner/repo", True, 2), ("owner/repo", 1, 0)])
async def test_list_run_artifacts_rejects_invalid_bounds_before_request(
    artifact_client, repo: str, run_id: int, max_pages: int
) -> None:
    gh_client, install = artifact_client
    install(lambda _request: pytest.fail("invalid arguments must not make an HTTP request"))

    with pytest.raises(ValueError):
        await gh_client.list_run_artifacts(repo, run_id, max_pages=max_pages)


@pytest.mark.unit
async def test_list_run_artifacts_refuses_incomplete_or_malformed_pagination(artifact_client) -> None:
    gh_client, install = artifact_client
    install(lambda _request: httpx.Response(200, json={"total_count": 2, "artifacts": [{"id": 1}]}))

    with pytest.raises(gh_client.GhClientError, match="pagination"):
        await gh_client.list_run_artifacts("owner/repo", 42, max_pages=1)


@pytest.mark.unit
async def test_download_follows_https_redirect_without_forwarding_authorization(artifact_client) -> None:
    gh_client, install = artifact_client
    seen: list[tuple[str, str | None, str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.url.host,
                request.headers.get("Authorization"),
                request.headers.get("Cookie"),
                request.headers.get("X-GitHub-Api-Version"),
            )
        )
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={"Location": "https://objects.example.test/signed"})
        return httpx.Response(200, stream=_Chunks([b"zip", b"data"]))

    install(handler)
    result = await gh_client.download_actions_artifact("owner/repo", 123, max_bytes=7)

    assert result == b"zipdata"
    assert seen == [
        (
            "api.github.com",
            "Bearer test-artifact-token",
            "jar-session=jar-secret",
            "2022-11-28",
        ),
        ("objects.example.test", None, None, None),
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("headers", "chunks"),
    [({"Content-Length": "5"}, [b"12345"]), ({}, [b"123", b"456"])],
    ids=("declared-over-limit", "streamed-over-limit"),
)
async def test_download_enforces_declared_and_streamed_byte_caps(artifact_client, headers, chunks) -> None:
    gh_client, install = artifact_client

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={"Location": "https://objects.example.test/a"})
        return httpx.Response(200, headers=headers, stream=_Chunks(chunks))

    install(handler)
    with pytest.raises(gh_client.GhClientError, match="limit"):
        await gh_client.download_actions_artifact("owner/repo", 123, max_bytes=4)


@pytest.mark.unit
async def test_download_rejects_content_encoding_expansion(artifact_client) -> None:
    gh_client, install = artifact_client

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={"Location": "https://objects.example.test/a"})
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=_Chunks([b"compressed"]))

    install(handler)
    with pytest.raises(gh_client.GhClientError, match="Content-Encoding"):
        await gh_client.download_actions_artifact("owner/repo", 123, max_bytes=100)


@pytest.mark.unit
@pytest.mark.parametrize(
    "location",
    ["http://objects.example.test/a", "https://user:pass@objects.example.test/a", "/relative/path"],
)
async def test_download_rejects_unsafe_redirects(artifact_client, location: str) -> None:
    gh_client, install = artifact_client
    install(lambda _request: httpx.Response(302, headers={"Location": location}))

    with pytest.raises(gh_client.GhClientError, match="redirect"):
        await gh_client.download_actions_artifact("owner/repo", 123, max_bytes=100)


@pytest.mark.unit
async def test_download_rejects_secondary_redirect_to_http(artifact_client) -> None:
    gh_client, install = artifact_client

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={"Location": "https://objects.example.test/first"})
        return httpx.Response(302, headers={"Location": "http://objects.example.test/second"})

    install(handler)
    with pytest.raises(gh_client.GhClientError, match="redirect"):
        await gh_client.download_actions_artifact("owner/repo", 123, max_bytes=100)


@pytest.mark.unit
async def test_download_bounds_secondary_redirect_hops(artifact_client) -> None:
    gh_client, install = artifact_client

    def handler(request: httpx.Request) -> httpx.Response:
        hop = request.url.path.count("/") - 1
        return httpx.Response(302, headers={"Location": f"https://objects.example.test/hop/{hop + 1}"})

    install(handler)
    with pytest.raises(gh_client.GhClientError, match="redirect"):
        await gh_client.download_actions_artifact("owner/repo", 123, max_bytes=100)


@pytest.mark.unit
@pytest.mark.parametrize("status", [404, 410, 429, 500])
async def test_download_maps_http_failures_to_typed_errors(artifact_client, status: int) -> None:
    gh_client, install = artifact_client
    install(lambda _request: httpx.Response(status, headers={"Retry-After": "2"}))

    expected = {
        404: gh_client.GhNotFound,
        410: gh_client.GhArtifactExpired,
        429: gh_client.GhRateLimited,
        500: gh_client.GhServerError,
    }[status]
    with pytest.raises(expected):
        await gh_client.download_actions_artifact("owner/repo", 123, max_bytes=100)


@pytest.mark.unit
@pytest.mark.parametrize(
    "headers,body",
    [
        ({"Retry-After": "7", "X-RateLimit-Remaining": "12"}, b"permission denied"),
        ({"X-RateLimit-Remaining": "8"}, b"You have exceeded a secondary rate limit."),
        ({}, b"API rate limit exceeded for installation"),
    ],
)
async def test_download_maps_secondary_403_to_rate_limited(artifact_client, headers, body) -> None:
    gh_client, install = artifact_client
    install(lambda _request: httpx.Response(403, headers=headers, stream=_Chunks([body])))

    with pytest.raises(gh_client.GhRateLimited):
        await gh_client.download_actions_artifact("owner/repo", 123, max_bytes=100)


@pytest.mark.unit
async def test_download_secondary_redirect_strips_all_client_defaults(artifact_client) -> None:
    gh_client, install = artifact_client
    seen: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, dict(request.headers)))
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={"Location": "https://objects.example.test/first"})
        if request.url.path == "/first":
            return httpx.Response(302, headers={"Location": "https://cdn.example.test/final"})
        return httpx.Response(200, stream=_Chunks([b"zip"]))

    install(handler)
    assert await gh_client.download_actions_artifact("owner/repo", 123, max_bytes=10) == b"zip"
    assert seen[0][1]["authorization"] == "Bearer test-artifact-token"
    assert seen[0][1]["cookie"] == "jar-session=jar-secret"
    for _host, headers in seen[1:]:
        assert "authorization" not in headers
        assert "proxy-authorization" not in headers
        assert "cookie" not in headers
        assert "x-github-api-version" not in headers


@pytest.mark.unit
async def test_generic_paginate_supports_artifacts_collection(artifact_client) -> None:
    gh_client, install = artifact_client
    install(lambda _request: httpx.Response(200, json={"total_count": 1, "artifacts": [{"id": 8}]}))

    assert [item async for item in gh_client.paginate("/repos/owner/repo/actions/runs/42/artifacts")] == [{"id": 8}]


@pytest.mark.unit
async def test_download_rejects_invalid_id_and_limit_without_request(artifact_client) -> None:
    gh_client, install = artifact_client
    install(lambda _request: pytest.fail("invalid arguments must not make an HTTP request"))

    for artifact_id, limit in [(0, 10), (True, 10), (1, 0)]:
        with pytest.raises(ValueError):
            await gh_client.download_actions_artifact("owner/repo", artifact_id, max_bytes=limit)


@pytest.mark.unit
async def test_download_timeout_closes_stream_and_uses_typed_error(artifact_client) -> None:
    gh_client, install = artifact_client
    body = _Chunks([], error=httpx.ReadTimeout("slow artifact"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={"Location": "https://objects.example.test/a"})
        return httpx.Response(200, stream=body)

    install(handler)
    with pytest.raises(gh_client.GhClientError) as caught:
        await gh_client.download_actions_artifact("owner/repo", 123, max_bytes=100)

    assert caught.value.kind == "timeout"
    assert body.closed


@pytest.mark.unit
async def test_download_cancellation_closes_stream(artifact_client) -> None:
    gh_client, install = artifact_client
    body = _Chunks([], error=asyncio.CancelledError())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={"Location": "https://objects.example.test/a"})
        return httpx.Response(200, stream=body)

    install(handler)
    with pytest.raises(asyncio.CancelledError):
        await gh_client.download_actions_artifact("owner/repo", 123, max_bytes=100)

    assert body.closed
