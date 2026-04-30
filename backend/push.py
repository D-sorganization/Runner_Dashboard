"""Web Push subscription storage and send foundation.

This module intentionally keeps the first slice small: subscriptions are
persisted in SQLite and ``send_push`` accepts an injectable transport for tests
or a later real VAPID implementation.
"""

from __future__ import annotations

import datetime as _dt_mod
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, require_principal, require_scope
from pydantic import BaseModel, Field, field_validator

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

PUSH_TOPICS = frozenset(
    {
        "agent.completed",
        "agent.failed",
        "ci.failed",
        "runner.offline",
        "queue.stale",
    }
)
MAX_PUSH_PAYLOAD_BYTES = 4096

DEFAULT_DB_PATH = Path(
    os.environ.get(
        "RUNNER_DASHBOARD_PUSH_DB",
        str(Path(__file__).resolve().parents[1] / "config" / "push_subscriptions.sqlite3"),
    )
)

router = APIRouter(prefix="/api/push", tags=["push"])


class PushKeys(BaseModel):
    p256dh: str = Field(..., min_length=1, max_length=512)
    auth: str = Field(..., min_length=1, max_length=256)


class PushSubscriptionRequest(BaseModel):
    endpoint: str = Field(..., min_length=1, max_length=2048)
    keys: PushKeys
    topics: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("endpoint")
    @classmethod
    def _validate_endpoint(cls, value: str) -> str:
        if not value.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise ValueError("endpoint must be https or loopback http")
        return value

    @field_validator("topics")
    @classmethod
    def _validate_topics(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for topic in value:
            if topic not in PUSH_TOPICS:
                raise ValueError(f"unsupported topic: {topic}")
            if topic not in seen:
                normalized.append(topic)
                seen.add(topic)
        return normalized


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
        if not value.startswith("/m/"):
            raise ValueError("deep_link must be an internal /m/ route")
        return value


@dataclass(frozen=True)
class PushSubscription:
    id: int
    user_id: str
    endpoint: str
    keys: dict[str, str]
    user_agent: str
    topics: tuple[str, ...]


class PushTransport(Protocol):
    async def send(self, subscription: PushSubscription, payload: dict[str, Any]) -> int:
        """Send one push payload and return the HTTP-like status code."""


class UnconfiguredPushTransport:
    async def send(self, _subscription: PushSubscription, _payload: dict[str, Any]) -> int:
        raise RuntimeError("Web Push transport is not configured")


_transport: PushTransport = UnconfiguredPushTransport()


def set_push_transport(transport: PushTransport) -> None:
    """Set the process-wide push transport used by routes and tests."""
    global _transport
    _transport = transport


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _db_path() -> Path:
    return Path(os.environ.get("RUNNER_DASHBOARD_PUSH_DB", str(DEFAULT_DB_PATH)))


def _connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            user_agent TEXT NOT NULL DEFAULT '',
            topics_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    return conn


def _row_to_subscription(row: sqlite3.Row) -> PushSubscription:
    topics_payload = json.loads(row["topics_json"])
    topics = tuple(topic for topic in topics_payload if topic in PUSH_TOPICS)
    return PushSubscription(
        id=int(row["id"]),
        user_id=str(row["user_id"]),
        endpoint=str(row["endpoint"]),
        keys={"p256dh": str(row["p256dh"]), "auth": str(row["auth"])},
        user_agent=str(row["user_agent"]),
        topics=topics,
    )


def upsert_subscription(
    *,
    user_id: str,
    endpoint: str,
    keys: PushKeys,
    user_agent: str,
    topics: list[str],
    db_path: Path | None = None,
) -> PushSubscription:
    now = _utc_now()
    topics_json = json.dumps(topics, separators=(",", ":"))
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO push_subscriptions
                (user_id, endpoint, p256dh, auth, user_agent, topics_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                user_id = excluded.user_id,
                p256dh = excluded.p256dh,
                auth = excluded.auth,
                user_agent = excluded.user_agent,
                topics_json = excluded.topics_json,
                updated_at = excluded.updated_at
            """,
            (user_id, endpoint, keys.p256dh, keys.auth, user_agent[:512], topics_json, now, now),
        )
        row = conn.execute("SELECT * FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).fetchone()
    if row is None:
        raise RuntimeError("subscription upsert did not return a row")
    return _row_to_subscription(row)


def delete_subscription(
    subscription_id: int,
    user_id: str,
    *,
    admin: bool = False,
    db_path: Path | None = None,
) -> bool:
    with _connect(db_path) as conn:
        if admin:
            cursor = conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (subscription_id,))
        else:
            cursor = conn.execute(
                "DELETE FROM push_subscriptions WHERE id = ? AND user_id = ?",
                (subscription_id, user_id),
            )
        return cursor.rowcount > 0


def _subscriptions_for_topic(
    topic: str,
    user_id: str | None = None,
    db_path: Path | None = None,
) -> list[PushSubscription]:
    params: list[Any] = []
    query = "SELECT * FROM push_subscriptions"
    if user_id:
        query += " WHERE user_id = ?"
        params.append(user_id)
    with _connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    subscriptions = [_row_to_subscription(row) for row in rows]
    return [subscription for subscription in subscriptions if topic in subscription.topics]


def _delete_stale_subscription(subscription_id: int, db_path: Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (subscription_id,))


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
) -> dict[str, int]:
    if topic not in PUSH_TOPICS:
        raise ValueError(f"unsupported topic: {topic}")
    _validate_push_payload(payload)
    sender = transport or _transport
    sent = 0
    failed = 0
    purged = 0
    for subscription in _subscriptions_for_topic(topic, user_id, db_path):
        try:
            status_code = await sender.send(subscription, payload)
        except Exception:
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


@router.post("/subscribe")
async def subscribe_push(
    request: Request,
    body: PushSubscriptionRequest,
    principal: Principal = Depends(require_principal),  # noqa: B008
) -> dict[str, Any]:
    subscription = upsert_subscription(
        user_id=principal.id,
        endpoint=body.endpoint,
        keys=body.keys,
        user_agent=request.headers.get("user-agent", ""),
        topics=body.topics,
    )
    return {
        "id": subscription.id,
        "user_id": subscription.user_id,
        "topics": list(subscription.topics),
    }


@router.delete("/subscribe/{subscription_id}")
async def unsubscribe_push(
    subscription_id: int,
    principal: Principal = Depends(require_principal),  # noqa: B008
) -> dict[str, Any]:
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
    body: PushTestRequest,
    principal: Principal = Depends(require_scope("admin")),  # noqa: B008
) -> dict[str, Any]:
    payload = {
        "topic": body.topic,
        "title": "Runner Dashboard test notification",
        "body": "Push transport and subscription routing are configured.",
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
