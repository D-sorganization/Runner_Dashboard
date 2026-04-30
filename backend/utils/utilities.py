"""
Utility functions and middleware for the Runner Dashboard backend.

This module contains extracted helper utilities including:
  - API key management
  - Input validation and sanitization
  - Subprocess utilities
  - Response caching
  - Security middleware
  - HTTP proxying and GitHub API helpers
"""

import asyncio
import ipaddress
import json
import logging
import os
import secrets
import shlex
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("dashboard")


# ─── API Key Authentication ───────────────────────────────────────────────────


def _load_or_generate_api_key() -> str:
    """Return the dashboard API key, generating one if not set."""
    key_from_env = os.environ.get("DASHBOARD_API_KEY", "").strip()
    if key_from_env:
        return key_from_env
    # Try to read from persistent file
    key_file = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "runner-dashboard" / "api_key.txt"
    try:
        if key_file.exists():
            stored = key_file.read_text(encoding="utf-8").strip()
            if stored:
                return stored
    except OSError:
        pass
    # Generate a new key and persist it
    new_key = secrets.token_urlsafe(32)
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(new_key, encoding="utf-8")
        key_file.chmod(0o600)
        log.warning("Generated new API key; saved to %s", key_file)
        log.warning("Add header 'Authorization: Bearer %s' to all API requests.", new_key)
    except OSError as exc:
        log.warning("Could not persist API key to %s: %s", key_file, exc)
    return new_key


def _setup_api_key() -> str:
    """Called after logging is configured to load/generate the API key.

    Returns the API key (caller should assign to DASHBOARD_API_KEY global).
    """
    return _load_or_generate_api_key()


# ─── Security Utilities ───────────────────────────────────────────────────────


def sanitize_log_value(value: str) -> str:
    """Strip log-injection characters from user-controlled strings."""
    return value.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")[:200]


def safe_subprocess_env() -> dict[str, str]:
    """Return os.environ with secrets stripped out for subprocess calls."""
    excluded = {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "ANTHROPIC_API_KEY",
        "DASHBOARD_API_KEY",
        "SECRET",
        "PASSWORD",
        "TOKEN",
    }
    return {k: v for k, v in os.environ.items() if not any(exc in k.upper() for exc in excluded)}


def validate_fleet_node_url(url: str) -> str:
    """Validate a fleet node URL to prevent SSRF (issue #28)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Fleet node URL must use http or https: {url}")
    host = parsed.hostname or ""
    try:
        addr = ipaddress.ip_address(host)
        if not (addr.is_private or addr.is_loopback):
            raise ValueError(f"Fleet node URL must be a private/local address: {url}")
    except ValueError as exc:
        # If it's not an IP address check it's a hostname we trust
        if "must be" in str(exc):
            raise
        # hostname — allow localhost, .local, .internal
        if not (host == "localhost" or host.endswith(".local") or host.endswith(".internal")):
            raise ValueError(f"Fleet node hostname not allowed: {host}") from exc
    return url


def validate_local_url(url: str, field: str = "url") -> str:
    """Validate that a URL has http/https scheme and a local host (issue #23)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"{field} must use http or https")
    return validate_fleet_node_url(url)


def validate_local_path(path_str: str, allowed_root: Path) -> Path:
    """Resolve path and ensure it stays within allowed_root (issue #23)."""
    resolved = Path(path_str).expanduser().resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError(f"Path escapes allowed root: {path_str}") from exc
    return resolved


def validate_health_command(cmd: str) -> list[str]:
    """Parse health command safely, rejecting shell metacharacters (issue #22)."""
    dangerous = set(";|&`$()<>")
    if any(c in cmd for c in dangerous):
        raise ValueError(f"health_command contains disallowed characters: {cmd!r}")
    return shlex.split(cmd)


# ─── Response Cache ───────────────────────────────────────────────────────────
# The frontend polls every 10-15 s; without caching, each poll spawns dozens of
# `gh api` subprocesses that rapidly exhaust the 5 000 req/hr rate limit.
# TTL values are tuned to each endpoint's staleness tolerance.
#
#   runners / health  → 25 s   (runner state changes on job start/finish)
#   queue             → 20 s   (jobs drain fast; want near-real-time)
#   runs              → 30 s
#   stats             → 60 s   (aggregate counts; no need to be instant)
#   repos             → 120 s  (repo list / metadata changes rarely)
#   diagnose          → 60 s   (expensive multi-call; used for troubleshooting)


def _cache_get(key: str, ttl: float, cache: dict[str, tuple[Any, float]]) -> Any | None:
    """Return cached value if within TTL, else None."""
    entry = cache.get(key)
    if entry is not None:
        data, ts = entry
        if time.time() - ts < ttl:
            return data
    return None


def _cache_set(key: str, data: Any, cache: dict[str, tuple[Any, float]], max_size: int = 500, evict_batch: int = 50, _ttl: float | None = None) -> None:
    """Store value with current timestamp. Evicts oldest entries when full (issue #48)."""
    if key in cache:
        cache.move_to_end(key)
    elif len(cache) >= max_size:
        for _ in range(evict_batch):
            if cache:
                cache.popitem(last=False)
    cache[key] = (data, time.time())


# ─── Subprocess Utilities ─────────────────────────────────────────────────────


async def run_cmd(cmd: list[str], timeout: int = 30, cwd: Path | None = None) -> tuple[int, str, str]:
    """Run a shell command asynchronously."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode if proc.returncode is not None else -1,
            stdout.decode(),
            stderr.decode(),
        )
    except (TimeoutError, asyncio.TimeoutError):  # noqa: UP041
        proc.kill()
        return -1, "", "Command timed out"


# ─── GitHub API Helpers ──────────────────────────────────────────────────────


async def gh_api(endpoint: str) -> dict:
    """Call the GitHub API via gh CLI.

    Uses GH_TOKEN env var when set (required for admin:org endpoints such as
    /orgs/{org}/actions/runners).  GH_TOKEN must be a classic PAT with
    scopes: repo, admin:org.  See docs/operations/fleet-machine-setup.md.
    """
    code, stdout, stderr = await run_cmd(["gh", "api", endpoint])
    if code != 0:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {stderr}")
    return json.loads(stdout)


# gh_api_admin is an alias kept for call-site clarity; all calls use GH_TOKEN.
gh_api_admin = gh_api


async def gh_api_raw(endpoint: str) -> str:
    """Call the GitHub API via gh CLI and return the raw body text."""
    code, stdout, stderr = await run_cmd(["gh", "api", "-H", "Accept: application/vnd.github.raw", endpoint])
    if code != 0:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {stderr}")
    return stdout


# ─── HTTP Proxying ───────────────────────────────────────────────────────────


async def proxy_to_hub(request: Request, hub_url: str) -> dict:
    """Proxy request to the designated hub_url for hub-spoke topology."""
    if not hub_url:
        raise HTTPException(status_code=502, detail="HUB_URL not configured")
    async with httpx.AsyncClient(timeout=15.0) as client:
        url = f"{hub_url}{request.url.path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
        try:
            req = client.build_request(
                request.method,
                url,
                headers={k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")},
                content=await request.body(),
            )
            resp = await client.send(req)
            # Prevent decoding errors on empty/non-json responses if necessary
            if resp.status_code == 204 or not resp.content:
                return {}
            return resp.json()
        except Exception as e:  # noqa: BLE001
            log.warning("Hub proxy error for %s: %s", request.url.path, e)
            raise HTTPException(status_code=502, detail="Hub proxy error") from e


def _should_proxy_fleet_to_hub(request: Request, machine_role: str, hub_url: str) -> bool:
    """Return True when this node should use the hub's fleet-wide view.

    Local health, system metrics, watchdog, and runner schedule endpoints stay
    local. Fleet-wide endpoints can proxy to the hub, while hub fan-out calls
    can add ``?local=1`` to force a node-local action and avoid proxy loops.
    """
    if machine_role != "node" or not hub_url:
        return False
    local_value = request.query_params.get("local", "").lower()
    scope_value = request.query_params.get("scope", "").lower()
    return local_value not in {"1", "true", "yes", "local"} and scope_value != "local"


# ─── Middleware ──────────────────────────────────────────────────────────────


async def _csrf_check(request: Request, call_next: Any) -> Any:
    """Reject state-changing requests that lack the CSRF sentinel header (issue #30).

    Browsers never send X-Requested-With cross-origin without an explicit CORS
    pre-flight, so requiring it is a lightweight CSRF mitigation suitable for a
    local-only dashboard.  The frontend must include the header on every
    POST / PUT / DELETE / PATCH request.
    """
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        # Allow health / static routes without the header so monitoring tools
        # (e.g. curl health checks) still work.  Only enforce on /api/* paths.
        if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/linear/webhook"):
            if request.headers.get("X-Requested-With") != "XMLHttpRequest":
                return JSONResponse(
                    {"error": "CSRF check failed: missing X-Requested-With header"},
                    status_code=403,
                )
    return await call_next(request)


async def _add_security_headers(request: Request, call_next: Any) -> Any:
    """Inject standard security headers on every response (issue #7, #18)."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # 'unsafe-inline' removed from script-src (issue #18).
    # 'strict-dynamic' lets scripts loaded from trusted CDN origins load further
    # dependencies without needing individual allow-list entries.
    # 'unsafe-inline' is retained for style-src because React's CSS-in-JS and
    # the dashboard's own <style> block rely on inline styles. A build step
    # would allow switching to nonce or hash-based CSP for style-src.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'strict-dynamic' "
        "https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://unpkg.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self' data:;"
    )
    return response
