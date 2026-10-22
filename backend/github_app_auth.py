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


_PERMISSION_RANK: dict[str, int] = {"read": 1, "triage": 2, "write": 3, "admin": 4}


class GhInstallationPermissionError(Exception):
    """Installation grant is narrower than the permissions requested at mint time."""

    def __init__(
        self,
        missing: list[str],
        *,
        endpoint: str = "",
        body: str = "",
    ) -> None:
        detail = ", ".join(missing) if missing else body[:200]
        super().__init__(f"GitHub App installation token minting failed: missing: {detail}")
        self.missing = list(missing)
        self.endpoint = endpoint
        self.body = body


def _app_jwt_headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {build_app_jwt()}",
    }


def _normalize_permission_level(level: str | None) -> str:
    return str(level or "").strip().lower()


_MINT_UNSUPPORTED_READ: frozenset[str] = frozenset({"workflows"})


def normalize_permissions_for_mint(requested: Mapping[str, str]) -> dict[str, str]:
    """Drop permission levels GitHub's mint API rejects even when the installation lists them."""
    out: dict[str, str] = {}
    for perm, level_raw in requested.items():
        level = _normalize_permission_level(level_raw)
        if not level:
            continue
        if perm in _MINT_UNSUPPORTED_READ and level == "read":
            continue
        out[str(perm)] = level
    return out


def permissions_payload_for_mint(
    requested: Mapping[str, str],
    granted: Mapping[str, str],
) -> dict[str, str]:
    """Build the ``permissions`` object for POST access_tokens (subset of installation grant)."""
    normalized = normalize_permissions_for_mint(requested)
    missing = compare_installation_permissions(normalized, granted)
    if missing:
        raise GhInstallationPermissionError(
            missing,
            endpoint="/app/installations/.../access_tokens",
        )
    return normalized


def compare_installation_permissions(
    requested: Mapping[str, str],
    granted: Mapping[str, str],
) -> list[str]:
    """Return human-readable ``permission:level`` gaps (requested exceeds installation grant)."""
    missing: list[str] = []
    for perm, want_raw in sorted(requested.items()):
        want = _normalize_permission_level(want_raw)
        if not want:
            continue
        have_raw = granted.get(perm)
        if not have_raw:
            missing.append(f"{perm}:{want} (installation has none)")
            continue
        have = _normalize_permission_level(have_raw)
        want_rank = _PERMISSION_RANK.get(want, 0)
        have_rank = _PERMISSION_RANK.get(have, 0)
        if want_rank > have_rank:
            missing.append(f"{perm}:{want} (installation has {perm}:{have})")
    return missing


def fetch_installation_permissions(
    *,
    client: httpx.Client | None = None,
    timeout: float = 30.0,
) -> dict[str, str]:
    """Read ``permissions`` from GET /app/installations/{installation_id} (App JWT)."""
    from gh_client import GhAuthError, GhServerError

    if not github_app_auth_configured():
        raise GhAuthError("GitHub App auth is not configured on this node")

    installation_id = os.environ["GITHUB_APP_INSTALLATION_ID"].strip()
    url = f"{_GITHUB_API_BASE}/app/installations/{installation_id}"
    headers = _app_jwt_headers()
    timeout_cfg = httpx.Timeout(connect=5.0, read=timeout, write=10.0, pool=5.0)

    if client is not None:
        resp = client.get(url, headers=headers)
    else:
        with httpx.Client(timeout=timeout_cfg) as owned:
            resp = owned.get(url, headers=headers)

    if resp.status_code != 200:
        raise GhServerError(resp.status_code, f"/app/installations/{installation_id}", resp.text)

    body = resp.json()
    raw = body.get("permissions") if isinstance(body, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {str(k): _normalize_permission_level(str(v)) for k, v in raw.items() if v}


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
    headers = _app_jwt_headers()
    mint_permissions: dict[str, str] | None = None
    if permissions:
        mint_permissions = normalize_permissions_for_mint(permissions)

    timeout_cfg = httpx.Timeout(connect=5.0, read=timeout, write=10.0, pool=5.0)
    with httpx.Client(timeout=timeout_cfg) as client:
        payload: dict[str, Any] = {}
        if repositories:
            payload["repositories"] = list(repositories)
        if mint_permissions:
            granted = fetch_installation_permissions(client=client, timeout=timeout)
            mint_permissions = permissions_payload_for_mint(mint_permissions, granted)
            payload["permissions"] = mint_permissions

        resp = client.post(url, headers=headers, json=payload or None)
        if resp.status_code not in (200, 201):
            if resp.status_code == 422 and mint_permissions:
                granted = fetch_installation_permissions(client=client, timeout=timeout)
                missing = compare_installation_permissions(mint_permissions, granted)
                if not missing:
                    missing = [
                        "GitHub rejected token mint (422); permissions may be invalid for mint "
                        f"even though installation metadata lists them: {resp.text[:160]}"
                    ]
                raise GhInstallationPermissionError(
                    missing,
                    endpoint="/app/installations/.../access_tokens",
                    body=resp.text,
                )
            raise GhServerError(resp.status_code, "/app/installations/.../access_tokens", resp.text)
        return _parse_installation_token_response(resp.json())
