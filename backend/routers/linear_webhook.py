"""Linear webhook receiver.

Provides a FastAPI endpoint that ingests webhook events from Linear.app,
validates payloads, and forwards them into the dashboard issue pipeline.

Security model
--------------
* Requests MUST arrive over Tailscale Funnel (see docs/tailscale-funnel.md).
* The ``Linear-Signature`` header is verified against the workspace
  ``webhook_secret_env`` secret (stubbed; see ``_verify_linear_signature``).
* Replay protection uses the same envelope-deduplication mechanism as the
  dispatch router.
* CSRF is NOT checked for this route because it is called by an external
  service, not the browser.

See SPEC.md § "Linear Webhook Integration" and issue #242.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

router = APIRouter(prefix="/api/linear", tags=["linear"])

log = logging.getLogger("dashboard.linear_webhook")

# ─── Configuration ───────────────────────────────────────────────────────────

LINEAR_WEBHOOK_SECRET_ENV = os.environ.get("LINEAR_WEBHOOK_SECRET_ENV", "LINEAR_WEBHOOK_SECRET")

# Maximum age of a webhook payload to prevent replay attacks (seconds)
MAX_WEBHOOK_AGE_SECONDS = 300

# ─── Pydantic Models ───────────────────────────────────────────────────────────


class LinearWebhookPayload(BaseModel):
    """Minimal validation for Linear webhook JSON body.

    Linear's webhook format is documented at:
    https://developers.linear.app/docs/graphql/webhooks
    """

    model_config = {"populate_by_name": True, "str_strip_whitespace": True}

    action: str = Field(..., max_length=50)
    type: str = Field(..., max_length=50)
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: str | int | None = Field(default=None, alias="createdAt")
    webhook_id: str | None = Field(default=None, alias="webhookId", max_length=100)
    organization_id: str | None = Field(default=None, alias="organizationId", max_length=100)

    @field_validator("action")
    @classmethod
    def _validate_action(cls, value: str) -> str:
        allowed = {
            "create",
            "update",
            "remove",
            "delete",
        }
        if value.lower() not in allowed:
            raise ValueError(f"unsupported webhook action: {value}")
        return value.lower()

    @field_validator("type")
    @classmethod
    def _validate_type(cls, value: str) -> str:
        # Linear sends singular nouns; we normalize to lowercase.
        known = {
            "issue",
            "comment",
            "issuecomment",
            "cycle",
            "project",
        }
        normalized = value.lower()
        if normalized not in known:
            # Accept unknown types so Linear schema additions don't break us,
            # but log a warning.
            log.warning("linear_webhook: unknown event type %r", normalized)
        return normalized


# ─── Signature Verification (stub) ─────────────────────────────────────────────


def _verify_linear_signature(
    body: bytes,
    signature_header: str | None,
    secret: str,
) -> bool:
    """Verify the Linear-Signature header against the shared secret.

    Linear signs payloads with HMAC-SHA256 and sends the hex digest in the
    ``Linear-Signature`` header.  This stub implements the verification; in
    production the secret is read from the workspace's ``webhook_secret_env``
    environment variable.

    Parameters
    ----------
    body:
        Raw request body bytes.
    signature_header:
        Value of the ``Linear-Signature`` header.
    secret:
        Shared webhook secret for the workspace.

    Returns
    -------
    bool
        ``True`` when the signature matches or when *both* the header and
        secret are absent (local development only).  ``False`` otherwise.
    """
    if not signature_header and not secret:
        # Local dev shortcut – both missing is allowed, but logged.
        log.warning("linear_webhook: signature verification skipped (no header, no secret)")
        return True

    if not signature_header:
        log.warning("linear_webhook: missing Linear-Signature header")
        return False

    if not secret:
        log.warning("linear_webhook: missing webhook secret")
        return False

    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    # Use constant-time comparison to avoid timing attacks
    return hmac.compare_digest(expected, signature_header)


# ─── Replay / Idempotency ──────────────────────────────────────────────────────

_processed_webhook_ids: set[str] = set()
"""(In-memory) set of recently processed webhook IDs.

Production deployments should replace this with Redis or a persistent cache
so that replay protection survives process restarts.
"""


def _is_replay(webhook_id: str | None) -> bool:
    """Return True if we have already processed this webhook."""
    if webhook_id is None:
        return False
    return webhook_id in _processed_webhook_ids


def _record_webhook(webhook_id: str | None) -> None:
    """Record a webhook ID as processed."""
    if webhook_id is None:
        return
    _processed_webhook_ids.add(webhook_id)
    # Prevent unbounded memory growth
    if len(_processed_webhook_ids) > 10_000:
        # Simple eviction: clear half the set.  In production use Redis TTL.
        to_remove = list(_processed_webhook_ids)[:5_000]
        for item in to_remove:
            _processed_webhook_ids.discard(item)


# ─── Request Age Check ─────────────────────────────────────────────────────────


def _payload_too_old(created_at: str | int | None) -> bool:
    """Return True if the payload timestamp is too old (replay protection)."""
    if created_at is None:
        return False
    try:
        if isinstance(created_at, str):
            # ISO-8601 string
            import datetime as dt_mod

            ts = dt_mod.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            ts_epoch = ts.timestamp()
        else:
            ts_epoch = int(created_at) / 1000.0  # Linear often sends ms
    except (ValueError, TypeError, OverflowError):
        return False

    age = time.time() - ts_epoch
    return age > MAX_WEBHOOK_AGE_SECONDS


# ─── Main Endpoint ─────────────────────────────────────────────────────────────


@router.post("/webhook")
async def linear_webhook(
    request: Request,
    linear_signature: str | None = Header(None, alias="Linear-Signature"),
) -> dict[str, Any]:
    """Receive and validate a Linear webhook event.

    1. Reads the raw body for signature verification.
    2. Validates JSON against ``LinearWebhookPayload``.
    3. Verifies ``Linear-Signature`` (stub; reads secret from env).
    4. Checks replay / age guards.
    5. Logs a sanitized summary and returns 200 so Linear doesn't retry.

    Returns
    -------
    dict
        ``{"ok": True, "action": ..., "type": ...}`` on success.
    """
    body = await request.body()

    # Parse JSON (do this before signature check so we can log the webhookId)
    try:
        payload_raw = json.loads(body)
    except json.JSONDecodeError as exc:
        log.warning("linear_webhook: invalid JSON body: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    # Validate shape
    try:
        payload = LinearWebhookPayload.model_validate(payload_raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("linear_webhook: payload validation failed: %s", exc)
        raise HTTPException(status_code=422, detail="Payload validation failed") from exc

    # Signature verification
    secret = os.environ.get(LINEAR_WEBHOOK_SECRET_ENV, "")
    if not _verify_linear_signature(body, linear_signature, secret):
        log.warning(
            "linear_webhook: signature verification failed (webhook_id=%s)",
            payload.webhook_id,
        )
        raise HTTPException(status_code=401, detail="Signature verification failed")

    # Replay protection
    if _is_replay(payload.webhook_id):
        log.info("linear_webhook: replay detected for webhook_id=%s", payload.webhook_id)
        return {"ok": True, "replay": True, "action": payload.action, "type": payload.type}

    # Age check
    if _payload_too_old(payload.created_at):
        log.warning(
            "linear_webhook: payload too old (webhook_id=%s, created_at=%s)",
            payload.webhook_id,
            payload.created_at,
        )
        raise HTTPException(status_code=400, detail="Payload too old")

    # Record as processed
    _record_webhook(payload.webhook_id)

    # Sanitized logging – never log the full issue body, just metadata
    log.info(
        "linear_webhook: accepted action=%s type=%s webhook_id=%s org=%s",
        payload.action,
        payload.type,
        payload.webhook_id,
        payload.organization_id,
    )

    # TODO(#242-followup): Route into issue pipeline when type == "Issue"
    # For now we acknowledge receipt and return the parsed metadata.
    return {
        "ok": True,
        "action": payload.action,
        "type": payload.type,
        "webhook_id": payload.webhook_id,
    }


# ─── Health / Discovery ────────────────────────────────────────────────────────


@router.get("/webhook/health")
async def linear_webhook_health() -> dict[str, Any]:
    """Return the webhook receiver's operational status.

    Useful for Tailscale Funnel health checks and monitoring.
    """
    secret_present = bool(os.environ.get(LINEAR_WEBHOOK_SECRET_ENV, "").strip())
    return {
        "status": "ok",
        "signature_verification": "enabled" if secret_present else "disabled",
        "max_age_seconds": MAX_WEBHOOK_AGE_SECONDS,
        "replay_buffer_size": len(_processed_webhook_ids),
    }
