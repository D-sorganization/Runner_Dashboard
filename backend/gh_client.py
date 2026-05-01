"""GitHub API async HTTP client (issue #352).

Replaces the hottest ``subprocess.run(["gh", "api", ...])`` call-sites with a
single pooled ``httpx.AsyncClient`` that reuses TLS connections and caches the
Bearer token from the environment (``GH_TOKEN`` / ``GITHUB_TOKEN``).

Architecture
------------
- One module-level ``httpx.AsyncClient`` with keep-alive pooling.
- Bearer token loaded once at first call and cached in memory.
- Rate-limit and error handling mirrors the existing ``gh_utils`` behaviour
  so call-sites can drop in ``gc.get(path)`` for ``gh_api(path)``.
- ``gh`` CLI kept as fallback: if ``GH_TOKEN`` is absent the module raises
  ``GhAuthError`` and callers can fall back to the subprocess path.

Typed exceptions
----------------
- ``GhAuthError`` — no token available.
- ``GhRateLimited`` — 429 / X-RateLimit-Remaining: 0.
- ``GhNotFound`` — 404 from GitHub.
- ``GhServerError`` — 5xx from GitHub.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

log = logging.getLogger("dashboard.gh_client")

_GITHUB_API_BASE = "https://api.github.com"
_DEFAULT_RETRY_AFTER = 60
_MAX_RETRIES = 5

# ── Token cache ──────────────────────────────────────────────────────────────

_cached_token: str | None = None


def _get_token() -> str:
    """Return a cached GitHub Bearer token from the environment.

    Raises:
        GhAuthError: when no token is available.
    """
    global _cached_token
    if _cached_token:
        return _cached_token
    token = os.environ.get("GH_TOKEN", "").strip() or os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise GhAuthError("GH_TOKEN / GITHUB_TOKEN not set; cannot use httpx GitHub client")
    _cached_token = token
    return token


def clear_token_cache() -> None:
    """Invalidate the cached token (e.g. after token rotation in tests)."""
    global _cached_token
    _cached_token = None


# ── Pooled client ─────────────────────────────────────────────────────────────

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Return the shared pooled GitHub API client, initialising it on first use."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=_GITHUB_API_BASE,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
            http2=False,  # httpx http2 requires the h2 extra; keep plain HTTP/1.1
        )
    return _client


async def close_client() -> None:
    """Close the pooled client (called from server shutdown hook)."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


# ── Typed exceptions ──────────────────────────────────────────────────────────


class GhClientError(RuntimeError):
    """Base class for all GhClient errors."""


class GhAuthError(GhClientError):
    """No GitHub token available."""


class GhRateLimited(GhClientError):
    """GitHub API rate limit hit."""

    def __init__(self, *, retry_after_seconds: int = _DEFAULT_RETRY_AFTER, endpoint: str = "") -> None:
        super().__init__(f"GitHub rate limited; retry after {retry_after_seconds}s (endpoint={endpoint})")
        self.retry_after_seconds = retry_after_seconds
        self.endpoint = endpoint


class GhNotFound(GhClientError):
    """GitHub returned 404."""

    def __init__(self, endpoint: str) -> None:
        super().__init__(f"GitHub 404: {endpoint}")
        self.endpoint = endpoint


class GhServerError(GhClientError):
    """GitHub returned 5xx."""

    def __init__(self, status_code: int, endpoint: str, body: str = "") -> None:
        super().__init__(f"GitHub {status_code}: {endpoint} — {body[:200]}")
        self.status_code = status_code
        self.endpoint = endpoint


# ── Core helpers ──────────────────────────────────────────────────────────────


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_get_token()}"}


def _parse_retry_after(resp: httpx.Response) -> int:
    ra = resp.headers.get("Retry-After", "")
    if ra.isdigit():
        return max(1, int(ra))
    # Try X-RateLimit-Reset (Unix timestamp)
    reset = resp.headers.get("X-RateLimit-Reset", "")
    if reset.isdigit():
        return max(1, int(reset) - int(time.time()))
    return _DEFAULT_RETRY_AFTER


async def _request(method: str, path: str, *, json: Any = None) -> httpx.Response:
    """Execute one authenticated GitHub API request with retry on 429.

    Raises:
        GhAuthError: token missing.
        GhRateLimited: after max retries exhausted.
        GhNotFound: 404.
        GhServerError: non-retryable 5xx.
    """
    client = _get_client()
    headers = _auth_headers()
    for attempt in range(_MAX_RETRIES):
        resp = await client.request(method, path, headers=headers, json=json)
        if resp.status_code == 200:
            return resp
        if resp.status_code == 204:
            return resp
        if resp.status_code == 404:
            raise GhNotFound(path)
        if resp.status_code == 429 or resp.headers.get("X-RateLimit-Remaining") == "0":
            retry_after = _parse_retry_after(resp)
            if attempt < _MAX_RETRIES - 1:
                log.warning(
                    "gh_client: rate limited on %s, waiting %ds (attempt %d/%d)",
                    path,
                    retry_after,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                import asyncio

                jitter = min(retry_after, 10) * (0.5 + 0.5 * (attempt / _MAX_RETRIES))
                await asyncio.sleep(jitter)
                continue
            raise GhRateLimited(retry_after_seconds=retry_after, endpoint=path)
        if resp.status_code >= 500:
            raise GhServerError(resp.status_code, path, resp.text)
        # Other client error (403, 422, etc.) — surface as GhServerError with real status
        raise GhServerError(resp.status_code, path, resp.text)
    raise GhRateLimited(endpoint=path)


# ── Public API ────────────────────────────────────────────────────────────────


async def get(path: str) -> Any:
    """GET a GitHub API path and return the parsed JSON body.

    Args:
        path: Relative API path, e.g. ``/orgs/myorg/actions/runners``.

    Returns:
        Parsed JSON (dict, list, etc.).

    Raises:
        GhAuthError, GhRateLimited, GhNotFound, GhServerError.
    """
    resp = await _request("GET", path)
    if resp.status_code == 204:
        return {}
    return resp.json()


async def post(path: str, *, json: Any = None) -> Any:
    """POST to a GitHub API path.

    Args:
        path: Relative API path.
        json: Optional JSON body.

    Returns:
        Parsed JSON body, or empty dict for 204.
    """
    resp = await _request("POST", path, json=json)
    if resp.status_code in (201, 204):
        return {}
    return resp.json()


async def paginate(path: str, *, per_page: int = 100) -> AsyncIterator[dict]:
    """Yield all items from a paginated GitHub list endpoint.

    Uses GitHub's ``Link: <…>; rel="next"`` header to follow pages.

    Args:
        path: Relative API path, e.g. ``/orgs/myorg/actions/runs``.
        per_page: Page size (max 100).

    Yields:
        Individual item dicts.
    """
    sep = "&" if "?" in path else "?"
    url: str | None = f"{path}{sep}per_page={per_page}"
    client = _get_client()
    headers = _auth_headers()

    while url:
        resp = await client.get(url, headers={**headers, **client.headers})
        if resp.status_code == 404:
            return
        if resp.status_code == 429:
            raise GhRateLimited(retry_after_seconds=_parse_retry_after(resp), endpoint=url)
        if resp.status_code >= 400:
            raise GhServerError(resp.status_code, url, resp.text)
        body = resp.json()
        items: list[dict] = body if isinstance(body, list) else []
        for key in ("workflow_runs", "jobs", "runners", "items", "repositories"):
            if isinstance(body, dict) and key in body:
                items = body[key]
                break
        for item in items:
            yield item
        link = resp.headers.get("link", "")
        url = _parse_next_link(link)


def _parse_next_link(link_header: str) -> str | None:
    """Extract the ``rel="next"`` URL from a GitHub Link header."""
    if not link_header:
        return None
    for part in link_header.split(","):
        url_part, *params = part.strip().split(";")
        url = url_part.strip().strip("<>")
        for param in params:
            if param.strip() == 'rel="next"':
                return url
    return None


# ── High-level typed wrappers ────────────────────────────────────────────────


async def cancel_run(repo_full: str, run_id: int) -> None:
    """Cancel a workflow run.

    Args:
        repo_full: Full repo slug, e.g. ``"myorg/myrepo"``.
        run_id: GitHub Actions run ID.
    """
    await post(f"/repos/{repo_full}/actions/runs/{run_id}/cancel")
    log.info("gh_client: cancelled run %d in %s", run_id, repo_full)


async def rerun_failed(repo_full: str, run_id: int) -> None:
    """Rerun failed jobs in a workflow run.

    Args:
        repo_full: Full repo slug.
        run_id: GitHub Actions run ID.
    """
    await post(f"/repos/{repo_full}/actions/runs/{run_id}/rerun-failed-jobs")
    log.info("gh_client: rerun-failed-jobs %d in %s", run_id, repo_full)
