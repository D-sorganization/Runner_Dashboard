"""Linear webhook receiver with agent-agnostic dispatch conversion.

Provides a FastAPI endpoint that ingests webhook events from Linear.app,
validates payloads, converts them into the internal dispatch envelope format
defined by ``dispatch_contract``, and forwards them to the fleet dispatch
pipeline.

Security model
--------------
* Requests MUST arrive over Tailscale Funnel (see docs/tailscale-funnel.md).
* The ``Linear-Signature`` header is verified against the workspace
  ``webhook_secret_env`` secret.
* Replay protection uses the same envelope-deduplication mechanism as the
  dispatch router.
* CSRF is NOT checked for this route because it is called by an external
  service, not the browser.

See SPEC.md § "Linear Webhook Integration" and issue #243.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

import dispatch_contract
from fastapi import APIRouter, Header, HTTPException, Request
from middleware import limit_body_size
from pydantic import BaseModel, Field, field_validator

router = APIRouter(prefix="/api/linear", tags=["linear"])

log = logging.getLogger("dashboard.linear_webhook")

# ─── Configuration ───────────────────────────────────────────────────────────

LINEAR_WEBHOOK_SECRET_ENV = os.environ.get("LINEAR_WEBHOOK_SECRET_ENV", "LINEAR_WEBHOOK_SECRET")

# Maximum age of a webhook payload to prevent replay attacks (seconds)
MAX_WEBHOOK_AGE_SECONDS = 300

# ─── Pydantic Models ───────────────────────────────────────────────────────────


class LinearWebhookPayload(BaseModel):
    """Minimal validation for Linear webhook JSON body."""

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
        allowed = {"create", "update", "remove", "delete"}
        if value.lower() not in allowed:
            raise ValueError(f"unsupported webhook action: {value}")
        return value.lower()

    @field_validator("type")
    @classmethod
    def _validate_type(cls, value: str) -> str:
        known = {"issue", "comment", "issuecomment", "cycle", "project"}
        normalized = value.lower()
        if normalized not in known:
            log.warning("linear_webhook: unknown event type %r", normalized)
        return normalized


# ─── Signature Verification ────────────────────────────────────────────────────


def _verify_linear_signature(
    body: bytes,
    signature_header: str | None,
    secret: str,
) -> bool:
    """Verify the Linear-Signature header against the shared secret."""
    if not signature_header and not secret:
        log.warning("linear_webhook: signature verification skipped (no header, no secret)")
        return True

    if not signature_header:
        log.warning("linear_webhook: missing Linear-Signature header")
        return False

    if not secret:
        log.warning("linear_webhook: missing webhook secret")
        return False

    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ─── Replay / Idempotency ─────────────────────────────────────────────────────

_processed_webhook_ids: set[str] = set()


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
    if len(_processed_webhook_ids) > 10_000:
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
            import datetime as dt_mod

            ts = dt_mod.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            ts_epoch = ts.timestamp()
        else:
            ts_epoch = int(created_at) / 1000.0
    except (ValueError, TypeError, OverflowError):
        return False

    age = time.time() - ts_epoch
    return age > MAX_WEBHOOK_AGE_SECONDS


# ─── Dispatch Conversion ────────────────────────────────────────────────────────


def _build_dispatch_envelope(linear_payload: LinearWebhookPayload) -> dispatch_contract.CommandEnvelope:
    """Convert a validated Linear webhook payload into a dispatch envelope.

    The envelope uses ``agents.dispatch.issue`` so the fleet can route an
    agent to the Linear issue.  All required fields are filled from the
    webhook ``data`` block; missing values default to safe placeholders.
    """
    data = linear_payload.data
    issue_id = str(data.get("id") or data.get("identifier") or "unknown")
    issue_title = str(data.get("title") or "")
    issue_url = str(data.get("url") or "")
    team_name = str(data.get("team", {}).get("name") or "") if isinstance(data.get("team"), dict) else ""

    # Build a payload compatible with agents.dispatch.issue action
    payload = {
        "issue_id": issue_id,
        "title": issue_title,
        "url": issue_url,
        "team": team_name,
        "source": "linear",
        "action": linear_payload.action,
        "event_type": linear_payload.type,
    }

    return dispatch_contract.build_envelope(
        action="agents.dispatch.issue",
        source="linear_webhook",
        target="fleet",
        requested_by="linear_webhook",
        reason=f"Linear webhook: {linear_payload.action} {linear_payload.type}",
        payload=payload,
        correlation_id=linear_payload.webhook_id or "",
    )


# ─── Main Endpoint ─────────────────────────────────────────────────────────────


@router.post("/webhook")
@limit_body_size(256 * 1024)
async def linear_webhook(
    request: Request,
    linear_signature: str | None = Header(None, alias="Linear-Signature"),
) -> dict[str, Any]:
    """Receive and validate a Linear webhook event, then convert to dispatch envelope."""
    body = await request.body()

    # Parse JSON
    try:
        payload_raw = json.loads(body)
    except json.JSONDecodeError as exc:
        log.warning("linear_webhook: invalid JSON body: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    # Validate shape
    try:
        payload = LinearWebhookPayload.model_validate(payload_raw)
    except Exception as exc:
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

    # Build dispatch envelope for Issue events
    envelope: dispatch_contract.CommandEnvelope | None = None
    if payload.type == "issue":
        try:
            envelope = _build_dispatch_envelope(payload)
        except Exception as exc:
            log.error("linear_webhook: failed to build dispatch envelope: %s", exc, exc_info=True)

    log.info(
        "linear_webhook: accepted action=%s type=%s webhook_id=%s org=%s dispatch=%s",
        payload.action,
        payload.type,
        payload.webhook_id,
        payload.organization_id,
        envelope.envelope_id if envelope else "none",
    )

    response: dict[str, Any] = {
        "ok": True,
        "action": payload.action,
        "type": payload.type,
        "webhook_id": payload.webhook_id,
    }

    if envelope is not None:
        response["envelope"] = envelope.to_dict()

    return response


# ─── Health / Discovery ────────────────────────────────────────────────────────


@router.get("/webhook/health")
async def linear_webhook_health() -> dict[str, Any]:
    """Return the webhook receiver's operational status."""
    secret_present = bool(os.environ.get(LINEAR_WEBHOOK_SECRET_ENV, "").strip())
    return {
        "status": "ok",
        "signature_verification": "enabled" if secret_present else "disabled",
        "max_age_seconds": MAX_WEBHOOK_AGE_SECONDS,
        "replay_buffer_size": len(_processed_webhook_ids),
    }
