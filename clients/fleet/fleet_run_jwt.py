"""Exchange a bootstrap fleet bearer for a per-run JWT (#3879 step 3)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fleet_validators import FleetAPIError

log = logging.getLogger("fleet.run_jwt")

_RUN_JWT_PATH = "/api/fleet/run-jwt"


def _bootstrap_token() -> str | None:
    token = os.environ.get("FLEET_API_TOKEN", "").strip()
    if token:
        return token
    rd_cfg = Path(os.environ.get("RUNNER_DASHBOARD_TOKEN_DIR") or "~/.config/runner-dashboard").expanduser()
    for name in ("token", "fleet-token"):
        path = rd_cfg / name
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    return None


def _required_env(*keys: str, label: str) -> str:
    for key in keys:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    raise RuntimeError(f"{label} is required (set one of: {', '.join(keys)})")


def fetch_run_jwt(
    *,
    run_id: str | None = None,
    principal_id: str | None = None,
    caller_node: str | None = None,
    bootstrap_token: str | None = None,
) -> tuple[str, float, str]:
    """Return ``(token, expires_at, jti)`` from the hub; never log the token."""
    from fleet_client import FleetClient  # noqa: PLC0415

    token = (bootstrap_token or _bootstrap_token() or "").strip()
    if not token:
        raise RuntimeError("bootstrap fleet bearer is required (FLEET_API_TOKEN or token file)")
    rid = (run_id or _required_env("FLEET_RUN_JWT_RUN_ID", "FLEET_SESSION", label="run_id")).strip()
    principal = (
        principal_id or _required_env("FLEET_RUN_JWT_PRINCIPAL_ID", "FLEET_AGENT", label="principal_id")
    ).strip()
    node = (caller_node or _required_env("FLEET_RUN_JWT_CALLER_NODE", "FLEET_NODE_NAME", label="caller node")).strip()
    body: dict[str, str] = {"run_id": rid, "principal_id": principal}
    client = FleetClient(token=token)
    try:
        resp = client.request(
            "POST",
            _RUN_JWT_PATH,
            body=body,
            headers={"X-Fleet-Node": node},
        )
    except FleetAPIError as exc:
        redacted = str(exc).replace(token, "[redacted]")
        raise RuntimeError(f"run-jwt request failed: {redacted}") from exc
    return _parse_run_jwt_response(resp)


def _parse_run_jwt_response(resp: Any) -> tuple[str, float, str]:
    if not isinstance(resp, dict):
        raise RuntimeError("run-jwt response was not a JSON object")
    raw = resp.get("token")
    minted = str(raw or "").strip()
    if not minted:
        raise RuntimeError("run-jwt response missing token")
    expires_at = float(resp.get("expires_at") or 0.0)
    jti = str(resp.get("jti") or "").strip()
    if not jti:
        raise RuntimeError("run-jwt response missing jti")
    return minted, expires_at, jti
