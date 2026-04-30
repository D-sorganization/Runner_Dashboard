from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    import auth_webauthn
    from identity import Principal, require_principal

    monkeypatch.setattr(auth_webauthn, "_CREDENTIALS_PATH", tmp_path / "webauthn_credentials.json")

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")

    def principal() -> Principal:
        return Principal(id="human:test", type="human", name="Test User", roles=["operator"])

    app.dependency_overrides[require_principal] = principal
    app.include_router(auth_webauthn.router)
    return TestClient(app)


def test_register_begin_returns_server_challenge(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)

    response = client.post("/api/auth/webauthn/register/begin", json={"label": "phone"})

    assert response.status_code == 200
    data = response.json()
    assert data["challenge"]
    assert data["challenge_signature"]
    assert data["user"]["id"] == "human:test"
    assert data["timeout_ms"] == 300000


def test_complete_endpoints_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)

    register = client.post("/api/auth/webauthn/register/complete", json={"credential": {"id": "cred-1"}})
    assertion = client.post("/api/auth/webauthn/assert/complete", json={"credential": {"id": "cred-1"}})

    assert register.status_code == 501
    assert assertion.status_code == 501


def test_assert_begin_requires_active_registered_credential(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)

    response = client.post("/api/auth/webauthn/assert/begin", json={})

    assert response.status_code == 404
    assert response.json()["detail"] == "No active WebAuthn credential registered for this user"


def test_list_and_revoke_only_current_user_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import auth_webauthn

    credential_path = tmp_path / "webauthn_credentials.json"
    monkeypatch.setattr(auth_webauthn, "_CREDENTIALS_PATH", credential_path)
    auth_webauthn._save_credentials(
        [
            auth_webauthn.WebAuthnCredentialRecord(
                user_id="human:test",
                credential_id="cred-current",
                public_key="public-key",
                sign_count=7,
                label="Phone",
            ),
            auth_webauthn.WebAuthnCredentialRecord(
                user_id="human:other",
                credential_id="cred-other",
                public_key="public-key",
                sign_count=1,
            ),
        ]
    )
    client = _client(monkeypatch, tmp_path)

    listed = client.get("/api/auth/webauthn/credentials")
    revoked = client.delete("/api/auth/webauthn/credentials/cred-current")
    listed_after = client.get("/api/auth/webauthn/credentials")

    assert listed.status_code == 200
    assert [item["credential_id"] for item in listed.json()["credentials"]] == ["cred-current"]
    assert revoked.status_code == 200
    assert listed_after.json()["credentials"] == []
