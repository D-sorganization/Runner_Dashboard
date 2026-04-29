"""Tests for the Linear webhook receiver with dispatch conversion (issue #243)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

import server  # noqa: E402
from routers import linear_webhook as webhook_router  # noqa: E402

API_AUTH = {"Authorization": "Bearer test-key"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(server.app)


@pytest.fixture(autouse=True)
def _clear_replay_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure each test starts with a clean replay buffer."""
    webhook_router._processed_webhook_ids.clear()
    # Ensure no secret is set by default
    monkeypatch.delenv("LINEAR_WEBHOOK_SECRET", raising=False)


# ─── Payload helpers ───────────────────────────────────────────────────────────


def _make_payload(
    *,
    action: str = "create",
    type_: str = "Issue",
    data: dict | None = None,
    created_at: int | str | None = None,
    webhook_id: str | None = "webhook-123",
    organization_id: str | None = "org-456",
) -> dict:
    """Build a minimal Linear webhook payload."""
    if created_at is None:
        created_at = int(time.time() * 1000)
    return {
        "action": action,
        "type": type_,
        "data": data or {},
        "createdAt": created_at,
        "webhookId": webhook_id,
        "organizationId": organization_id,
    }


def _sign_body(body: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 hex digest for the Linear-Signature header."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


# ─── Validation tests ──────────────────────────────────────────────────────────


def test_webhook_returns_ok_for_valid_payload(client: TestClient) -> None:
    payload = _make_payload()
    response = client.post(
        "/api/linear/webhook",
        json=payload,
        headers=API_AUTH,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["action"] == "create"
    assert data["type"] == "issue"


def test_webhook_rejects_invalid_json(client: TestClient) -> None:
    response = client.post(
        "/api/linear/webhook",
        content=b"not json",
        headers={**API_AUTH, "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid JSON body"


def test_webhook_rejects_unsupported_action(client: TestClient) -> None:
    payload = _make_payload(action="destroy")
    response = client.post("/api/linear/webhook", json=payload, headers=API_AUTH)
    assert response.status_code == 422


def test_webhook_accepts_unknown_type_with_warning(client: TestClient) -> None:
    payload = _make_payload(type_="UnknownType")
    response = client.post("/api/linear/webhook", json=payload, headers=API_AUTH)
    assert response.status_code == 200
    assert response.json()["type"] == "unknowntype"


# ─── Signature verification tests ─────────────────────────────────────────────


def test_webhook_verifies_signature_with_secret(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "super-secret"
    monkeypatch.setenv("LINEAR_WEBHOOK_SECRET", secret)

    payload = _make_payload()
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig = _sign_body(body, secret)

    response = client.post(
        "/api/linear/webhook",
        content=body,
        headers={"Linear-Signature": sig, "Content-Type": "application/json", **API_AUTH},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_webhook_rejects_bad_signature(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "super-secret"
    monkeypatch.setenv("LINEAR_WEBHOOK_SECRET", secret)

    payload = _make_payload()
    response = client.post(
        "/api/linear/webhook",
        json=payload,
        headers={"Linear-Signature": "bad-signature", **API_AUTH},
    )
    assert response.status_code == 401


def test_webhook_allows_dev_mode_when_no_secret_or_header(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINEAR_WEBHOOK_SECRET", raising=False)
    payload = _make_payload()
    response = client.post("/api/linear/webhook", json=payload, headers=API_AUTH)
    assert response.status_code == 200


# ─── Replay protection tests ──────────────────────────────────────────────────


def test_webhook_detects_replay(client: TestClient) -> None:
    payload = _make_payload(webhook_id="replay-me")
    response1 = client.post("/api/linear/webhook", json=payload, headers=API_AUTH)
    assert response1.status_code == 200
    assert response1.json().get("replay") is None

    response2 = client.post("/api/linear/webhook", json=payload, headers=API_AUTH)
    assert response2.status_code == 200
    assert response2.json()["replay"] is True


def test_webhook_without_webhook_id_allows_replay(client: TestClient) -> None:
    payload = _make_payload(webhook_id=None)
    response1 = client.post("/api/linear/webhook", json=payload, headers=API_AUTH)
    assert response1.status_code == 200

    response2 = client.post("/api/linear/webhook", json=payload, headers=API_AUTH)
    assert response2.status_code == 200


# ─── Age check tests ───────────────────────────────────────────────────────────


def test_webhook_rejects_old_payload(client: TestClient) -> None:
    old_time = int((time.time() - 400) * 1000)
    payload = _make_payload(created_at=old_time)
    response = client.post("/api/linear/webhook", json=payload, headers=API_AUTH)
    assert response.status_code == 400
    assert response.json()["detail"] == "Payload too old"


def test_webhook_accepts_recent_payload(client: TestClient) -> None:
    recent_time = int((time.time() - 10) * 1000)
    payload = _make_payload(created_at=recent_time)
    response = client.post("/api/linear/webhook", json=payload, headers=API_AUTH)
    assert response.status_code == 200


# ─── Dispatch conversion tests ─────────────────────────────────────────────────


def test_webhook_issue_event_returns_envelope(client: TestClient) -> None:
    """Issue events should produce a dispatch envelope."""
    payload = _make_payload(
        type_="Issue",
        data={
            "id": "issue-uuid-1",
            "identifier": "TEAM-42",
            "title": "Fix flaky test",
            "url": "https://linear.app/issue/TEAM-42",
            "team": {"name": "Platform"},
        },
    )
    response = client.post("/api/linear/webhook", json=payload, headers=API_AUTH)
    assert response.status_code == 200
    data = response.json()

    assert data["ok"] is True
    assert "envelope" in data

    envelope = data["envelope"]
    assert envelope["action"] == "agents.dispatch.issue"
    assert envelope["source"] == "linear_webhook"
    assert envelope["target"] == "fleet"
    assert envelope["requested_by"] == "linear_webhook"

    assert envelope["payload"]["issue_id"] == "issue-uuid-1"
    assert envelope["payload"]["title"] == "Fix flaky test"
    assert envelope["payload"]["url"] == "https://linear.app/issue/TEAM-42"
    assert envelope["payload"]["team"] == "Platform"
    assert envelope["payload"]["source"] == "linear"


def test_webhook_non_issue_event_skips_envelope(client: TestClient) -> None:
    """Non-issue events should not produce a dispatch envelope."""
    payload = _make_payload(type_="Cycle", data={"id": "cycle-1"})
    response = client.post("/api/linear/webhook", json=payload, headers=API_AUTH)
    assert response.status_code == 200
    data = response.json()
    assert "envelope" not in data


def test_webhook_envelope_missing_team(client: TestClient) -> None:
    """Envelope should still be built when team data is absent."""
    payload = _make_payload(
        type_="Issue",
        data={"id": "issue-2", "title": "No team here"},
    )
    response = client.post("/api/linear/webhook", json=payload, headers=API_AUTH)
    assert response.status_code == 200
    data = response.json()
    assert "envelope" in data
    assert data["envelope"]["payload"]["team"] == ""
    assert data["envelope"]["payload"]["issue_id"] == "issue-2"


# ─── Health endpoint tests ───────────────────────────────────────────────────


def test_webhook_health_returns_status(client: TestClient) -> None:
    response = client.get("/api/linear/webhook/health", headers=API_AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["signature_verification"] == "disabled"


def test_webhook_health_shows_enabled_when_secret_set(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_WEBHOOK_SECRET", "present")
    response = client.get("/api/linear/webhook/health", headers=API_AUTH)
    assert response.status_code == 200
    assert response.json()["signature_verification"] == "enabled"


# ─── CSRF bypass test ───────────────────────────────────────────────────────────


def test_webhook_bypasses_csrf_check(client: TestClient) -> None:
    """The webhook endpoint should not be blocked by CSRF."""
    payload = _make_payload()
    response = client.post("/api/linear/webhook", json=payload, headers=API_AUTH)
    assert response.status_code == 200
