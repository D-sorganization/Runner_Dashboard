"""GitHub API utilities for runner-dashboard."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256

from cache_utils import cache_get, cache_set
from dashboard_config import DEPLOYMENT_FILE, HOSTNAME, VERSION
from fastapi import HTTPException
from system_utils import BOOT_TIME, get_deployment_info, run_cmd

UTC = timezone.utc  # noqa: UP017
log = logging.getLogger("dashboard.gh_utils")

DEFAULT_RATE_LIMIT_RETRY_AFTER_SECONDS = 60
_rate_limit_breakers: dict[tuple[str, str], float] = {}


class RateLimitedError(RuntimeError):
    """Machine-readable GitHub rate-limit failure from ``gh api``."""

    def __init__(
        self,
        *,
        retry_after_seconds: int,
        endpoint: str,
        resource_class: str,
        detail: str = "GitHub API rate limit exceeded",
    ) -> None:
        super().__init__(detail)
        self.retry_after_seconds = max(1, retry_after_seconds)
        self.endpoint = endpoint
        self.resource_class = resource_class
        self.detail = detail


def clear_rate_limit_breakers() -> None:
    """Reset in-memory GitHub rate-limit circuit breakers for tests."""
    _rate_limit_breakers.clear()


def _github_token_fingerprint() -> str:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        return "anonymous"
    return sha256(token.encode("utf-8")).hexdigest()[:16]


def _resource_class(endpoint: str) -> str:
    parts = [part for part in endpoint.split("?")[0].split("/") if part]
    if "actions" in parts:
        return "actions"
    if "repos" in parts:
        return "repos"
    if "orgs" in parts:
        return "orgs"
    return parts[0] if parts else "default"


def _rate_limit_key(endpoint: str) -> tuple[str, str]:
    return (_github_token_fingerprint(), _resource_class(endpoint))


def _parse_retry_after_seconds(text: str) -> int:
    for line in text.splitlines():
        name, separator, value = line.partition(":")
        if separator and name.strip().lower() == "retry-after":
            value = value.strip()
            if value.isdigit():
                return max(1, int(value))
            try:
                retry_at = parsedate_to_datetime(value)
            except (TypeError, ValueError):
                break
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            return max(1, int(retry_at.timestamp() - time.time()))
    return DEFAULT_RATE_LIMIT_RETRY_AFTER_SECONDS


def _looks_rate_limited(text: str) -> bool:
    lower = text.lower()
    return (
        "x-ratelimit-remaining: 0" in lower
        or "api rate limit exceeded" in lower
        or "secondary rate limit" in lower
        or "rate limit exceeded" in lower
    )


def _raise_if_circuit_open(endpoint: str) -> None:
    key = _rate_limit_key(endpoint)
    retry_until = _rate_limit_breakers.get(key)
    if retry_until is None:
        return
    retry_after = int(retry_until - time.monotonic())
    if retry_after > 0:
        raise RateLimitedError(
            retry_after_seconds=retry_after,
            endpoint=endpoint,
            resource_class=key[1],
            detail="GitHub API rate limit circuit breaker is open",
        )
    _rate_limit_breakers.pop(key, None)


def _record_rate_limit(endpoint: str, retry_after_seconds: int) -> RateLimitedError:
    key = _rate_limit_key(endpoint)
    retry_after_seconds = max(1, retry_after_seconds)
    _rate_limit_breakers[key] = time.monotonic() + retry_after_seconds
    return RateLimitedError(
        retry_after_seconds=retry_after_seconds,
        endpoint=endpoint,
        resource_class=key[1],
    )


async def gh_api(endpoint: str) -> dict:
    """Call the GitHub API via gh CLI.

    Uses GH_TOKEN env var when set (required for admin:org endpoints).
    """
    _raise_if_circuit_open(endpoint)
    code, stdout, stderr = await run_cmd(["gh", "api", endpoint])
    if code != 0:
        output = "\n".join(part for part in (stderr, stdout) if part)
        if _looks_rate_limited(output):
            raise _record_rate_limit(endpoint, _parse_retry_after_seconds(output))
        raise HTTPException(status_code=502, detail=f"GitHub API error: {stderr}")
    try:
        return json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"Invalid JSON from GitHub API: {stdout}") from exc


# gh_api_admin is an alias kept for call-site clarity.
gh_api_admin = gh_api


async def gh_api_raw(endpoint: str) -> str:
    """Call the GitHub API via gh CLI and return the raw body text."""
    code, stdout, stderr = await run_cmd(["gh", "api", "-H", "Accept: application/vnd.github.raw", endpoint])
    if code != 0:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {stderr}")
    return stdout


async def get_gh_health_summary(org: str) -> dict:
    """Core health logic for GitHub runners and dashboard state."""
    try:
        # Reuse the runner cache so health checks don't add extra API calls.
        data = cache_get("runners", 25.0)
        if data is None:
            data = await gh_api_admin(f"/orgs/{org}/actions/runners")
            cache_set("runners", data)
        gh_ok = True
        runner_count = len(data.get("runners", []))
    except Exception as exc:  # noqa: BLE001
        log.warning("GitHub health check failed: %s", exc)
        gh_ok = False
        runner_count = 0

    return {
        "status": "healthy" if gh_ok else "degraded",
        "timestamp": datetime.now(UTC).isoformat(),
        "hostname": HOSTNAME,
        "github_api": "connected" if gh_ok else "unreachable",
        "runners_registered": runner_count,
        "dashboard_uptime_seconds": int(time.time() - BOOT_TIME),
        "deployment": get_deployment_info(VERSION, DEPLOYMENT_FILE),
    }
