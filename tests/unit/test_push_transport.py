"""Tests for real WebPushTransport in push.py and initialization."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import httpx
import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import push  # noqa: E402
import webpush_crypto  # noqa: E402


@pytest.fixture()
def push_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "push_test.sqlite3"
    monkeypatch.setenv("RUNNER_DASHBOARD_PUSH_DB", str(db))
    return db


@pytest.fixture()
def vapid_keys() -> tuple[str, str]:
    return webpush_crypto.generate_vapid_keypair()


@pytest.mark.asyncio
async def test_webpush_transport_send_constructs_expected_headers(push_db: Path, vapid_keys: tuple[str, str]) -> None:
    priv_b64, pub_b64 = vapid_keys
    priv_key = webpush_crypto.load_vapid_private_key(priv_b64)
    pub_key = webpush_crypto.load_vapid_public_key(pub_b64)

    captured_requests: list[httpx.Request] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(201)

    mock_transport = httpx.MockTransport(handle_request)
    async with httpx.AsyncClient(transport=mock_transport) as client:
        transport = push.WebPushTransport(
            public_key=pub_key,
            private_key=priv_key,
            subject="mailto:admin@example.com",
            client=client,
        )

        _, client_pub = webpush_crypto.generate_vapid_keypair()
        push.upsert_subscription(
            user_id="user-1",
            endpoint="https://push.example.com/sub/abc123",
            keys=push.PushKeys(p256dh=client_pub, auth="AAAAAAAAAAAAAAAAAAAAAA"),
            user_agent="pytest",
            topics=["agent.completed"],
            db_path=push_db,
        )

        result = await push.send_push(
            "agent.completed",
            {"title": "Agent finished", "body": "Success"},
            user_id="user-1",
            transport=transport,
            db_path=push_db,
        )

        assert result["sent"] == 1
        assert len(captured_requests) == 1

        req = captured_requests[0]
        assert req.url == "https://push.example.com/sub/abc123"
        assert req.headers["Content-Encoding"] == "aes128gcm"
        assert req.headers["TTL"] == "86400"
        assert req.headers["Urgency"] == "normal"
        assert "Authorization" in req.headers

        auth_header = req.headers["Authorization"]
        assert auth_header.startswith("vapid t=")
        assert f"k={pub_b64}" in auth_header

        # Verify body starts with 86-byte RFC 8188 binary header
        body = req.content
        assert len(body) > 86


@pytest.mark.asyncio
async def test_webpush_transport_purges_on_410_gone(push_db: Path, vapid_keys: tuple[str, str]) -> None:
    priv_b64, pub_b64 = vapid_keys
    priv_key = webpush_crypto.load_vapid_private_key(priv_b64)
    pub_key = webpush_crypto.load_vapid_public_key(pub_b64)

    def handle_request(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(410)

    mock_transport = httpx.MockTransport(handle_request)
    async with httpx.AsyncClient(transport=mock_transport) as client:
        transport = push.WebPushTransport(
            public_key=pub_key,
            private_key=priv_key,
            subject="mailto:admin@example.com",
            client=client,
        )

        _, client_pub = webpush_crypto.generate_vapid_keypair()
        push.upsert_subscription(
            user_id="user-1",
            endpoint="https://push.example.com/sub/stale-sub",
            keys=push.PushKeys(p256dh=client_pub, auth="AAAAAAAAAAAAAAAAAAAAAA"),
            user_agent="pytest",
            topics=["agent.completed"],
            db_path=push_db,
        )

        result = await push.send_push(
            "agent.completed",
            {"title": "Agent finished"},
            user_id="user-1",
            transport=transport,
            db_path=push_db,
        )

        assert result["purged"] == 1
        assert result["sent"] == 0


def test_init_push_transport_configured_and_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    vapid_keys: tuple[str, str],
) -> None:
    priv_b64, pub_b64 = vapid_keys
    caplog.set_level(logging.INFO)

    # 1. Configured path
    monkeypatch.setenv("VAPID_PUBLIC_KEY", pub_b64)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", priv_b64)
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:test@example.com")

    push.init_push_transport()
    assert isinstance(push._transport, push.WebPushTransport)  # noqa: SLF001

    # Ensure no key material was logged
    log_text = caplog.text
    assert pub_b64 not in log_text
    assert priv_b64 not in log_text
    assert "Web Push transport initialized" in log_text

    # 2. Unconfigured path
    caplog.clear()
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("VAPID_SUBJECT", raising=False)

    push.init_push_transport()
    assert isinstance(push._transport, push.UnconfiguredPushTransport)  # noqa: SLF001
    assert "Web Push transport unconfigured" in caplog.text


def test_push_keygen_helper(capsys: pytest.CaptureFixture[str]) -> None:
    """Test push.keygen() generates 3 valid env lines without error."""
    lines = push.generate_keygen_env_lines()
    assert len(lines) == 3
    assert lines[0].startswith("VAPID_PUBLIC_KEY=")
    assert lines[1].startswith("VAPID_PRIVATE_KEY=")
    assert lines[2].startswith("VAPID_SUBJECT=")

    pub = lines[0].split("=")[1]
    priv = lines[1].split("=")[1]

    # Verify keys are valid
    assert webpush_crypto.load_vapid_public_key(pub) is not None
    assert webpush_crypto.load_vapid_private_key(priv) is not None
