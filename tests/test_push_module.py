"""Tests for push.py — Web Push subscription storage and send logic.

Tests cover:
- Pydantic model validation (PushSubscriptionRequest, PushTestRequest)
- _validate_push_payload enforcement
- upsert_subscription / delete_subscription with an in-memory SQLite DB
- send_push with an injectable stub transport
- _row_to_subscription topic filtering

These tests use an injectable transport stub (`_OkTransport`, `_StaleTransport`,
etc.) and never perform real HTTP. They are safe to run in PR CI and are NOT
marked `@pytest.mark.integration`.

TODO(#434): if future tests are added here that exercise the real httpx
transport against a live VAPID-signed push gateway, prefer `respx` /
`pytest-httpx` to mock the network and otherwise mark the test
`@pytest.mark.integration` so it is excluded from PR CI (see issue #401).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

import push  # noqa: E402
from push import (  # noqa: E402
    PushKeys,
    PushSubscription,
    PushSubscriptionRequest,
    PushTestRequest,
    _validate_push_payload,
    delete_subscription,
    send_push,
    upsert_subscription,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Provide a throwaway SQLite path for each test."""
    return tmp_path / "push_test.sqlite3"


def _make_keys(p256dh: str = "AAAA" * 30, auth: str = "BBBB" * 10) -> PushKeys:
    return PushKeys(p256dh=p256dh, auth=auth)


def _upsert(
    db: Path, endpoint: str = "https://push.example.com/sub/1", topics: list[str] | None = None
) -> PushSubscription:
    return upsert_subscription(
        user_id="user-1",
        endpoint=endpoint,
        keys=_make_keys(),
        user_agent="pytest/1",
        topics=topics or ["agent.completed"],
        db_path=db,
    )


# ---------------------------------------------------------------------------
# PushSubscriptionRequest — Pydantic validators
# ---------------------------------------------------------------------------


def test_push_subscription_request_valid() -> None:
    req = PushSubscriptionRequest(
        endpoint="https://push.example.com/sub/1",
        keys=PushKeys(p256dh="A" * 20, auth="B" * 10),
        topics=["agent.completed"],
    )
    assert req.endpoint.startswith("https://")
    assert req.topics == ["agent.completed"]


def test_push_subscription_request_rejects_http_non_loopback() -> None:
    with pytest.raises(ValueError):
        PushSubscriptionRequest(
            endpoint="http://evil.example.com/sub/1",
            keys=PushKeys(p256dh="A" * 20, auth="B" * 10),
            topics=[],
        )


def test_push_subscription_request_allows_localhost() -> None:
    req = PushSubscriptionRequest(
        endpoint="http://localhost:8321/push",
        keys=PushKeys(p256dh="A" * 20, auth="B" * 10),
        topics=[],
    )
    assert "localhost" in req.endpoint


def test_push_subscription_request_allows_loopback_127() -> None:
    req = PushSubscriptionRequest(
        endpoint="http://127.0.0.1:8321/push",
        keys=PushKeys(p256dh="A" * 20, auth="B" * 10),
        topics=[],
    )
    assert "127.0.0.1" in req.endpoint


def test_push_subscription_request_rejects_unknown_topic() -> None:
    with pytest.raises(ValueError):
        PushSubscriptionRequest(
            endpoint="https://push.example.com/sub/1",
            keys=PushKeys(p256dh="A" * 20, auth="B" * 10),
            topics=["unknown.topic"],
        )


def test_push_subscription_request_deduplicates_topics() -> None:
    req = PushSubscriptionRequest(
        endpoint="https://push.example.com/sub/1",
        keys=PushKeys(p256dh="A" * 20, auth="B" * 10),
        topics=["agent.completed", "agent.completed", "ci.failed"],
    )
    assert req.topics.count("agent.completed") == 1


def test_push_subscription_request_empty_topics_allowed() -> None:
    req = PushSubscriptionRequest(
        endpoint="https://push.example.com/sub/1",
        keys=PushKeys(p256dh="A" * 20, auth="B" * 10),
        topics=[],
    )
    assert req.topics == []


# ---------------------------------------------------------------------------
# PushTestRequest — Pydantic validators
# ---------------------------------------------------------------------------


def test_push_test_request_defaults() -> None:
    req = PushTestRequest()
    assert req.topic == "agent.completed"
    assert req.deep_link.startswith("/m/")


def test_push_test_request_valid_custom_topic() -> None:
    req = PushTestRequest(topic="runner.offline", deep_link="/m/fleet")
    assert req.topic == "runner.offline"


def test_push_test_request_rejects_unknown_topic() -> None:
    with pytest.raises(ValueError):
        PushTestRequest(topic="not.a.real.topic")


def test_push_test_request_rejects_non_internal_deep_link() -> None:
    with pytest.raises(ValueError):
        PushTestRequest(deep_link="https://evil.com/page")


def test_push_test_request_all_known_topics_accepted() -> None:
    for topic in push.PUSH_TOPICS:
        req = PushTestRequest(topic=topic)
        assert req.topic == topic


# ---------------------------------------------------------------------------
# _validate_push_payload
# ---------------------------------------------------------------------------


def test_validate_push_payload_valid() -> None:
    _validate_push_payload({"title": "Test", "body": "Hello"})


def test_validate_push_payload_rejects_oversized() -> None:
    with pytest.raises(ValueError, match="4 KB"):
        _validate_push_payload({"data": "x" * 5000})


def test_validate_push_payload_rejects_secret_key() -> None:
    with pytest.raises(ValueError, match="secrets"):
        _validate_push_payload({"token": "abc123", "title": "Test"})


def test_validate_push_payload_rejects_authorization_key() -> None:
    with pytest.raises(ValueError, match="secrets"):
        _validate_push_payload({"Authorization": "Bearer token", "title": "Test"})


def test_validate_push_payload_rejects_password_key() -> None:
    with pytest.raises(ValueError, match="secrets"):
        _validate_push_payload({"password": "s3cr3t"})


# ---------------------------------------------------------------------------
# upsert_subscription / delete_subscription
# ---------------------------------------------------------------------------


def test_upsert_subscription_creates_entry(db_path: Path) -> None:
    sub = _upsert(db_path)
    assert sub.id > 0
    assert sub.user_id == "user-1"
    assert sub.endpoint == "https://push.example.com/sub/1"
    assert "agent.completed" in sub.topics


def test_upsert_subscription_updates_on_duplicate_endpoint(db_path: Path) -> None:
    sub1 = _upsert(db_path, topics=["agent.completed"])
    sub2 = upsert_subscription(
        user_id="user-2",
        endpoint="https://push.example.com/sub/1",  # same endpoint
        keys=_make_keys(),
        user_agent="pytest/2",
        topics=["ci.failed"],
        db_path=db_path,
    )
    assert sub1.id == sub2.id  # same row updated
    assert sub2.user_id == "user-2"
    assert "ci.failed" in sub2.topics


def test_upsert_subscription_multiple_topics(db_path: Path) -> None:
    sub = _upsert(db_path, topics=["agent.completed", "runner.offline"])
    assert "agent.completed" in sub.topics
    assert "runner.offline" in sub.topics


def test_delete_subscription_owned_row(db_path: Path) -> None:
    sub = _upsert(db_path)
    deleted = delete_subscription(sub.id, "user-1", db_path=db_path)
    assert deleted is True


def test_delete_subscription_wrong_user_denied(db_path: Path) -> None:
    sub = _upsert(db_path)
    deleted = delete_subscription(sub.id, "other-user", db_path=db_path)
    assert deleted is False


def test_delete_subscription_admin_can_delete_any_row(db_path: Path) -> None:
    sub = _upsert(db_path)
    deleted = delete_subscription(sub.id, "admin-user", admin=True, db_path=db_path)
    assert deleted is True


def test_delete_subscription_nonexistent_row(db_path: Path) -> None:
    deleted = delete_subscription(9999, "user-1", db_path=db_path)
    assert deleted is False


# ---------------------------------------------------------------------------
# send_push — injectable transport stub
# ---------------------------------------------------------------------------


class _OkTransport:
    async def send(self, subscription: PushSubscription, payload: dict[str, Any]) -> int:
        return 200


class _StaleTransport:
    """Returns 410 Gone — subscription should be purged."""

    async def send(self, subscription: PushSubscription, payload: dict[str, Any]) -> int:
        return 410


class _ErrorTransport:
    """Returns 500 Internal Server Error."""

    async def send(self, subscription: PushSubscription, payload: dict[str, Any]) -> int:
        return 500


class _RaisingTransport:
    """Raises an exception instead of returning a status code."""

    async def send(self, subscription: PushSubscription, payload: dict[str, Any]) -> int:
        raise RuntimeError("network failure")


def _run(coro):
    return asyncio.run(coro)


def test_send_push_sent_to_subscriber(db_path: Path) -> None:
    _upsert(db_path, topics=["agent.completed"])
    result = _run(
        send_push(
            "agent.completed",
            {"title": "Done"},
            transport=_OkTransport(),
            db_path=db_path,
        )
    )
    assert result["sent"] == 1
    assert result["failed"] == 0
    assert result["purged"] == 0


def test_send_push_stale_subscription_purged(db_path: Path) -> None:
    _upsert(db_path, topics=["agent.completed"])
    result = _run(
        send_push(
            "agent.completed",
            {"title": "Done"},
            transport=_StaleTransport(),
            db_path=db_path,
        )
    )
    assert result["purged"] == 1
    assert result["sent"] == 0


def test_send_push_error_status_counted_as_failed(db_path: Path) -> None:
    _upsert(db_path, topics=["agent.completed"])
    result = _run(
        send_push(
            "agent.completed",
            {"title": "Done"},
            transport=_ErrorTransport(),
            db_path=db_path,
        )
    )
    assert result["failed"] == 1


def test_send_push_exception_counted_as_failed(db_path: Path) -> None:
    _upsert(db_path, topics=["agent.completed"])
    result = _run(
        send_push(
            "agent.completed",
            {"title": "Done"},
            transport=_RaisingTransport(),
            db_path=db_path,
        )
    )
    assert result["failed"] == 1


def test_send_push_unknown_topic_raises(db_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported topic"):
        _run(send_push("not.a.topic", {"title": "Bad"}, transport=_OkTransport(), db_path=db_path))


def test_send_push_oversized_payload_raises(db_path: Path) -> None:
    with pytest.raises(ValueError, match="4 KB"):
        _run(send_push("agent.completed", {"data": "x" * 5000}, transport=_OkTransport(), db_path=db_path))


def test_send_push_no_matching_subscribers_returns_zeros(db_path: Path) -> None:
    # Subscribe only to ci.failed, then push to runner.offline -> no match
    _upsert(db_path, topics=["ci.failed"])
    result = _run(
        send_push(
            "runner.offline",
            {"title": "Node down"},
            transport=_OkTransport(),
            db_path=db_path,
        )
    )
    assert result == {"sent": 0, "failed": 0, "purged": 0}


def test_send_push_filtered_by_user_id(db_path: Path) -> None:
    # Two subscribers; only one should receive
    _upsert(db_path, endpoint="https://push.example.com/a", topics=["agent.completed"])
    upsert_subscription(
        user_id="user-2",
        endpoint="https://push.example.com/b",
        keys=_make_keys(),
        user_agent="pytest",
        topics=["agent.completed"],
        db_path=db_path,
    )
    result = _run(
        send_push(
            "agent.completed",
            {"title": "Done"},
            user_id="user-1",
            transport=_OkTransport(),
            db_path=db_path,
        )
    )
    assert result["sent"] == 1  # only user-1's subscription
