from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import push  # noqa: E402
from identity import Principal  # noqa: E402


@pytest_asyncio.fixture
async def client():
    from httpx import ASGITransport, AsyncClient  # noqa: PLC0415
    from server import app  # noqa: PLC0415

    headers = {
        "Authorization": "Bearer test-key",
        "X-Requested-With": "XMLHttpRequest",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers=headers,
    ) as ac:
        yield ac


class RecordingTransport:
    def __init__(self, status_code: int = 201) -> None:
        self.status_code = status_code
        self.sent: list[tuple[push.PushSubscription, dict]] = []

    async def send(self, subscription: push.PushSubscription, payload: dict) -> int:
        self.sent.append((subscription, payload))
        return self.status_code


@pytest.fixture
def push_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "push_subscriptions.sqlite3"
    monkeypatch.setenv("RUNNER_DASHBOARD_PUSH_DB", str(path))
    return path


def _request(endpoint: str = "https://updates.push.services.example/sub/1") -> push.PushSubscriptionRequest:
    return push.PushSubscriptionRequest(
        endpoint=endpoint,
        keys=push.PushKeys(p256dh="client-public-key", auth="client-auth-secret"),
        topics=["agent.completed", "ci.failed"],
    )


def test_subscribe_upserts_subscription(push_db: Path) -> None:
    first = push.upsert_subscription(
        user_id="alice",
        endpoint=_request().endpoint,
        keys=_request().keys,
        user_agent="pytest",
        topics=["agent.completed"],
        db_path=push_db,
    )
    second = push.upsert_subscription(
        user_id="alice",
        endpoint=_request().endpoint,
        keys=_request().keys,
        user_agent="pytest/updated",
        topics=["ci.failed"],
        db_path=push_db,
    )

    assert second.id == first.id
    assert second.topics == ("ci.failed",)
    assert second.user_agent == "pytest/updated"


@pytest.mark.asyncio
async def test_send_push_filters_by_topic_and_payload(push_db: Path) -> None:
    push.upsert_subscription(
        user_id="alice",
        endpoint="https://updates.push.services.example/sub/1",
        keys=_request().keys,
        user_agent="pytest",
        topics=["agent.completed"],
        db_path=push_db,
    )
    push.upsert_subscription(
        user_id="alice",
        endpoint="https://updates.push.services.example/sub/2",
        keys=_request().keys,
        user_agent="pytest",
        topics=["ci.failed"],
        db_path=push_db,
    )
    transport = RecordingTransport()

    result = await push.send_push(
        "agent.completed",
        {"deep_link": "/m/remediation?run=123", "run_id": "123"},
        user_id="alice",
        transport=transport,
        db_path=push_db,
    )

    assert result == {"sent": 1, "failed": 0, "purged": 0}
    assert len(transport.sent) == 1
    assert transport.sent[0][0].endpoint.endswith("/sub/1")


@pytest.mark.asyncio
async def test_send_push_purges_404_or_410_subscriptions(push_db: Path) -> None:
    subscription = push.upsert_subscription(
        user_id="alice",
        endpoint="https://updates.push.services.example/stale",
        keys=_request().keys,
        user_agent="pytest",
        topics=["agent.completed"],
        db_path=push_db,
    )

    result = await push.send_push(
        "agent.completed",
        {"deep_link": "/m/remediation?run=stale"},
        user_id="alice",
        transport=RecordingTransport(status_code=410),
        db_path=push_db,
    )

    assert result == {"sent": 0, "failed": 0, "purged": 1}
    assert not push.delete_subscription(subscription.id, "alice", db_path=push_db)


def test_unsubscribe_is_scoped_to_owner(push_db: Path) -> None:
    subscription = push.upsert_subscription(
        user_id="alice",
        endpoint="https://updates.push.services.example/sub/1",
        keys=_request().keys,
        user_agent="pytest",
        topics=["agent.completed"],
        db_path=push_db,
    )

    assert not push.delete_subscription(subscription.id, "bob", db_path=push_db)
    assert push.delete_subscription(subscription.id, "bob", admin=True, db_path=push_db)


def test_push_router_has_expected_routes() -> None:
    paths = {route.path for route in push.router.routes}  # type: ignore[attr-defined]
    assert "/api/push/subscribe" in paths
    assert "/api/push/subscribe/{subscription_id}" in paths
    assert "/api/push/test" in paths


@pytest.mark.asyncio
async def test_push_routes_subscribe_and_unsubscribe(client, push_db: Path) -> None:
    resp = await client.post(
        "/api/push/subscribe",
        json=_request().model_dump(),
        headers={"user-agent": "pytest"},
    )

    assert resp.status_code == 200
    subscription_id = resp.json()["id"]
    assert resp.json()["user_id"] == "test-admin"

    deleted = await client.delete(f"/api/push/subscribe/{subscription_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": subscription_id}


def test_payload_rejects_secret_keys() -> None:
    with pytest.raises(ValueError, match="must not contain secrets"):
        push._validate_push_payload({"token": "do-not-send"})  # noqa: SLF001


def test_payload_rejects_oversized_body() -> None:
    with pytest.raises(ValueError, match="exceeds 4 KB"):
        push._validate_push_payload({"body": "x" * 4097})  # noqa: SLF001


def test_principal_import_keeps_auth_dependency_available() -> None:
    assert Principal(id="test", type="bot", name="Test").id == "test"


@pytest.mark.asyncio
async def test_vapid_public_key_returns_503_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException  # noqa: PLC0415

    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    with pytest.raises(HTTPException) as excinfo:
        await push.get_vapid_public_key()
    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == "Push not configured"


@pytest.mark.asyncio
async def test_vapid_public_key_returns_key_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "VAPID_PUBLIC_KEY",
        "BMxJh-T8x6YlT3q5KqoZqoZqoZqoZqoZqoZqoZqoZqoZqoZqoZqoZqoZqoZqoZqoZqoZqoZqoZqoZqoZqoZqoZ",
    )
    resp = await push.get_vapid_public_key()
    assert resp["publicKey"].startswith("BMxJh")


@pytest.mark.asyncio
async def test_vapid_public_key_route_returns_503_when_unset(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    resp = await client.get("/api/push/vapid-public-key")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Push not configured"


@pytest.mark.asyncio
async def test_vapid_public_key_route_returns_200_when_configured(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "BTestKeyValue123")
    resp = await client.get("/api/push/vapid-public-key")
    assert resp.status_code == 200
    assert resp.json() == {"publicKey": "BTestKeyValue123"}
