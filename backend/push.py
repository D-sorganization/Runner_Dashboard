"""Web Push subscription storage and send foundation.

This module intentionally keeps the first slice small: subscriptions are
persisted in SQLite and ``send_push`` accepts an injectable transport for tests
or a later real VAPID implementation.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import webpush_crypto
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, Depends, HTTPException, Request

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]
from identity import Principal, require_principal
from push_store import (
    PUSH_TOPICS,
    PushKeys,
    PushSubscription,
    _delete_stale_subscription,
    _subscriptions_for_topic,
    delete_subscription,
    is_in_quiet_hours,
    list_subscriptions,
    update_subscription_preferences,
    upsert_subscription,
    validate_quiet_hour_time,
)
from pydantic import BaseModel, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

log = logging.getLogger(__name__)

MAX_PUSH_PAYLOAD_BYTES = 4096

ALLOWED_PUSH_ENDPOINT_HOST_SUFFIXES: tuple[str, ...] = (
    "fcm.googleapis.com",
    "android.googleapis.com",
    "push.googleapis.com",
    "push.services.mozilla.com",
    "updates.services.mozilla.com",
    "push.apple.com",
    "notify.windows.com",
)


def _require_push_csrf(request: Request) -> None:
    """Require CSRF header/token on state-changing Web Push endpoints."""
    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.headers.get("x-csrf-token"):
        return
    raise HTTPException(
        status_code=403,
        detail="CSRF check failed: missing X-Requested-With header",
    )


router = APIRouter(prefix="/api/push", tags=["push"])


def validate_push_endpoint(value: str) -> str:
    """Validate that a Web Push endpoint uses HTTPS and targets an allowlisted push service host."""
    parsed = urlsplit(value)
    if parsed.scheme != "https":
        raise ValueError("endpoint must use https")
    if parsed.username or parsed.password:
        raise ValueError("endpoint must not include credentials")
    if parsed.port not in (None, 443):
        raise ValueError("endpoint must use standard https port")
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if not hostname or not parsed.path or parsed.path == "/":
        raise ValueError("endpoint must include a valid host and subscription path")
    if hostname in {"localhost", "ip6-localhost"} or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("endpoint host must not be loopback or local")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("endpoint host must be a known push-service domain, not an IP address")

    extra_raw = os.environ.get("DASHBOARD_PUSH_ALLOWED_HOSTS", "")
    extra_hosts = tuple(h.strip().rstrip(".").lower() for h in extra_raw.split(",") if h.strip())
    allowed_suffixes = ALLOWED_PUSH_ENDPOINT_HOST_SUFFIXES + extra_hosts
    if not any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in allowed_suffixes):
        raise ValueError("endpoint host is not an allowlisted Web Push service")
    return value


def _normalize_topics(value: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for topic in value:
        if topic not in PUSH_TOPICS:
            raise ValueError(f"unsupported topic: {topic}")
        if topic not in seen:
            normalized.append(topic)
            seen.add(topic)
    return normalized


class PushSubscriptionRequest(BaseModel):
    endpoint: str = Field(..., min_length=1, max_length=2048)
    keys: PushKeys
    topics: list[str] = Field(default_factory=list, max_length=20)
    quiet_hours_start: SkipJsonSchema[str | None] = Field(default=None)
    quiet_hours_end: SkipJsonSchema[str | None] = Field(default=None)

    @field_validator("endpoint")
    @classmethod
    def _validate_endpoint(cls, value: str) -> str:
        return validate_push_endpoint(value)

    @field_validator("topics")
    @classmethod
    def _validate_topics(cls, value: list[str]) -> list[str]:
        return _normalize_topics(value)

    @field_validator("quiet_hours_start", "quiet_hours_end")
    @classmethod
    def _validate_quiet_hours(cls, value: str | None) -> str | None:
        return validate_quiet_hour_time(value)


class PushPreferencesUpdateRequest(BaseModel):
    topics: list[str] | None = Field(default=None, max_length=20)
    quiet_hours_start: str | None = Field(default=None)
    quiet_hours_end: str | None = Field(default=None)

    @field_validator("topics")
    @classmethod
    def _validate_topics(cls, value: list[str] | None) -> list[str] | None:
        return _normalize_topics(value) if value is not None else None

    @field_validator("quiet_hours_start", "quiet_hours_end")
    @classmethod
    def _validate_quiet_hours(cls, value: str | None) -> str | None:
        return validate_quiet_hour_time(value)


class PushTestRequest(BaseModel):
    topic: str = Field(default="agent.completed")
    deep_link: str = Field(default="/m/remediation")

    @field_validator("topic")
    @classmethod
    def _validate_topic(cls, value: str) -> str:
        if value not in PUSH_TOPICS:
            raise ValueError(f"unsupported topic: {value}")
        return value

    @field_validator("deep_link")
    @classmethod
    def _validate_deep_link(cls, value: str) -> str:
        valid_prefixes = ("/m/", "/staff", "/settings", "/t/")
        if not any(value.startswith(p) for p in valid_prefixes):
            raise ValueError("deep_link must be an internal route")
        return value


class PushTransport(Protocol):
    async def send(self, subscription: PushSubscription, payload: dict[str, Any]) -> int:
        """Send one push payload and return the HTTP-like status code."""


class UnconfiguredPushTransport:
    async def send(self, _subscription: PushSubscription, _payload: dict[str, Any]) -> int:
        raise RuntimeError("Web Push transport is not configured")


class WebPushTransport:
    """Production Web Push transport delivering encrypted payloads via RFC 8291/8292."""

    def __init__(
        self,
        *,
        public_key: ec.EllipticCurvePublicKey,
        private_key: ec.EllipticCurvePrivateKey,
        subject: str,
        client: httpx.AsyncClient | None = None,
        ttl: int = 86400,
        urgency: str = "normal",
    ) -> None:
        self.public_key = public_key
        self.private_key = private_key
        self.subject = subject
        self._client = client
        self.ttl = ttl
        self.urgency = urgency

    async def send(self, subscription: PushSubscription, payload: dict[str, Any]) -> int:
        validate_push_endpoint(subscription.endpoint)
        json_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        body_bytes, enc_headers = webpush_crypto.encrypt_webpush_payload(
            plaintext=json_bytes,
            p256dh=subscription.keys["p256dh"],
            auth=subscription.keys["auth"],
        )
        vapid_headers = webpush_crypto.create_vapid_auth_header(
            endpoint=subscription.endpoint,
            subject=self.subject,
            private_key=self.private_key,
            public_key=self.public_key,
            ttl=self.ttl,
            urgency=self.urgency,
        )
        headers = {
            "Content-Type": "application/octet-stream",
            **enc_headers,
            **vapid_headers,
        }
        if self._client is not None:
            resp = await self._client.post(
                subscription.endpoint,
                content=body_bytes,
                headers=headers,
                timeout=10.0,
            )
            return resp.status_code

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                subscription.endpoint,
                content=body_bytes,
                headers=headers,
            )
            return resp.status_code


_transport: PushTransport = UnconfiguredPushTransport()


def set_push_transport(transport: PushTransport) -> None:
    """Set the process-wide push transport used by routes and tests."""
    global _transport
    _transport = transport


def init_push_transport() -> None:
    """Initialize the process-wide push transport from environment variables.

    Requires VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, and VAPID_SUBJECT.
    Logs one line either way, never logging key material.
    """
    pub_b64 = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
    priv_raw = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    subject = os.environ.get("VAPID_SUBJECT", "").strip()

    if not (pub_b64 and priv_raw and subject):
        set_push_transport(UnconfiguredPushTransport())
        log.info("Web Push transport unconfigured (missing VAPID env vars)")
        return

    try:
        pub_key = webpush_crypto.load_vapid_public_key(pub_b64)
        priv_key = webpush_crypto.load_vapid_private_key(priv_raw)
        set_push_transport(
            WebPushTransport(
                public_key=pub_key,
                private_key=priv_key,
                subject=subject,
            )
        )
        log.info("Web Push transport initialized with VAPID")
    except Exception as exc:  # noqa: BLE001
        set_push_transport(UnconfiguredPushTransport())
        log.warning("Failed to initialize Web Push transport: %s", exc)


def generate_keygen_env_lines() -> list[str]:
    """Generate VAPID keypair formatted as environment variable lines."""
    priv_b64, pub_b64 = webpush_crypto.generate_vapid_keypair()
    return [
        f"VAPID_PUBLIC_KEY={pub_b64}",
        f"VAPID_PRIVATE_KEY={priv_b64}",
        "VAPID_SUBJECT=mailto:admin@example.com",
    ]


def _validate_push_payload(payload: dict[str, Any]) -> None:
    size = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    if size > MAX_PUSH_PAYLOAD_BYTES:
        raise ValueError("push payload exceeds 4 KB")
    forbidden = {"token", "secret", "password", "api_key", "authorization"}
    if any(key.lower() in forbidden for key in payload):
        raise ValueError("push payload must not contain secrets")


async def send_push(
    topic: str,
    payload: dict[str, Any],
    *,
    user_id: str | None = None,
    transport: PushTransport | None = None,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    if topic not in PUSH_TOPICS:
        raise ValueError(f"unsupported topic: {topic}")
    _validate_push_payload(payload)
    sender = transport or _transport
    sent = 0
    failed = 0
    purged = 0
    for subscription in _subscriptions_for_topic(topic, user_id, db_path):
        if is_in_quiet_hours(subscription.quiet_hours_start, subscription.quiet_hours_end, now=now):
            continue
        try:
            status_code = await sender.send(subscription, payload)
        except Exception as e:
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            failed += 1
            continue
        if status_code in (404, 410):
            _delete_stale_subscription(subscription.id, db_path)
            purged += 1
        elif 200 <= status_code < 300:
            sent += 1
        else:
            failed += 1
    return {"sent": sent, "failed": failed, "purged": purged}


_pending_notifications: set[asyncio.Task[dict[str, int]]] = set()


def _log_notify_failure(task: asyncio.Task[dict[str, int]]) -> None:
    _pending_notifications.discard(task)
    if not task.cancelled() and task.exception() is not None:
        log.warning("push notify failed: %s", task.exception())


def notify(topic: str, title: str, body: str) -> None:
    """Send a push from synchronous code, never raising into the caller.

    Inside a running loop the send is scheduled and its failure logged; with no
    loop it runs to completion here. ``send_push`` is looked up at call time so
    tests can replace it.
    """
    assert topic in PUSH_TOPICS, f"unsupported topic: {topic}"
    payload = {"title": title, "body": body}
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(send_push(topic, payload))
        except Exception as exc:  # noqa: BLE001
            log.warning("push notify failed: %s", exc)
        return
    task = loop.create_task(send_push(topic, payload))
    _pending_notifications.add(task)
    task.add_done_callback(_log_notify_failure)


def _serialize_subscription(sub: PushSubscription) -> dict[str, Any]:
    return {
        "id": sub.id,
        "user_id": sub.user_id,
        "endpoint": sub.endpoint,
        "topics": list(sub.topics),
        "quiet_hours_start": sub.quiet_hours_start,
        "quiet_hours_end": sub.quiet_hours_end,
    }


@router.get("/subscriptions", include_in_schema=False)
async def get_push_subscriptions(
    principal: Principal = Depends(require_principal),  # noqa: B008
) -> dict[str, Any]:
    return {"subscriptions": [_serialize_subscription(sub) for sub in list_subscriptions(principal.id)]}


@router.post("/subscribe")
async def subscribe_push(
    request: Request,
    body: PushSubscriptionRequest,
    principal: Principal = Depends(require_principal),  # noqa: B008
) -> dict[str, Any]:
    _require_push_csrf(request)
    subscription = upsert_subscription(
        user_id=principal.id,
        endpoint=body.endpoint,
        keys=body.keys,
        user_agent=request.headers.get("user-agent", ""),
        topics=body.topics,
        quiet_hours_start=body.quiet_hours_start,
        quiet_hours_end=body.quiet_hours_end,
    )
    return _serialize_subscription(subscription)


@router.patch("/subscribe/{subscription_id}", include_in_schema=False)
async def update_push_subscription(
    request: Request,
    subscription_id: int,
    body: PushPreferencesUpdateRequest,
    principal: Principal = Depends(require_principal),  # noqa: B008
) -> dict[str, Any]:
    _require_push_csrf(request)
    fields_set = body.model_fields_set
    updated = update_subscription_preferences(
        subscription_id,
        principal.id,
        topics=body.topics,
        quiet_hours_start=body.quiet_hours_start,
        quiet_hours_end=body.quiet_hours_end,
        update_quiet_hours=("quiet_hours_start" in fields_set or "quiet_hours_end" in fields_set),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="subscription not found")
    return _serialize_subscription(updated)


@router.delete("/subscribe/{subscription_id}")
async def unsubscribe_push(
    request: Request,
    subscription_id: int,
    principal: Principal = Depends(require_principal),  # noqa: B008
) -> dict[str, Any]:
    _require_push_csrf(request)
    deleted = delete_subscription(
        subscription_id,
        principal.id,
        admin="admin" in principal.roles,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="subscription not found")
    return {"deleted": True, "id": subscription_id}


@router.post("/test")
async def test_push(
    request: Request,
    body: PushTestRequest,
    principal: Principal = Depends(require_principal),  # noqa: B008
) -> dict[str, Any]:
    _require_push_csrf(request)
    payload = {
        "topic": body.topic,
        "title": "Runner Dashboard test notification",
        "body": "Push transport and subscription routing are configured.",
        "url": body.deep_link,
        "deep_link": body.deep_link,
    }
    try:
        result = await send_push(body.topic, payload, user_id=principal.id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"topic": body.topic, **result}


@router.get("/vapid-public-key")
async def get_vapid_public_key() -> dict[str, str]:
    """Return the VAPID public key for Web Push subscription.

    When ``VAPID_PUBLIC_KEY`` is unset, return a 503 so the frontend can
    surface a clear "not configured" message instead of subscribing to a
    transport that will silently fail on send.
    """
    public_key = os.environ.get("VAPID_PUBLIC_KEY", "")
    if not public_key:
        raise HTTPException(status_code=503, detail="Push not configured")
    return {"publicKey": public_key}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "keygen":
        for env_line in generate_keygen_env_lines():
            sys.stdout.write(f"{env_line}\n")
        sys.exit(0)
    else:
        sys.stderr.write("Usage: python -m push keygen\n")
        sys.exit(1)
