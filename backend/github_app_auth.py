"""GitHub App JWT and installation access-token minting (#938, #2038).

Shared between the async ``gh_client`` dashboard cache and synchronous staff-run
credential injection. Staff runs mint scoped, short-lived tokens per run; the
dashboard cache uses an unscoped installation token.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("dashboard.github_app_auth")

_GITHUB_API_BASE = "https://api.github.com"


def github_app_auth_configured() -> bool:
    return bool(
        os.environ.get("GITHUB_APP_ID", "").strip()
        and os.environ.get("GITHUB_APP_INSTALLATION_ID", "").strip()
        and github_app_private_key_configured()
    )


def github_app_private_key_configured() -> bool:
    return bool(
        os.environ.get("GITHUB_APP_PRIVATE_KEY", "").strip()
        or os.environ.get("GITHUB_APP_PRIVATE_KEY_FILE", "").strip()
    )


def github_app_private_key() -> str:
    key = os.environ.get("GITHUB_APP_PRIVATE_KEY", "").strip()
    if key:
        return key.replace("\\n", "\n")
    key_file = os.environ.get("GITHUB_APP_PRIVATE_KEY_FILE", "").strip()
    if not key_file:
        return ""
    return Path(key_file).expanduser().read_text(encoding="utf-8")


def build_app_jwt(now: datetime | None = None) -> str:
    import jwt

    app_id = os.environ["GITHUB_APP_ID"].strip()
    issued_at = now or datetime.now(UTC)
    payload = {
        "iat": int((issued_at - timedelta(seconds=60)).timestamp()),
        "exp": int((issued_at + timedelta(minutes=9)).timestamp()),
        "iss": app_id,
    }
    return jwt.encode(payload, github_app_private_key(), algorithm="RS256")


@dataclass(frozen=True)
class InstallationToken:
    token: str
    expires_at: float


def _parse_installation_token_response(body: dict[str, Any]) -> InstallationToken:
    from gh_client import GhAuthError

    token = str(body.get("token", "")).strip()
    if not token:
        raise GhAuthError("GitHub App installation token response did not include token")
    expires_at_raw = str(body.get("expires_at", "")).strip()
    try:
        expiry = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        expiry = time.time() + 3300
    return InstallationToken(token=token, expires_at=expiry)


def mint_installation_access_token(
    *,
    repositories: Sequence[str] | None = None,
    permissions: Mapping[str, str] | None = None,
    timeout: float = 30.0,
) -> InstallationToken:
    """Exchange a GitHub App JWT for an installation access token (sync, #2038).

    Pre: ``github_app_auth_configured()`` is true.
    Post: returned token is non-empty and ``expires_at`` is in the future.
    """
    from gh_client import GhAuthError, GhServerError

    if not github_app_auth_configured():
        raise GhAuthError("GitHub App auth is not configured on this node")

    installation_id = os.environ["GITHUB_APP_INSTALLATION_ID"].strip()
    url = f"{_GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {build_app_jwt()}",
    }
    payload: dict[str, Any] = {}
    if repositories:
        payload["repositories"] = list(repositories)
    if permissions:
        payload["permissions"] = dict(permissions)

    with httpx.Client(timeout=httpx.Timeout(connect=5.0, read=timeout, write=10.0, pool=5.0)) as client:
        resp = client.post(url, headers=headers, json=payload or None)
    if resp.status_code not in (200, 201):
        raise GhServerError(resp.status_code, "/app/installations/.../access_tokens", resp.text)
    return _parse_installation_token_response(resp.json())
