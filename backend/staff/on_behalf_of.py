"""Signed X-Staff-On-Behalf-Of delegation for hub→peer staff dispatches (#1311, #4025)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

log = logging.getLogger("dashboard.staff.on_behalf_of")


def hub_fleet_bearer_authenticated(header_token: str | None) -> bool:
    """True when ``Authorization`` presents the configured ``HUB_FLEET_TOKEN`` (#4025)."""
    hub_token = os.environ.get("HUB_FLEET_TOKEN", "")
    if not hub_token or not header_token or not header_token.startswith("Bearer "):
        return False
    return hmac.compare_digest(header_token[7:], hub_token)


def sign_on_behalf_of(
    principal: str,
    surface: str = "api",
    thread_id: str = "",
    request_id: str = "",
    secret: str | None = None,
    origin_node: str = "",
) -> str:
    """Create a signed X-Staff-On-Behalf-Of header value (issue #1311).

    Format: base64url(json_payload).hmac_signature_hex
    """
    key = secret if secret is not None else os.environ.get("HUB_FLEET_TOKEN") or os.environ.get("SESSION_SECRET") or ""
    payload = {
        "principal": principal,
        "surface": surface,
        "thread_id": thread_id,
        "request_id": request_id,
        "origin_node": origin_node,
        "iat": int(time.time()),
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_json).decode("ascii")
    sig = hmac.new(key.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def verify_on_behalf_of(
    header: str,
    secret: str | None = None,
    ttl_seconds: int = 300,
) -> dict[str, Any] | None:
    """Verify an X-Staff-On-Behalf-Of header value (issue #1311).

    Returns payload dict if valid, unexpired, and non-empty principal, else None.
    """
    key = secret if secret is not None else os.environ.get("HUB_FLEET_TOKEN") or os.environ.get("SESSION_SECRET") or ""
    if not header or "." not in header:
        return None
    try:
        payload_b64, sig = header.split(".", 1)
        expected_sig = hmac.new(key.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        payload_json = base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8")
        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            return None
        principal = payload.get("principal")
        if not principal or not isinstance(principal, str) or not principal.strip():
            return None
        iat = payload.get("iat")
        if not isinstance(iat, (int, float)):
            return None
        now = time.time()
        if now - iat > ttl_seconds or iat - now > 60:
            return None
        return payload
    except Exception as exc:  # noqa: BLE001
        log.debug("verify_on_behalf_of failed: %s", exc)
        return None


def is_fleet_peer(principal: Any) -> bool:
    """True if principal is authenticated as a fleet peer (issue #1311)."""
    roles = getattr(principal, "roles", []) or []
    pid = getattr(principal, "id", "")
    return pid in ("fleet-peer", "test-peer") or "fleet-peer" in roles


def caller_identity(principal: Any) -> str:
    """Format principal identity for run records (issue #1311)."""
    pid = getattr(principal, "id", str(principal))
    if pid in (
        "fleet-peer",
        "__loopback__",
        "loopback-dev",
        "test-orchestrator",
        "test-peer",
    ):
        return pid
    if pid.startswith("user:") or pid.startswith("principal:"):
        return pid
    return f"principal:{pid}"


def extract_on_behalf_of(
    header: str | None,
    is_peer: bool,
    *,
    fleet_bearer_authenticated: bool = False,
) -> dict[str, Any] | None:
    """Validate and unpack on-behalf-of header for trusted fleet forwarders (#1311, #4025)."""
    if not header:
        return None
    verified = verify_on_behalf_of(header)
    trusted_forwarder = is_peer or fleet_bearer_authenticated
    if verified is None:
        if trusted_forwarder:
            log.warning("staff: invalid X-Staff-On-Behalf-Of header from peer")
        else:
            log.warning("staff: ignored X-Staff-On-Behalf-Of header from non-peer")
        return None
    if not trusted_forwarder:
        log.warning("staff: ignored X-Staff-On-Behalf-Of header from non-peer")
        return None
    return verified
