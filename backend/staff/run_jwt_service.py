"""Per-run JWT credentials: hub minting and bearer verification (#3879).

Contract: docs/architecture/per-run-jwt-credentials.md. Minting is hub-only;
verification runs on every ``IdentityManager.resolve_bearer_token`` call for
tokens in the ``runner-dashboard-agent-run`` issuer family (fail closed).
Static ``agent-tokens/*.token`` ingestion is unchanged.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import dashboard_config
import jwt
from fastapi import HTTPException
from fleet_autoconfig import resolve_hub_public_origin_from_registry
from identity import IdentityManager
from machine_registry import load_machine_registry
from staff.fleet_run_token_service import assert_registered_fleet_node

log = logging.getLogger("dashboard.staff.run_jwt")

ISSUER = "runner-dashboard-agent-run"
DEFAULT_TTL_SECONDS = 15 * 60
_MAX_JWT_BYTES = 16_384


def _uses_run_jwt_issuer(raw_token: str) -> bool:
    if not isinstance(raw_token, str) or not raw_token or len(raw_token) > _MAX_JWT_BYTES:
        return False
    if raw_token.count(".") != 2:
        return False
    try:
        claims = jwt.decode(raw_token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return False
    return claims.get("iss") == ISSUER


def run_jwt_bearer_gate(raw_token: str) -> str | None:
    """Cryptographically verify a run JWT before hash lookup.

    Returns ``None`` when ``raw_token`` is not in the run-JWT issuer family,
    or when verification succeeded. Otherwise returns a reason code (never logs
    the token).
    """
    if not _uses_run_jwt_issuer(raw_token):
        return None
    if not run_jwt_signing_key_configured():
        return "run_jwt_verification_unavailable"
    audience = resolve_hub_public_origin_from_registry(load_machine_registry())
    if not audience:
        return "run_jwt_verification_unavailable"
    try:
        claims = jwt.decode(
            raw_token,
            run_jwt_signing_key(),
            algorithms=["HS256"],
            audience=audience,
            issuer=ISSUER,
            options={"require": ["exp", "nbf", "sub", "jti", "run_id"]},
            leeway=0,
        )
    except jwt.ExpiredSignatureError:
        return "run_jwt_expired"
    except jwt.InvalidAudienceError:
        return "run_jwt_audience_mismatch"
    except jwt.InvalidIssuerError:
        return "run_jwt_issuer_mismatch"
    except jwt.PyJWTError:
        return "run_jwt_invalid"
    run_id = claims.get("run_id")
    jti = claims.get("jti")
    sub = claims.get("sub")
    if not isinstance(run_id, str) or not run_id.strip():
        return "run_jwt_claims_invalid"
    if not isinstance(jti, str) or not jti.strip():
        return "run_jwt_claims_invalid"
    if not isinstance(sub, str) or not sub.strip():
        return "run_jwt_claims_invalid"
    return None


def revoke_run_jwt(identity_manager: IdentityManager, raw_token: str) -> None:
    """Revoke one minted run JWT by its raw bearer value (hash-only storage)."""
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    identity_manager.revoke_ephemeral_token(token_hash)


def run_jwt_signing_key_configured() -> bool:
    return bool(
        os.environ.get("RUN_JWT_SIGNING_KEY", "").strip() or os.environ.get("RUN_JWT_SIGNING_KEY_FILE", "").strip()
    )


def run_jwt_signing_key() -> str:
    key = os.environ.get("RUN_JWT_SIGNING_KEY", "").strip()
    if key:
        return key
    key_file = os.environ.get("RUN_JWT_SIGNING_KEY_FILE", "").strip()
    if not key_file:
        return ""
    return Path(key_file).expanduser().read_text(encoding="utf-8").strip()


@dataclass(frozen=True)
class RunJwtCredential:
    token: str
    expires_at: float
    jti: str


def mint_run_jwt(
    identity_manager: IdentityManager,
    *,
    run_id: str,
    principal_id: str,
    caller_node: str,
    now: float | None = None,
) -> RunJwtCredential:
    """Mint a short-lived, run-scoped JWT and register it as a revocable ephemeral token.

    Pre: this process is the fleet hub, ``principal_id`` names an existing
    ``Principal`` (static or ephemeral), and ``caller_node`` is a registered,
    non-retired, non-hub fleet node.
    Post: the returned token resolves through ``IdentityManager.resolve_bearer_token``
    exactly as the existing ``principal_id`` would via a static token, until
    its ``exp`` or an explicit ``revoke_ephemeral_token(token_hash)`` call.
    """
    if dashboard_config.MACHINE_ROLE != "hub":
        raise HTTPException(status_code=404, detail="run-jwt minting is only available on the fleet hub")
    rid = str(run_id or "").strip()
    if not rid:
        raise HTTPException(status_code=422, detail="run_id is required")
    assert_registered_fleet_node(caller_node)
    principal = identity_manager.get_principal(str(principal_id or "").strip())
    if principal is None:
        raise HTTPException(status_code=422, detail=f"unknown principal {principal_id!r}")
    if not run_jwt_signing_key_configured():
        raise HTTPException(status_code=503, detail="RUN_JWT_SIGNING_KEY is not configured on the hub")

    audience = resolve_hub_public_origin_from_registry(load_machine_registry())
    if not audience:
        raise HTTPException(status_code=503, detail="fleet hub public origin is not uniquely resolvable")

    issued_at = now if now is not None else time.time()
    expires_at = issued_at + DEFAULT_TTL_SECONDS
    jti = uuid.uuid4().hex
    payload = {
        "sub": principal.id,
        "run_id": rid,
        "jti": jti,
        "aud": audience,
        "iss": ISSUER,
        "iat": int(issued_at),
        "nbf": int(issued_at),
        "exp": int(expires_at),
    }
    raw_token = jwt.encode(payload, run_jwt_signing_key(), algorithm="HS256")
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    identity_manager.add_ephemeral_token(token_hash, principal, expires_at=expires_at, name=f"run:{rid}")
    log.info("minted run JWT jti=%s run_id=%s principal=%s", jti, rid, principal.id)
    return RunJwtCredential(token=raw_token, expires_at=expires_at, jti=jti)
