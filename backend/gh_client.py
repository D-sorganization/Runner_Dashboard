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

import asyncio
import logging
import os
import random
import re
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx

log = logging.getLogger("dashboard.gh_client")

_GITHUB_API_BASE = "https://api.github.com"
_DEFAULT_RETRY_AFTER = 60

# ── Retry policy constants ────────────────────────────────────────────────────

_MAX_RETRIES: int = 5
assert _MAX_RETRIES > 0, "MAX_RETRIES must be positive"
_MAX_ARTIFACT_PAGES = 100
_MAX_ARTIFACT_REDIRECTS = 3
_MAX_ARTIFACT_DOWNLOAD_BYTES = 20 * 1024 * 1024
_REPOSITORY_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ARTIFACT_COLLECTION_PATH = re.compile(
    r"^/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/(?:runs/[1-9][0-9]*/)?artifacts$"
)

# ── Token cache ──────────────────────────────────────────────────────────────

_cached_token: str | None = None
_cached_token_expires_at: float = 0.0
_cached_token_source: str = "unknown"
# Issue #938(c): serialize concurrent GitHub App token exchanges so a refresh
# storm (every request in flight when the ~hourly token expires) triggers
# exactly one upstream exchange instead of one per coroutine.
_token_refresh_lock: asyncio.Lock = asyncio.Lock()
_last_status: dict[str, Any] = {
    "status": "unknown",
    "detail": "No GitHub API request has completed in this process.",
    "endpoint": "",
    "retry_after_seconds": 0,
    "auth_source": "unknown",
    "updated_at": None,
}


def _set_status(status: str, detail: str, *, endpoint: str = "", retry_after_seconds: int = 0) -> None:
    _last_status.update(
        {
            "status": status,
            "detail": detail[:500],
            "endpoint": endpoint,
            "retry_after_seconds": max(0, int(retry_after_seconds)),
            "auth_source": _cached_token_source,
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )


def get_status() -> dict[str, Any]:
    """Return the last observed GitHub API health state for dashboard display."""
    return dict(_last_status)


def _get_token() -> str:
    """Return a cached GitHub Bearer token.

    GitHub App installation auth is preferred when configured:
    ``GITHUB_APP_ID`` + ``GITHUB_APP_INSTALLATION_ID`` +
    ``GITHUB_APP_PRIVATE_KEY_FILE`` or ``GITHUB_APP_PRIVATE_KEY``.

    ``GH_TOKEN`` / ``GITHUB_TOKEN`` remains a fallback so an App credential
    rollout cannot take the dashboard offline.

    Raises:
        GhAuthError: when no token is available.
    """
    if _cached_token and (_cached_token_expires_at == 0.0 or time.time() < _cached_token_expires_at):
        return _cached_token
    if _github_app_auth_configured():
        raise GhAuthError(
            "GitHub App auth requires the async token path; call _get_token_async() "
            "from the request path (issue #938c)."
        )
    return _resolve_env_token()


def _resolve_env_token() -> str:
    """Resolve and cache a GH_TOKEN / GITHUB_TOKEN env credential (sync)."""
    global _cached_token, _cached_token_expires_at, _cached_token_source
    token = os.environ.get("GH_TOKEN", "").strip() or os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        _cached_token_source = "missing"
        _set_status(
            "auth_missing",
            "GitHub auth is not configured; set GitHub App env vars or GH_TOKEN / GITHUB_TOKEN",
        )
        raise GhAuthError("GitHub auth not configured; cannot use httpx GitHub client")
    _cached_token = token
    _cached_token_expires_at = time.time() + 300
    _cached_token_source = "env_token"
    _last_status["auth_source"] = _cached_token_source
    return token


async def _get_token_async() -> str:
    """Async token resolver used by the request path (issue #938c).

    GitHub App installation tokens are exchanged via an async client under a
    shared lock, so a refresh storm at token expiry triggers exactly one
    upstream exchange. Falls back to GH_TOKEN / GITHUB_TOKEN when the App
    exchange fails or App auth is not configured.
    """
    global _cached_token, _cached_token_expires_at, _cached_token_source
    if _cached_token and (_cached_token_expires_at == 0.0 or time.time() < _cached_token_expires_at):
        return _cached_token

    if _github_app_auth_configured():
        async with _token_refresh_lock:
            # Re-check under the lock: a concurrent coroutine may have just
            # refreshed the token while we waited (dedupe the exchange).
            if _cached_token and (_cached_token_expires_at == 0.0 or time.time() < _cached_token_expires_at):
                return _cached_token
            try:
                _cached_token = await _fetch_github_app_installation_token()
                _cached_token_source = "github_app"
                _last_status["auth_source"] = _cached_token_source
                return _cached_token
            except Exception as exc:
                _cached_token = None
                _cached_token_expires_at = 0.0
                _cached_token_source = "github_app_error"
                _set_status("auth_error", f"GitHub App token exchange failed: {type(exc).__name__}: {exc}")
                log.exception("GitHub App token exchange failed; falling back to GH_TOKEN when present")

    return _resolve_env_token()


def clear_token_cache() -> None:
    """Invalidate the cached token (e.g. after token rotation in tests)."""
    global _cached_token, _cached_token_expires_at, _cached_token_source
    _cached_token = None
    _cached_token_expires_at = 0.0
    _cached_token_source = "unknown"


def get_auth_fingerprint() -> str:
    """Return a non-secret auth identity for cache/rate-limit partitioning."""
    app_id = os.environ.get("GITHUB_APP_ID", "").strip()
    installation_id = os.environ.get("GITHUB_APP_INSTALLATION_ID", "").strip()
    if app_id and installation_id and _github_app_private_key_configured():
        return f"github_app:{app_id}:{installation_id}"
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if token:
        return f"token:{sha256(token.encode()).hexdigest()[:16]}"
    return "anonymous"


def _github_app_auth_configured() -> bool:
    from github_app_auth import github_app_auth_configured

    return github_app_auth_configured()


def _github_app_private_key_configured() -> bool:
    from github_app_auth import github_app_private_key_configured

    return github_app_private_key_configured()


def _build_app_jwt(now: datetime | None = None) -> str:
    from github_app_auth import build_app_jwt

    return build_app_jwt(now)


async def _fetch_github_app_installation_token() -> str:
    """Exchange a GitHub App JWT for an installation access token (#938c, #2038).

    Scoped staff-run minting and permission diagnostics live in ``github_app_auth``;
    this path keeps the dashboard cache on an unscoped installation token.
    """
    global _cached_token_expires_at

    from github_app_auth import mint_installation_access_token

    minted = await asyncio.to_thread(mint_installation_access_token)
    _cached_token_expires_at = max(time.time() + 60, minted.expires_at - 300)
    return minted.token


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

    def __init__(
        self,
        message: str,
        *,
        kind: str = "unknown",
        attempts: int = 0,
        last_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.attempts = attempts
        self.last_status = last_status


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


class GhArtifactExpired(GhClientError):
    """GitHub reports an artifact archive as expired or gone."""

    def __init__(self, endpoint: str) -> None:
        super().__init__(f"GitHub artifact is expired or gone: {endpoint}", kind="artifact_expired")
        self.endpoint = endpoint


class GhServerError(GhClientError):
    """GitHub returned 5xx."""

    def __init__(self, status_code: int, endpoint: str, body: str = "") -> None:
        super().__init__(f"GitHub {status_code}: {endpoint} — {body[:200]}")
        self.status_code = status_code
        self.endpoint = endpoint


# ── Core helpers ──────────────────────────────────────────────────────────────


async def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {await _get_token_async()}"}


def _parse_retry_after(resp: httpx.Response) -> int:
    ra = resp.headers.get("Retry-After", "")
    if ra.isdigit():
        return max(1, int(ra))
    # Try X-RateLimit-Reset (Unix timestamp)
    reset = resp.headers.get("X-RateLimit-Reset", "")
    if reset.isdigit():
        return max(1, int(reset) - int(time.time()))
    return _DEFAULT_RETRY_AFTER


def _is_rate_limited_response(resp: httpx.Response) -> bool:
    body = resp.text.lower()
    return (
        resp.status_code == 429
        or resp.headers.get("X-RateLimit-Remaining") == "0"
        or "api rate limit exceeded" in body
        or "secondary rate limit" in body
        or "rate limit exceeded" in body
    )


async def _request(method: str, path: str, *, json: Any = None) -> httpx.Response:
    """Execute authenticated GitHub API request with retry on transient errors.

    Pre-condition: path starts with '/' or is a full URL.
    Post-condition: returns httpx.Response or raises a GhClientError subclass.

    Retries on: TimeoutException, ConnectError, 5xx, 429.
    Does NOT retry on: 4xx client errors (except 429).

    Raises:
        GhAuthError: token missing.
        GhRateLimited: 429 after all retries exhausted.
        GhNotFound: 404.
        GhServerError: non-retryable client error or auth error.
        GhClientError: timeout/connect error after all retries exhausted.
    """
    client = _get_client()
    headers = await _auth_headers()
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        try:
            resp = await client.request(method, path, headers=headers, json=json)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            backoff = min(2**attempt + random.uniform(0, 1), 32)
            log.warning(
                "gh_client: retry attempt=%d reason=%s path=%s backoff=%.1fs",
                attempt + 1,
                type(exc).__name__.lower(),
                path,
                backoff,
            )
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(backoff)
            continue

        # Issue #938(a): accept any 2xx as success. GitHub returns 202 Accepted
        # for async-processed actions (e.g. cancel_run); treating it as an error
        # reported a successful cancel as a GhServerError.
        if 200 <= resp.status_code < 300:
            _set_status("ok", "GitHub API requests are succeeding.", endpoint=path)
            return resp
        if resp.status_code in (401, 403) and not _is_rate_limited_response(resp):
            _set_status("auth_error", f"GitHub API returned {resp.status_code}", endpoint=path)
            raise GhServerError(resp.status_code, path, resp.text)
        if resp.status_code == 404:
            _set_status("not_found", f"GitHub API returned 404 for {path}", endpoint=path)
            raise GhNotFound(path)
        if _is_rate_limited_response(resp):
            retry_after = _parse_retry_after(resp)
            _set_status(
                "rate_limited",
                f"Rate limited; retry after {retry_after}s",
                endpoint=path,
                retry_after_seconds=retry_after,
            )
            log.warning(
                "gh_client: rate limited on %s; retry after %ds (attempt=%d)",
                path,
                retry_after,
                attempt + 1,
            )
            last_exc = GhRateLimited(retry_after_seconds=retry_after, endpoint=path)
            # A dashboard request handler must not sleep for GitHub's reset
            # window. Under fleet backlogs the UI fans out across many repos;
            # retrying each call for 30s+ makes the backend look offline and
            # multiplies pressure on the same exhausted token.
            raise last_exc
        if resp.status_code >= 500:
            backoff = min(2**attempt + random.uniform(0, 1), 32)
            log.warning(
                "gh_client: server error %d on %s (attempt=%d) backoff=%.1fs",
                resp.status_code,
                path,
                attempt + 1,
                backoff,
            )
            last_exc = GhServerError(resp.status_code, path, resp.text)
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(backoff)
            continue
        # Other client errors (422, etc.) — don't retry
        _set_status("client_error", f"GitHub API returned {resp.status_code}", endpoint=path)
        raise GhServerError(resp.status_code, path, resp.text)

    # All retries exhausted
    if isinstance(last_exc, (httpx.TimeoutException, httpx.ConnectError)):
        raise GhClientError(
            f"GitHub API timeout/connection failed after {_MAX_RETRIES} attempts: {path}",
            kind="timeout",
            attempts=_MAX_RETRIES,
        )
    if isinstance(last_exc, GhRateLimited):
        raise last_exc
    if isinstance(last_exc, GhServerError):
        raise GhClientError(
            f"GitHub API server error after {_MAX_RETRIES} attempts: {path}",
            kind="server_error",
            attempts=_MAX_RETRIES,
            last_status=last_exc.status_code,
        )
    raise GhClientError(
        f"GitHub API request failed after {_MAX_RETRIES} attempts: {path}",
        attempts=_MAX_RETRIES,
    )


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


async def get_text(path: str) -> str:
    """GET a GitHub API path whose body is plain text, such as a job log (#1881).

    GitHub answers ``/actions/jobs/{id}/logs`` with a redirect to a short-lived signed
    URL; it is followed here (httpx drops the ``Authorization`` header on the cross-origin
    hop). No retries: callers treat a missing log as best-effort.

    Raises:
        GhAuthError, GhNotFound, GhServerError.
    """
    resp = await _get_client().get(path, headers=await _auth_headers(), follow_redirects=True)
    if resp.status_code == 404:
        raise GhNotFound(path)
    if resp.status_code >= 400:
        raise GhServerError(resp.status_code, path, resp.text[:500])
    return resp.text


def _validate_artifact_request(repo_full: str, ident: int, limit: int) -> None:
    if not _REPOSITORY_SLUG.fullmatch(repo_full) or any(part in {".", ".."} for part in repo_full.split("/")):
        raise ValueError("repo_full must be an owner/repository slug")
    if isinstance(ident, bool) or not isinstance(ident, int) or ident <= 0:
        raise ValueError("artifact and run IDs must be positive integers")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_ARTIFACT_DOWNLOAD_BYTES:
        raise ValueError(f"max_bytes must be from 1 to {_MAX_ARTIFACT_DOWNLOAD_BYTES}")


def _validate_artifact_page(body: Any) -> tuple[int, list[dict[str, Any]]]:
    if not isinstance(body, dict):
        raise GhClientError("Malformed Actions artifact pagination response", kind="pagination")
    total = body.get("total_count")
    artifacts = body.get("artifacts")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise GhClientError("Malformed Actions artifact pagination total_count", kind="pagination")
    if not isinstance(artifacts, list) or len(artifacts) > 100 or any(not isinstance(item, dict) for item in artifacts):
        raise GhClientError("Malformed Actions artifact pagination items", kind="pagination")
    return total, artifacts


async def list_run_artifacts(repo_full: str, run_id: int, *, max_pages: int = 10) -> list[dict[str, Any]]:
    """List every artifact for a workflow run, refusing incomplete bounded pagination."""
    _validate_artifact_request(repo_full, run_id, 1)
    path = f"/repos/{repo_full}/actions/runs/{run_id}/artifacts"
    return await _list_artifact_collection(path, max_pages=max_pages)


def _artifact_page_url(path: str, page: int, per_page: int) -> str:
    parsed = urlsplit(path)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in {"page", "per_page"}
    ]
    query.extend((("page", str(page)), ("per_page", str(per_page))))
    return parsed._replace(query=urlencode(query)).geturl()


async def _list_artifact_collection(path: str, *, max_pages: int = 10, per_page: int = 100) -> list[dict[str, Any]]:
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= _MAX_ARTIFACT_PAGES:
        raise ValueError(f"max_pages must be from 1 to {_MAX_ARTIFACT_PAGES}")
    if isinstance(per_page, bool) or not isinstance(per_page, int) or not 1 <= per_page <= 100:
        raise ValueError("per_page must be from 1 to 100")
    result: list[dict[str, Any]] = []
    expected_total: int | None = None
    seen_ids: set[int] = set()
    for page in range(1, max_pages + 1):
        response = await _request("GET", _artifact_page_url(path, page, per_page))
        total, items = _validate_artifact_page(response.json())
        if expected_total is not None and total != expected_total:
            raise GhClientError("Actions artifact pagination changed during listing", kind="pagination")
        expected_total = total
        for item in items:
            artifact_id = item.get("id")
            if (
                isinstance(artifact_id, bool)
                or not isinstance(artifact_id, int)
                or artifact_id <= 0
                or artifact_id in seen_ids
            ):
                raise GhClientError("Actions artifact pagination contains invalid or duplicate IDs", kind="pagination")
            seen_ids.add(artifact_id)
            result.append(item)
        if len(result) > total:
            raise GhClientError("Actions artifact pagination exceeded total_count", kind="pagination")
        if len(result) == total:
            return result
        if not items:
            raise GhClientError("Actions artifact pagination ended before total_count", kind="pagination")
    raise GhClientError("Actions artifact pagination exceeded max_pages", kind="pagination")


def _is_artifact_collection_path(path: str) -> bool:
    parsed = urlsplit(path)
    if _ARTIFACT_COLLECTION_PATH.fullmatch(parsed.path) is None:
        return False
    if parsed.scheme or parsed.netloc:
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.github.com"
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Artifact collection origin must be https://api.github.com")
    return True


def _validate_artifact_redirect(location: str) -> str:
    if not location or len(location) > 8192 or any(char in location for char in "\r\n"):
        raise GhClientError("Invalid Actions artifact redirect", kind="redirect")
    try:
        parsed = urlsplit(location)
        port = parsed.port
    except ValueError as exc:
        raise GhClientError("Invalid Actions artifact redirect", kind="redirect") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in (None, 443)
    ):
        raise GhClientError("Invalid Actions artifact redirect", kind="redirect")
    return location


async def _read_artifact_body(response: httpx.Response, limit: int, endpoint: str) -> bytes:
    encoding = response.headers.get("Content-Encoding", "identity").lower().strip()
    if encoding not in ("", "identity"):
        raise GhClientError("Content-Encoding on Actions artifact is not accepted", kind="content_encoding")
    declared = response.headers.get("Content-Length")
    if declared is not None:
        if not declared.isdigit():
            raise GhClientError("Invalid Actions artifact Content-Length", kind="content_length")
        if int(declared) > limit:
            raise GhClientError("Actions artifact exceeds download byte limit", kind="size_limit")
    data = bytearray()
    async for chunk in response.aiter_raw(chunk_size=min(64 * 1024, limit + 1)):
        if len(data) + len(chunk) > limit:
            raise GhClientError("Actions artifact exceeds download byte limit", kind="size_limit")
        data.extend(chunk)
    size = len(data)
    if declared is not None and size != int(declared):
        raise GhClientError(f"Actions artifact length mismatch for {endpoint}", kind="content_length")
    return bytes(data)


async def _read_error_prefix(response: httpx.Response, limit: int = 512) -> str:
    prefix = bytearray()
    async for chunk in response.aiter_raw(chunk_size=limit):
        prefix.extend(chunk[: limit - len(prefix)])
        if len(prefix) >= limit:
            break
    return prefix.decode("utf-8", errors="replace").lower()


async def _raise_artifact_http_error(response: httpx.Response, endpoint: str) -> None:
    status = response.status_code
    if status == 404:
        raise GhNotFound(endpoint)
    if status == 410:
        raise GhArtifactExpired(endpoint)
    if status in (403, 429):
        retry_after = response.headers.get("Retry-After")
        remaining = response.headers.get("X-RateLimit-Remaining")
        body = await _read_error_prefix(response) if status == 403 else ""
        limited = (
            status == 429
            or remaining == "0"
            or retry_after is not None
            or "api rate limit exceeded" in body
            or "secondary rate limit" in body
            or "rate limit exceeded" in body
        )
        if limited:
            raise GhRateLimited(retry_after_seconds=_parse_retry_after(response), endpoint=endpoint)
    raise GhServerError(status, endpoint)


async def _download_artifact(client: httpx.AsyncClient, endpoint: str, auth: dict[str, str], limit: int) -> bytes:
    url = endpoint
    request_headers = auth
    visited: set[str] = set()
    for redirect_count in range(_MAX_ARTIFACT_REDIRECTS + 1):
        request = client.build_request("GET", url, headers=request_headers)
        if redirect_count:
            host = request.headers.get("host")
            request.headers.clear()
            if host:
                request.headers["Host"] = host
            request.headers["Accept"] = "application/octet-stream"
        response: httpx.Response | None = None
        try:
            response = await client.send(request, stream=True, follow_redirects=False, auth=None)
            if response.status_code in {301, 302, 303, 307, 308}:
                if redirect_count >= _MAX_ARTIFACT_REDIRECTS:
                    raise GhClientError("Actions artifact exceeded redirect limit", kind="redirect")
                target = _validate_artifact_redirect(response.headers.get("Location", ""))
                if target in visited or target == url:
                    raise GhClientError("Actions artifact redirect loop", kind="redirect")
                visited.add(url)
                url, request_headers = target, {}
                continue
            if response.status_code != 200:
                await _raise_artifact_http_error(response, endpoint)
            return await _read_artifact_body(response, limit, endpoint)
        except httpx.TimeoutException as exc:
            raise GhClientError("GitHub artifact download timed out", kind="timeout", attempts=1) from exc
        except httpx.TransportError as exc:
            raise GhClientError("GitHub artifact download failed", kind="connection", attempts=1) from exc
        finally:
            if response is not None:
                await response.aclose()
    raise GhClientError("Actions artifact exceeded redirect limit", kind="redirect")


async def download_actions_artifact(repo_full: str, artifact_id: int, *, max_bytes: int) -> bytes:
    """Download one artifact ZIP with bounded bytes and unauthenticated HTTPS redirects."""
    _validate_artifact_request(repo_full, artifact_id, max_bytes)
    endpoint = f"/repos/{repo_full}/actions/artifacts/{artifact_id}/zip"
    try:
        result = await _download_artifact(_get_client(), endpoint, await _auth_headers(), max_bytes)
    except GhClientError:
        raise
    except httpx.TimeoutException as exc:
        raise GhClientError("GitHub artifact download timed out", kind="timeout", attempts=1) from exc
    if result:
        _set_status("ok", "GitHub Actions artifact download succeeded.", endpoint=endpoint)
    return result


async def post(path: str, *, json: Any = None) -> Any:
    """POST to a GitHub API path.

    Args:
        path: Relative API path.
        json: Optional JSON body.

    Returns:
        Parsed JSON body (including the created resource for 201), or an
        empty dict for 204 No Content.
    """
    resp = await _request("POST", path, json=json)
    if resp.status_code == 204:
        return {}
    return resp.json()


async def patch(path: str, *, json: Any = None) -> Any:
    """PATCH a GitHub API path (e.g. edit an issue comment, #1865).

    Args:
        path: Relative API path.
        json: Optional JSON body.

    Returns:
        Parsed JSON body, or empty dict for 204.
    """
    resp = await _request("PATCH", path, json=json)
    if resp.status_code == 204:
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
    if _is_artifact_collection_path(path):
        artifacts = await _list_artifact_collection(path, per_page=per_page)
        for artifact in artifacts:
            yield artifact
        return

    sep = "&" if "?" in path else "?"
    url: str | None = f"{path}{sep}per_page={per_page}"

    while url:
        # Issue #938(b): route every page through _request so the shared
        # 403-primary-rate-limit detection (X-RateLimit-Remaining: 0), 429
        # back-off, and 5xx retry/backoff apply. paginate previously only
        # special-cased 429 and aborted on a primary-rate-limit 403 or a
        # transient 5xx.
        try:
            resp = await _request("GET", url)
        except GhNotFound:
            return
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


async def rerun(repo_full: str, run_id: int) -> None:
    """Rerun every job in a workflow run.

    Args:
        repo_full: Full repo slug.
        run_id: GitHub Actions run ID.
    """
    await post(f"/repos/{repo_full}/actions/runs/{run_id}/rerun")
    log.info("gh_client: rerun %d in %s", run_id, repo_full)
