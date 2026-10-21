"""Child-process client: refresh GitHub tokens from the local dashboard API (#2038)."""

from __future__ import annotations

import os

import httpx


def fetch_github_token_for_run() -> str:
    """Return a fresh installation token using this run's ``FLEET_API_TOKEN``."""
    url = os.environ.get("STAFF_GITHUB_CREDENTIAL_URL", "").strip()
    fleet = os.environ.get("FLEET_API_TOKEN", "").strip()
    if not url or not fleet:
        raise RuntimeError("STAFF_GITHUB_CREDENTIAL_URL and FLEET_API_TOKEN are required")

    resp = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {fleet}",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=httpx.Timeout(10.0),
    )
    resp.raise_for_status()
    body = resp.json()
    token = str(body.get("token", "")).strip()
    if not token:
        raise RuntimeError("github-credential response missing token")
    return token
