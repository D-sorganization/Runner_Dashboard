"""Fail-closed WebAuthn scaffolding for mobile biometric unlock.

This module deliberately does not complete registration or assertion ceremonies
without a verifier implementation. It provides the authenticated route surface,
server-generated challenges, and credential metadata storage needed for the next
slice that wires in a pinned WebAuthn library.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, require_principal
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/auth/webauthn", tags=["auth"])

_CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "runner-dashboard"
_CREDENTIALS_PATH = Path(os.environ.get("DASHBOARD_WEBAUTHN_CREDENTIALS", _CONFIG_DIR / "webauthn_credentials.json"))
_CHALLENGE_SESSION_KEY = "webauthn_challenges"
_CHALLENGE_TTL_SECONDS = 300


class WebAuthnCredentialRecord(BaseModel):
    """Metadata for a registered WebAuthn credential."""

    user_id: str
    credential_id: str
    public_key: str
    sign_count: int = Field(ge=0)
    label: str | None = None
    created_at: float = Field(default_factory=time.time)
    revoked_at: float | None = None


class WebAuthnChallenge(BaseModel):
    """Server-generated challenge returned to the browser."""

    challenge: str
    signature: str
    user_id: str
    ceremony: str
    expires_at: float


class RegisterBeginRequest(BaseModel):
    label: str | None = Field(default=None, max_length=120)


class RegisterCompleteRequest(BaseModel):
    credential: dict[str, Any]


class AssertBeginRequest(BaseModel):
    credential_id: str | None = None


class AssertCompleteRequest(BaseModel):
    credential: dict[str, Any]


def _b64url_token(byte_count: int = 32) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(byte_count)).decode("ascii").rstrip("=")


def _challenge_secret() -> bytes:
    secret = os.environ.get("DASHBOARD_WEBAUTHN_CHALLENGE_SECRET") or os.environ.get("SESSION_SECRET")
    if not secret:
        secret = os.environ.get("DASHBOARD_API_KEY", "runner-dashboard-development-secret")
    return secret.encode("utf-8")


def _sign_challenge(challenge: str, user_id: str, ceremony: str, expires_at: float) -> str:
    payload = f"{challenge}.{user_id}.{ceremony}.{int(expires_at)}".encode()
    digest = hmac.new(_challenge_secret(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _load_credentials(path: Path = _CREDENTIALS_PATH) -> list[WebAuthnCredentialRecord]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    records: list[WebAuthnCredentialRecord] = []
    for item in raw:
        if isinstance(item, dict):
            records.append(WebAuthnCredentialRecord(**item))
    return records


def _save_credentials(records: list[WebAuthnCredentialRecord], path: Path = _CREDENTIALS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [record.model_dump() for record in records]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _active_credentials_for(user_id: str) -> list[WebAuthnCredentialRecord]:
    return [record for record in _load_credentials() if record.user_id == user_id and record.revoked_at is None]


def _store_challenge(request: Request, challenge: WebAuthnChallenge) -> None:
    if not hasattr(request, "session"):
        raise HTTPException(status_code=500, detail="Session middleware is required for WebAuthn")
    stored = request.session.get(_CHALLENGE_SESSION_KEY, [])
    if not isinstance(stored, list):
        stored = []
    now = time.time()
    stored = [item for item in stored if isinstance(item, dict) and float(item.get("expires_at", 0)) > now]
    stored.append(challenge.model_dump())
    request.session[_CHALLENGE_SESSION_KEY] = stored


def _new_challenge(request: Request, principal: Principal, ceremony: str) -> WebAuthnChallenge:
    challenge_value = _b64url_token()
    expires_at = time.time() + _CHALLENGE_TTL_SECONDS
    challenge = WebAuthnChallenge(
        challenge=challenge_value,
        signature=_sign_challenge(challenge_value, principal.id, ceremony, expires_at),
        user_id=principal.id,
        ceremony=ceremony,
        expires_at=expires_at,
    )
    _store_challenge(request, challenge)
    return challenge


@router.post("/register/begin")
async def register_begin(
    request: Request,
    _body: RegisterBeginRequest,
    principal: Principal = Depends(require_principal),  # noqa: B008
) -> dict[str, Any]:
    """Start device registration after the existing session is authenticated."""
    challenge = _new_challenge(request, principal, "register")
    return {
        "challenge": challenge.challenge,
        "challenge_signature": challenge.signature,
        "user": {"id": principal.id, "name": principal.name},
        "rp": {"name": "D-sorganization Runner Dashboard"},
        "timeout_ms": _CHALLENGE_TTL_SECONDS * 1000,
    }


@router.post("/register/complete")
async def register_complete(
    _body: RegisterCompleteRequest,
    _principal: Principal = Depends(require_principal),  # noqa: B008
) -> None:
    """Fail closed until a pinned WebAuthn verifier validates attestation."""
    raise HTTPException(status_code=501, detail="WebAuthn registration verification is not implemented")


@router.post("/assert/begin")
async def assert_begin(
    request: Request,
    body: AssertBeginRequest,
    principal: Principal = Depends(require_principal),  # noqa: B008
) -> dict[str, Any]:
    """Start an assertion ceremony for the current authenticated user."""
    credentials = _active_credentials_for(principal.id)
    if body.credential_id:
        credentials = [record for record in credentials if record.credential_id == body.credential_id]
    if not credentials:
        raise HTTPException(status_code=404, detail="No active WebAuthn credential registered for this user")
    challenge = _new_challenge(request, principal, "assert")
    return {
        "challenge": challenge.challenge,
        "challenge_signature": challenge.signature,
        "allow_credentials": [{"id": record.credential_id, "type": "public-key"} for record in credentials],
        "timeout_ms": _CHALLENGE_TTL_SECONDS * 1000,
    }


@router.post("/assert/complete")
async def assert_complete(
    _body: AssertCompleteRequest,
    _principal: Principal = Depends(require_principal),  # noqa: B008
) -> None:
    """Fail closed until a pinned WebAuthn verifier validates assertion data."""
    raise HTTPException(status_code=501, detail="WebAuthn assertion verification is not implemented")


@router.get("/credentials")
async def list_webauthn_credentials(principal: Principal = Depends(require_principal)) -> dict[str, Any]:  # noqa: B008
    """List active credential metadata for the current principal."""
    return {
        "credentials": [
            {
                "credential_id": record.credential_id,
                "label": record.label,
                "created_at": record.created_at,
                "sign_count": record.sign_count,
            }
            for record in _active_credentials_for(principal.id)
        ]
    }


@router.delete("/credentials/{credential_id}")
async def revoke_webauthn_credential(
    credential_id: str,
    principal: Principal = Depends(require_principal),  # noqa: B008
) -> dict[str, Any]:
    """Revoke one WebAuthn credential for the current principal."""
    records = _load_credentials()
    changed = False
    now = time.time()
    for record in records:
        if record.user_id == principal.id and record.credential_id == credential_id and record.revoked_at is None:
            record.revoked_at = now
            changed = True
    if not changed:
        raise HTTPException(status_code=404, detail="Credential not found")
    _save_credentials(records)
    return {"status": "revoked", "credential_id": credential_id}
