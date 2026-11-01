"""ETag cache for GitHub GETs so unchanged data costs no rate limit (issue #3671).

GitHub answers a GET carrying ``If-None-Match: <etag>`` with ``304 Not Modified``
when the resource is unchanged, and a 304 does not count against the primary rate
limit. The dashboard refetches the same runners, runs and repo listings on every
cache refresh, so remembering the last body per URL turns most of those refetches
into free 304s. It saves quota, not latency: a 304 still costs a round trip.

Entries are keyed by auth identity plus URL so two tokens never share a body.
The cache is bounded by entry count and per-body size and evicts least recently
used entries first.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass

import httpx

DEFAULT_MAX_ENTRIES = 512
DEFAULT_MAX_BODY_BYTES = 1024 * 1024
# Response headers callers read from a replayed 304 (pagination uses Link).
REPLAYED_HEADERS = ("content-type", "link", "etag")
HTTP_OK = 200


@dataclass(frozen=True)
class CachedResponse:
    """The parts of a 200 response needed to replay it after a 304."""

    etag: str
    content: bytes
    headers: dict[str, str]


class EtagCache:
    """Thread-safe bounded LRU of ``key -> CachedResponse``."""

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES, max_body_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
        if max_entries <= 0 or max_body_bytes <= 0:
            raise ValueError("EtagCache limits must be positive")
        self._max_entries = max_entries
        self._max_body_bytes = max_body_bytes
        self._entries: OrderedDict[str, CachedResponse] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> CachedResponse | None:
        """Return the entry for ``key`` and mark it recently used."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
            return entry

    def put(self, key: str, entry: CachedResponse) -> None:
        """Store ``entry`` unless its body is over the size limit."""
        if len(entry.content) > self._max_body_bytes:
            return
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        """Drop every entry."""
        with self._lock:
            self._entries.clear()


_cache = EtagCache()


def cache_key(auth_fingerprint: str, path: str) -> str:
    """Key a GET by auth identity and URL (path or absolute pagination URL)."""
    return f"{auth_fingerprint}\n{path}"


def lookup(key: str) -> CachedResponse | None:
    """Return the cached response for ``key``, if any."""
    return _cache.get(key)


def clear() -> None:
    """Drop every cached response (tests, token rotation)."""
    _cache.clear()


def remember(key: str, resp: httpx.Response) -> None:
    """Cache a 200 response that carries an ETag; ignore anything else.

    Tolerates stand-in responses: only real ``httpx.Headers`` with an ETag and a
    ``bytes`` body are stored.
    """
    response_headers = getattr(resp, "headers", None)
    content = getattr(resp, "content", None)
    if resp.status_code != HTTP_OK or not isinstance(response_headers, httpx.Headers):
        return
    etag = response_headers.get("etag")
    if not etag or not isinstance(content, bytes):
        return
    headers = {name: response_headers[name] for name in REPLAYED_HEADERS if name in response_headers}
    _cache.put(key, CachedResponse(etag=etag, content=content, headers=headers))


def replay(entry: CachedResponse, not_modified: httpx.Response) -> httpx.Response:
    """Build a 200 response from ``entry`` for a request GitHub answered with 304."""
    headers = dict(entry.headers)
    link = not_modified.headers.get("link")
    if isinstance(link, str) and link:
        headers["link"] = link
    return httpx.Response(HTTP_OK, content=entry.content, headers=headers, request=not_modified.request)
