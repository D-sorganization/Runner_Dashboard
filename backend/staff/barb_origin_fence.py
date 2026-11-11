"""Signed Barb lease attestation for hub→peer staff dispatches (#4357).

The Barb continuity store is hub-local; a peer must not re-check the lease against its
own store when the originating hub already fenced the write. The hub signs a short-lived
claim after a successful origin fence; only a trusted fleet forwarder (peer principal or
``HUB_FLEET_TOKEN`` bearer) may present it, and the signature must match the declared
``X-Barb-*`` headers on the same request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

from staff.barb_lease_fence import BarbClaim

log = logging.getLogger("dashboard.staff.barb_origin_fence")

ORIGIN_FENCE_HEADER = "x-barb-origin-fence"
DEFAULT_TTL_SECONDS = 300


def _signing_key() -> str:
    return os.environ.get("HUB_FLEET_TOKEN") or os.environ.get("SESSION_SECRET") or ""


def sign_barb_origin_fence(claim: BarbClaim, *, origin_node: str) -> str:
    """Build ``X-Barb-Origin-Fence`` after the origin hub validated the lease (#4357)."""
    key = _signing_key()
    payload = {
        "scope": claim.scope,
        "epoch": claim.epoch,
        "holder": claim.holder,
        "origin_node": origin_node,
        "iat": int(time.time()),
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_json).decode("ascii")
    sig = hmac.new(key.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def verify_barb_origin_fence(
    header: str | None,
    claim: BarbClaim,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> bool:
    """True when ``header`` is a valid, unexpired attestation for ``claim``."""
    key = _signing_key()
    if not key or not header or "." not in header:
        return False
    try:
        payload_b64, sig = header.split(".", 1)
        expected = hmac.new(key.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        payload_json = base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8")
        payload: dict[str, Any] = json.loads(payload_json)
        if not isinstance(payload, dict):
            return False
        iat = payload.get("iat")
        if not isinstance(iat, (int, float)):
            return False
        now = time.time()
        if now - iat > ttl_seconds or iat - now > 60:
            return False
        if str(payload.get("scope") or "") != claim.scope:
            return False
        if int(payload.get("epoch") or 0) != claim.epoch:
            return False
        declared_holder = str(payload.get("holder") or "")
        if declared_holder != (claim.holder or ""):
            return False
        return bool(str(payload.get("origin_node") or "").strip())
    except Exception as exc:  # noqa: BLE001
        log.debug("verify_barb_origin_fence failed: %s", exc)
        return False


def apply_origin_fence_trust(
    claim: BarbClaim | None,
    header: str | None,
    *,
    trusted_forwarder: bool,
) -> BarbClaim | None:
    """Return a claim marked origin-validated when the attestation checks out (#4357)."""
    if claim is None or not trusted_forwarder:
        return claim
    if verify_barb_origin_fence(header, claim):
        return BarbClaim(
            scope=claim.scope,
            epoch=claim.epoch,
            caller_id=claim.caller_id,
            holder=claim.holder,
            lease_validated_at_origin=True,
        )
    return claim
