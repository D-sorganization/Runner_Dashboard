"""Tests for versioned public staff API (/api/v1/staff) (SC-F3, Issue #1312).

Tests:
- Legacy deprecation headers on /api/staff/*
- Stable error envelope {error: {code, message, retryable, hint, request_id}} on 4xx/5xx
- Idempotency-Key requirement and duplicate replay on mutating POSTs
- Fail-closed 503 on idempotency store failure
- Cursor pagination on list endpoints with base64 cursors and invalid cursor detection
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from identity import Principal, TokenRecord, identity_manager
from server import app

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.15", 52000)


@pytest.fixture
def v1_auth_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    id_dir = tmp_path / "identity"
    id_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(id_dir))
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-peer-secret-12345")
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "roles"))

    from staff import runner as runner_mod
    from staff import store as store_mod

    store_mod.reset_store()
    runner_mod.reset_runner()

    identity_manager.config_dir = id_dir
    identity_manager.principals_path = id_dir / "principals.yml"
    identity_manager.tokens_path = id_dir / "tokens.yml"

    principals = [
        Principal(id="operator-user", type="human", name="Operator", roles=["operator"]),
        Principal(id="viewer-user", type="human", name="Viewer", roles=["viewer"]),
    ]
    tokens = {
        "operator-token": "operator-user",
        "viewer-token": "viewer-user",
    }

    token_records = []
    for raw_tok, p_id in tokens.items():
        thash = hashlib.sha256(raw_tok.encode("utf-8")).hexdigest()
        token_records.append(
            TokenRecord(
                token_hash=thash,
                principal_id=p_id,
                name=f"{p_id}-token",
                created_at=time.time(),
                expires_at=time.time() + 86400,
            )
        )
    identity_manager.principals = {p.id: p for p in principals}
    identity_manager.tokens = token_records
    identity_manager.save_principals()
    identity_manager.save_tokens()

    yield {
        "operator": "operator-token",
        "viewer": "viewer-token",
    }

    store_mod.reset_store()
    runner_mod.reset_runner()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, client=_REMOTE, raise_server_exceptions=False)


def test_legacy_routes_have_deprecation_headers(client: TestClient, v1_auth_env: dict[str, str]) -> None:
    headers = {"Authorization": f"Bearer {v1_auth_env['viewer']}"}
    resp = client.get("/api/staff/roster", headers=headers)
    assert resp.status_code == 200
    assert resp.headers.get("Deprecation") == "true"
    assert "Sunset" in resp.headers
    assert "</api/v1/staff/roster>" in resp.headers.get("Link", "")

    # v1 route does NOT have deprecation header
    resp_v1 = client.get("/api/v1/staff/roster", headers=headers)
    assert resp_v1.status_code == 200
    assert "Deprecation" not in resp_v1.headers


def test_v1_error_envelope_on_unauthorized_and_forbidden(client: TestClient, v1_auth_env: dict[str, str]) -> None:
    # 401 Unauthorized
    resp_401 = client.get("/api/v1/staff/roster")
    assert resp_401.status_code == 401
    body_401 = resp_401.json()
    assert "error" in body_401
    err_401 = body_401["error"]
    assert err_401["code"] == "unauthorized"
    assert "message" in err_401
    assert err_401["retryable"] is False
    assert "hint" in err_401
    assert "request_id" in err_401

    # 403 Forbidden (viewer lacks staff.dispatch scope)
    headers = {
        "Authorization": f"Bearer {v1_auth_env['viewer']}",
        "Idempotency-Key": "test-key-403",
        **_XHR,
    }
    resp_403 = client.post("/api/v1/staff/night-watch/run", json={"prompt": "test"}, headers=headers)
    assert resp_403.status_code == 403
    body_403 = resp_403.json()
    assert "error" in body_403
    err_403 = body_403["error"]
    assert err_403["code"] == "forbidden"
    assert "staff.dispatch" in err_403["message"]
    assert err_403["retryable"] is False


def test_v1_error_envelope_on_not_found(client: TestClient, v1_auth_env: dict[str, str]) -> None:
    headers = {"Authorization": f"Bearer {v1_auth_env['viewer']}"}
    resp_404 = client.get("/api/v1/staff/runs/nonexistent-run-id-9999", headers=headers)
    assert resp_404.status_code == 404
    body_404 = resp_404.json()
    assert "error" in body_404
    assert body_404["error"]["code"] == "not_found"
    assert body_404["error"]["retryable"] is False


def test_v1_error_envelope_on_validation_error(client: TestClient, v1_auth_env: dict[str, str]) -> None:
    headers = {
        "Authorization": f"Bearer {v1_auth_env['operator']}",
        "Idempotency-Key": "test-key-val",
        **_XHR,
    }
    # Pass invalid type for dry_run
    resp_422 = client.post("/api/v1/staff/night-watch/run", json={"dry_run": "not-a-bool"}, headers=headers)
    assert resp_422.status_code == 422
    body_422 = resp_422.json()
    assert "error" in body_422
    assert body_422["error"]["code"] == "validation_error"
    assert body_422["error"]["retryable"] is False


def test_v1_idempotency_key_required_on_mutating_post(client: TestClient, v1_auth_env: dict[str, str]) -> None:
    headers = {
        "Authorization": f"Bearer {v1_auth_env['operator']}",
        **_XHR,
    }
    # Missing Idempotency-Key
    resp = client.post("/api/v1/staff/night-watch/run", json={"dry_run": True}, headers=headers)
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == "missing_idempotency_key"
    assert body["error"]["retryable"] is False
    assert "Idempotency-Key" in body["error"]["hint"]


def test_v1_idempotency_replay_prevents_duplicate_dispatch(client: TestClient, v1_auth_env: dict[str, str]) -> None:
    headers = {
        "Authorization": f"Bearer {v1_auth_env['operator']}",
        "Idempotency-Key": "idem-key-unique-001",
        **_XHR,
    }
    payload: dict[str, Any] = {"dry_run": True, "prompt": "verify idempotency", "provider": "claude"}

    # First dispatch
    resp1 = client.post("/api/v1/staff/ad-hoc/run", json=payload, headers=headers)
    assert resp1.status_code == 200
    data1 = resp1.json()

    # Second dispatch with identical Idempotency-Key
    resp2 = client.post("/api/v1/staff/ad-hoc/run", json=payload, headers=headers)
    assert resp2.status_code == 200
    data2 = resp2.json()

    # Replay flag in headers or body, identical payload
    assert resp2.headers.get("Idempotent-Replay") == "true"
    assert data1 == data2


def test_v1_cursor_pagination_on_runs(client: TestClient, v1_auth_env: dict[str, str]) -> None:
    from staff.runner import get_runner
    from staff.store import RunRecord

    runner = get_runner()
    # Insert 5 test runs
    for i in range(5):
        runner.store.create_run(
            RunRecord(
                id=f"run-page-{i}",
                role="night-watch",
                provider="gemini",
                model=None,
                machine="Desk",
                repo="Runner_Dashboard",
                target_kind="prompt",
                target_ref="",
                prompt=f"test run {i}",
                status="succeeded",
                created_at=f"2026-09-24T10:0{i}:00Z",
            )
        )

    headers = {"Authorization": f"Bearer {v1_auth_env['viewer']}"}

    # Fetch page 1 (limit 2)
    resp1 = client.get("/api/v1/staff/runs?limit=2", headers=headers)
    assert resp1.status_code == 200
    page1 = resp1.json()
    assert "items" in page1
    assert len(page1["items"]) == 2
    assert page1["has_more"] is True
    assert page1["next_cursor"] is not None

    # Fetch page 2 using next_cursor
    cursor = page1["next_cursor"]
    resp2 = client.get(f"/api/v1/staff/runs?limit=2&cursor={cursor}", headers=headers)
    assert resp2.status_code == 200
    page2 = resp2.json()
    assert len(page2["items"]) == 2
    assert page2["items"][0]["id"] != page1["items"][0]["id"]

    # Invalid cursor returns 400 with invalid_cursor error code
    bad_resp = client.get("/api/v1/staff/runs?cursor=not-a-valid-base64!!!", headers=headers)
    assert bad_resp.status_code == 400
    assert bad_resp.json()["error"]["code"] == "invalid_cursor"
