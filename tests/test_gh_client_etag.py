"""gh_client sends If-None-Match and serves 304 Not Modified from its ETag cache (#3671).

GitHub does not count a 304 answer to a conditional request against the primary
rate limit, so unchanged data refetched on every cache refresh becomes free.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import gh_client  # noqa: E402
import gh_etag_cache  # noqa: E402

pytestmark = pytest.mark.unit

ETAG = 'W/"abc123"'


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GH_TOKEN", "stub-not-a-real-token")
    gh_client._cached_token = "stub-not-a-real-token"
    gh_client._cached_token_expires_at = 0.0
    gh_client._client = None
    gh_etag_cache.clear()
    yield
    gh_client._cached_token = None
    gh_client._client = None
    gh_etag_cache.clear()


def _client_answering(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="https://api.github.com", transport=httpx.MockTransport(handler))


class _ConditionalServer:
    """Answers 200 with an ETag, then 304 whenever If-None-Match matches."""

    def __init__(self, body: object, link: str = "") -> None:
        self.body = body
        self.link = link
        self.seen_if_none_match: list[str | None] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        sent = request.headers.get("if-none-match")
        self.seen_if_none_match.append(sent)
        headers = {"ETag": ETAG}
        if self.link:
            headers["Link"] = self.link
        if sent == ETAG:
            return httpx.Response(304, headers=headers)
        return httpx.Response(200, json=self.body, headers=headers)


@pytest.mark.asyncio
async def test_second_get_sends_if_none_match_and_returns_stored_body_on_304() -> None:
    server = _ConditionalServer({"runners": [{"id": 1}]})
    with patch("gh_client._get_client", return_value=_client_answering(server)):
        first = await gh_client.get("/orgs/o/actions/runners")
        second = await gh_client.get("/orgs/o/actions/runners")
    assert first == second == {"runners": [{"id": 1}]}
    assert server.seen_if_none_match == [None, ETAG]
    assert gh_client.get_status()["status"] == "ok"


@pytest.mark.asyncio
async def test_paginate_keeps_link_header_through_a_304() -> None:
    pages = {
        "/repos/o/r/actions/runs?per_page=100": _ConditionalServer(
            {"workflow_runs": [{"id": 1}]},
            link='<https://api.github.com/repos/o/r/actions/runs?per_page=100&page=2>; rel="next"',
        ),
        "/repos/o/r/actions/runs?per_page=100&page=2": _ConditionalServer({"workflow_runs": [{"id": 2}]}),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return pages[str(request.url).removeprefix("https://api.github.com")](request)

    with patch("gh_client._get_client", return_value=_client_answering(handler)):
        cold = [item["id"] async for item in gh_client.paginate("/repos/o/r/actions/runs")]
        warm = [item["id"] async for item in gh_client.paginate("/repos/o/r/actions/runs")]
    assert cold == warm == [1, 2]
    assert all(server.seen_if_none_match[-1] == ETAG for server in pages.values())


@pytest.mark.asyncio
async def test_post_is_never_conditional() -> None:
    server = _ConditionalServer({"ok": True})
    with patch("gh_client._get_client", return_value=_client_answering(server)):
        await gh_client.post("/repos/o/r/dispatches", json={})
        await gh_client.post("/repos/o/r/dispatches", json={})
    assert server.seen_if_none_match == [None, None]


@pytest.mark.asyncio
async def test_cache_is_partitioned_by_auth_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _ConditionalServer({"login": "a"})
    with patch("gh_client._get_client", return_value=_client_answering(server)):
        await gh_client.get("/user")
        monkeypatch.setenv("GH_TOKEN", "a-different-stub-token")
        await gh_client.get("/user")
    assert server.seen_if_none_match == [None, None]


def test_cache_evicts_least_recently_used_entry() -> None:
    cache = gh_etag_cache.EtagCache(max_entries=2)
    for key in ("a", "b"):
        cache.put(key, gh_etag_cache.CachedResponse(etag=key, content=b"{}", headers={}))
    assert cache.get("a") is not None  # touch "a" so "b" is the oldest
    cache.put("c", gh_etag_cache.CachedResponse(etag="c", content=b"{}", headers={}))
    assert cache.get("b") is None
    assert cache.get("a") is not None
    assert cache.get("c") is not None


def test_oversized_bodies_are_not_cached() -> None:
    cache = gh_etag_cache.EtagCache(max_entries=4, max_body_bytes=8)
    cache.put("big", gh_etag_cache.CachedResponse(etag="x", content=b"0123456789", headers={}))
    assert cache.get("big") is None
