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


def test_missing_session_middleware_raises_500(monkeypatch, tmp_path) -> None:
    import auth_webauthn
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from identity import Principal, require_principal

    monkeypatch.setattr(auth_webauthn, "_CREDENTIALS_PATH", tmp_path / "webauthn_credentials.json")

    app = FastAPI()

    def principal() -> Principal:
        return Principal(id="human:test", type="human", name="Test User", roles=["operator"])

    app.dependency_overrides[require_principal] = principal
    app.include_router(auth_webauthn.router)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/auth/webauthn/register/begin", json={"label": "phone"})
    assert response.status_code == 500


def test_corrupted_credentials_json(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    cred_file = tmp_path / "webauthn_credentials.json"

    cred_file.write_text('{"not": "a list"}')
    response = client.get("/api/auth/webauthn/credentials")
    assert response.status_code == 200
    assert response.json()["credentials"] == []

    cred_file.write_text("{invalid_json")
    response = client.get("/api/auth/webauthn/credentials")
    assert response.status_code == 200
    assert response.json()["credentials"] == []


def test_revoked_credentials_filtered(monkeypatch, tmp_path) -> None:
    import time

    import auth_webauthn

    credential_path = tmp_path / "webauthn_credentials.json"
    monkeypatch.setattr(auth_webauthn, "_CREDENTIALS_PATH", credential_path)
    auth_webauthn._save_credentials(
        [
            auth_webauthn.WebAuthnCredentialRecord(
                user_id="human:test",
                credential_id="cred-revoked",
                public_key="public-key",
                sign_count=0,
                revoked_at=time.time(),
            ),
            auth_webauthn.WebAuthnCredentialRecord(
                user_id="human:test",
                credential_id="cred-active",
                public_key="public-key",
                sign_count=0,
            ),
        ]
    )
    client = _client(monkeypatch, tmp_path)

    listed = client.get("/api/auth/webauthn/credentials")
    assert listed.status_code == 200
    assert [item["credential_id"] for item in listed.json()["credentials"]] == ["cred-active"]


def test_challenge_expiration_and_storage(monkeypatch, tmp_path) -> None:
    import time

    import auth_webauthn
    from fastapi import Request

    client = _client(monkeypatch, tmp_path)

    response1 = client.post("/api/auth/webauthn/register/begin", json={})
    assert response1.status_code == 200

    monkeypatch.setattr(auth_webauthn, "_CHALLENGE_TTL_SECONDS", -10)
    response2 = client.post("/api/auth/webauthn/register/begin", json={})
    assert response2.status_code == 200

    scope = {"type": "http", "session": {}}
    req = Request(scope)

    challenge1 = auth_webauthn.WebAuthnChallenge(
        challenge="c1", signature="s1", user_id="u1", ceremony="register", expires_at=time.time() - 100
    )
    challenge2 = auth_webauthn.WebAuthnChallenge(
        challenge="c2", signature="s2", user_id="u1", ceremony="register", expires_at=time.time() + 100
    )

    auth_webauthn._store_challenge(req, challenge1)
    assert len(req.session["webauthn_challenges"]) == 1

    auth_webauthn._store_challenge(req, challenge2)
    assert len(req.session["webauthn_challenges"]) == 1
    assert req.session["webauthn_challenges"][0]["challenge"] == "c2"


def test_challenge_signature_tampering() -> None:
    import auth_webauthn

    sig1 = auth_webauthn._sign_challenge("chal1", "user1", "register", 12345.0)
    sig2 = auth_webauthn._sign_challenge("chal2", "user1", "register", 12345.0)
    assert sig1 != sig2

    sig3 = auth_webauthn._sign_challenge("chal1", "user1", "assert", 12345.0)
    assert sig1 != sig3


def _seed_active_credential(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sign_count: int = 7) -> Path:
    import auth_webauthn

    auth_webauthn._webauthn_rate.clear()
    credential_path = tmp_path / "webauthn_credentials.json"
    monkeypatch.setattr(auth_webauthn, "_CREDENTIALS_PATH", credential_path)
    auth_webauthn._save_credentials(
        [
            auth_webauthn.WebAuthnCredentialRecord(
                user_id="human:test",
                credential_id="cred-current",
                public_key="public-key",
                sign_count=sign_count,
                label="Phone",
            )
        ]
    )
    return credential_path


def _session_unlocked(auth_webauthn_mod: object, session: dict[str, object]) -> bool:
    from fastapi import Request

    req = Request({"type": "http", "session": session})
    return bool(auth_webauthn_mod.is_session_unlocked(req))  # type: ignore[attr-defined]


def _assert_payload(bdata: dict[str, object], **overrides: object) -> dict[str, object]:
    cred: dict[str, object] = {
        "id": "cred-current",
        "challenge": bdata["challenge"],
        "challenge_signature": bdata["challenge_signature"],
        "sign_count": 8,
    }
    cred.update(overrides)
    return {"credential": cred}


def test_assert_complete_error_leaves_session_locked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import auth_webauthn

    _seed_active_credential(monkeypatch, tmp_path)

    def exploding_verifier(record: object, challenge: object, credential: dict[str, object]) -> bool:
        raise RuntimeError("verifier hardware failure")

    monkeypatch.setattr(auth_webauthn, "_assertion_verifier", exploding_verifier, raising=False)
    client = _client(monkeypatch, tmp_path)
    bdata = client.post("/api/auth/webauthn/assert/begin", json={}).json()

    resp = client.post("/api/auth/webauthn/assert/complete", json=_assert_payload(bdata))
    assert resp.status_code == 401
    req = auth_webauthn.Request({"type": "http", "session": {"webauthn_unlocked": True}})
    with pytest.raises(auth_webauthn.HTTPException):
        auth_webauthn.verify_assertion(
            req,
            auth_webauthn.Principal(id="human:test", type="human", name="Test User", roles=["operator"]),
            {"id": "cred-current", "challenge": bdata["challenge"]},
        )
    assert _session_unlocked(auth_webauthn, req.session) is False


def test_assert_complete_timeout_leaves_session_locked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import time

    import auth_webauthn

    _seed_active_credential(monkeypatch, tmp_path)
    monkeypatch.setattr(auth_webauthn, "_assertion_verifier", lambda *_a: True, raising=False)

    now = time.time()
    req_expired = auth_webauthn.Request({"type": "http", "session": {"webauthn_unlocked": True}})
    principal = auth_webauthn.Principal(id="human:test", type="human", name="Test User", roles=["operator"])
    expired_chal = auth_webauthn.WebAuthnChallenge(
        challenge="expired-c1",
        signature=auth_webauthn._sign_challenge("expired-c1", principal.id, "assert", now - 5),
        user_id=principal.id,
        ceremony="assert",
        expires_at=now - 5,
    )
    req_expired.session["webauthn_challenges"] = [expired_chal.model_dump()]
    with pytest.raises(auth_webauthn.HTTPException) as exc_info:
        auth_webauthn.verify_assertion(
            req_expired,
            principal,
            {
                "id": "cred-current",
                "challenge": "expired-c1",
                "challenge_signature": expired_chal.signature,
                "sign_count": 8,
            },
        )
    assert exc_info.value.status_code == 408
    assert _session_unlocked(auth_webauthn, req_expired.session) is False

    def timeout_verifier(*_a: object) -> bool:
        raise TimeoutError("authenticator timed out")

    monkeypatch.setattr(auth_webauthn, "_assertion_verifier", timeout_verifier, raising=False)
    client = _client(monkeypatch, tmp_path)
    bdata = client.post("/api/auth/webauthn/assert/begin", json={}).json()
    resp = client.post("/api/auth/webauthn/assert/complete", json=_assert_payload(bdata))
    assert resp.status_code == 408


def test_assert_complete_bad_signature_leaves_session_locked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import auth_webauthn

    _seed_active_credential(monkeypatch, tmp_path)
    monkeypatch.setattr(auth_webauthn, "_assertion_verifier", lambda *_a: True, raising=False)
    client = _client(monkeypatch, tmp_path)

    begin1 = client.post("/api/auth/webauthn/assert/begin", json={}).json()
    tampered = client.post(
        "/api/auth/webauthn/assert/complete",
        json=_assert_payload(begin1, challenge_signature="tampered-signature"),
    )
    assert tampered.status_code == 401

    monkeypatch.setattr(auth_webauthn, "_assertion_verifier", lambda *_a: False, raising=False)
    begin2 = client.post("/api/auth/webauthn/assert/begin", json={}).json()
    bad_sig = client.post("/api/auth/webauthn/assert/complete", json=_assert_payload(begin2))
    assert bad_sig.status_code == 401


def test_assert_complete_replay_leaves_session_locked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import auth_webauthn

    _seed_active_credential(monkeypatch, tmp_path, sign_count=7)
    monkeypatch.setattr(auth_webauthn, "_assertion_verifier", lambda *_a: True, raising=False)
    client = _client(monkeypatch, tmp_path)

    begin1 = client.post("/api/auth/webauthn/assert/begin", json={}).json()
    payload = _assert_payload(begin1)
    first = client.post("/api/auth/webauthn/assert/complete", json=payload)
    assert first.status_code == 200

    replay_challenge = client.post("/api/auth/webauthn/assert/complete", json=payload)
    assert replay_challenge.status_code == 401

    begin2 = client.post("/api/auth/webauthn/assert/begin", json={}).json()
    replay_counter = client.post("/api/auth/webauthn/assert/complete", json=_assert_payload(begin2))
    assert replay_counter.status_code == 401


def test_mobile_webauthn_component_never_unlocks_on_error_or_missing_webauthn() -> None:
    mobile_tsx = (Path(__file__).parent.parent / "frontend" / "src" / "pages" / "Credentials" / "Mobile.tsx").read_text(
        encoding="utf-8"
    )
    assert "/api/auth/webauthn/assert/complete" in mobile_tsx
    assert mobile_tsx.count("unlocked = true") == 1
