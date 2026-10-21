"""Spoke-node client: request GitHub installation tokens from the fleet hub (#2051)."""

from __future__ import annotations

import logging
import os

import httpx
from dashboard_config import HOSTNAME, HUB_URL
from staff.github_run_env import StaffGithubToken

log = logging.getLogger("dashboard.staff.fleet_run_token")


def mint_staff_run_github_token_via_hub(role_name: str, repo: str, run_id: str) -> StaffGithubToken:
    """Ask the hub to mint a GitHub App installation token for one staff run.

    Pre: ``HUB_URL`` and ``HUB_FLEET_TOKEN`` are configured on this node.
    Post: returns a token minted by the hub; raises ``RuntimeError`` when the hub refuses.
    """
    hub_raw = HUB_URL if HUB_URL is not None else os.environ.get("HUB_URL") or ""
    hub = hub_raw.strip().rstrip("/")
    fleet = os.environ.get("HUB_FLEET_TOKEN", "").strip()
    rid = str(run_id or "").strip()
    if not hub or not fleet:
        raise RuntimeError(
            "Staff GitHub tokens on fleet nodes require HUB_URL and HUB_FLEET_TOKEN; "
            "configure hub fleet credentials on this node."
        )
    if not rid:
        raise RuntimeError("run_id is required to mint a staff GitHub token via the hub")

    repositories = [repo] if str(repo or "").strip() else []
    url = f"{hub}/api/fleet/run-token"
    try:
        resp = httpx.post(
            url,
            json={"run_id": rid, "role": role_name, "repositories": repositories},
            headers={
                "Authorization": f"Bearer {fleet}",
                "X-Requested-With": "XMLHttpRequest",
                "X-Fleet-Node": HOSTNAME,
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(10.0),
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"hub run-token endpoint unreachable at {url}: {exc}") from exc

    if resp.status_code >= 400:
        detail = resp.text[:200]
        try:
            body = resp.json()
            if isinstance(body, dict) and body.get("detail"):
                detail = str(body["detail"])
        except ValueError:
            pass
        raise RuntimeError(f"hub refused staff GitHub token ({resp.status_code}): {detail}")

    body = resp.json()
    token = str(body.get("token", "")).strip()
    if not token:
        raise RuntimeError("hub run-token response missing token")
    expires_at = float(body.get("expires_at") or 0.0)
    log.debug("Received hub-minted GitHub token for run %s", rid)
    return StaffGithubToken(token=token, expires_at=expires_at, source="hub_github_app")
