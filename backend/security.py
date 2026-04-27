"""Security utilities extracted from server.py (issue #161).

Provides:
- Log value sanitization
- Subprocess environment scrubbing
- URL/path/command validation
- Dispatch rate limiting
"""

from __future__ import annotations

import ipaddress
import os
import shlex
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException

# ─── Log Sanitization ──────────────────────────────────────────────────────────


def sanitize_log_value(value: str) -> str:
    """Strip log-injection characters from user-controlled strings."""
    return value.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")[:200]


# ─── Environment Scrubbing ─────────────────────────────────────────────────────


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


# ─── URL Validation ────────────────────────────────────────────────────────────


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
        # hostname – allow localhost, .local, .internal
        if not (host == "localhost" or host.endswith(".local") or host.endswith(".internal")):
            raise ValueError(f"Fleet node hostname not allowed: {host}") from exc
    return url


def validate_local_url(url: str, field: str = "url") -> str:
    """Validate that a URL has http/https scheme and a local host (issue #23)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"{field} must use http or https")
    return validate_fleet_node_url(url)


# ─── Path Validation ───────────────────────────────────────────────────────────


def validate_local_path(path_str: str, allowed_root: Path) -> Path:
    """Resolve path and ensure it stays within allowed_root (issue #23)."""
    resolved = Path(path_str).expanduser().resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError(f"Path escapes allowed root: {path_str}") from exc
    return resolved


# ─── Command Validation ────────────────────────────────────────────────────────


def validate_health_command(cmd: str) -> list[str]:
    """Parse health command safely, rejecting shell metacharacters (issue #22)."""
    dangerous = set(";|&`$()<>")
    if any(c in cmd for c in dangerous):
        raise ValueError(f"health_command contains disallowed characters: {cmd!r}")
    return shlex.split(cmd)


# ─── Rate Limiting ───────────────────────────────────────────────────────────

_dispatch_rate: dict[str, list[float]] = defaultdict(list)
DISPATCH_LIMIT_PER_MINUTE = 10


def check_dispatch_rate(client_ip: str) -> None:
    """Enforce rate limiting for AI agent dispatch endpoints (issue #31)."""
    now = time.monotonic()
    window = [t for t in _dispatch_rate[client_ip] if now - t < 60]
    if len(window) >= DISPATCH_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Rate limit exceeded for agent dispatch")
    window.append(now)
    _dispatch_rate[client_ip] = window
