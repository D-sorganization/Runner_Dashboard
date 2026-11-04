"""Per-run JWT bearer verification (#3879 phase C).

Fail-closed cryptographic checks (iss/aud/exp/nbf/claims) plus ephemeral hash
registration via ``IdentityManager.resolve_bearer_token``.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import jwt
import pytest
from identity import IdentityManager, Principal
from staff import run_jwt_service as svc

REGISTRY = {
    "machines": [
        {
            "name": "hub-a",
            "role": "hub",
            "retired": False,
            "dashboard_url": "http://hub-a.fleet-example.ts.net:8321",
        }
    ]
}


@pytest.fixture
def identity_manager(tmp_path: Path) -> IdentityManager:
    mgr = IdentityManager(config_dir=tmp_path)
    mgr.principals["agent-claude"] = Principal(id="agent-claude", type="bot", name="Claude Agent", roles=["agent"])
    return mgr


@pytest.fixture(autouse=True)
def hub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc.dashboard_config, "MACHINE_ROLE", "hub")
    monkeypatch.setattr(svc, "load_machine_registry", lambda: REGISTRY)
    monkeypatch.setattr(svc, "assert_registered_fleet_node", lambda node: "hub-a")
    monkeypatch.setenv("RUN_JWT_SIGNING_KEY", "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")


def test_resolve_bearer_accepts_minted_run_jwt(identity_manager: IdentityManager) -> None:
    cred = svc.mint_run_jwt(identity_manager, run_id="run-1", principal_id="agent-claude", caller_node="hub-a")

    principal, reason = identity_manager.resolve_bearer_token(cred.token)

    assert reason is None
    assert principal is not None
    assert principal.id == "agent-claude"


def test_resolve_bearer_rejects_expired_run_jwt(identity_manager: IdentityManager, monkeypatch: pytest.MonkeyPatch) -> None:
    issued = 1_700_000_000.0
    monkeypatch.setattr(time, "time", lambda: issued)
    cred = svc.mint_run_jwt(
        identity_manager,
        run_id="run-1",
        principal_id="agent-claude",
        caller_node="hub-a",
        now=issued,
    )
    monkeypatch.setattr(time, "time", lambda: issued + svc.DEFAULT_TTL_SECONDS + 1)

    principal, reason = identity_manager.resolve_bearer_token(cred.token)

    assert principal is None
    assert reason == "run_jwt_expired"


def test_resolve_bearer_rejects_wrong_audience(identity_manager: IdentityManager) -> None:
    cred = svc.mint_run_jwt(identity_manager, run_id="run-1", principal_id="agent-claude", caller_node="hub-a")
    claims = jwt.decode(
        cred.token,
        options={"verify_signature": False},
    )
    claims["aud"] = "https://other-hub.example.ts.net"
    tampered = jwt.encode(claims, "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", algorithm="HS256")

    principal, reason = identity_manager.resolve_bearer_token(tampered)

    assert principal is None
    assert reason == "run_jwt_audience_mismatch"


def test_resolve_bearer_rejects_revoked_run_jwt_replay(identity_manager: IdentityManager) -> None:
    cred = svc.mint_run_jwt(identity_manager, run_id="run-1", principal_id="agent-claude", caller_node="hub-a")
    identity_manager.revoke_ephemeral_token(hashlib.sha256(cred.token.encode()).hexdigest())

    principal, reason = identity_manager.resolve_bearer_token(cred.token)

    assert principal is None
    assert reason == "token_hash_not_registered"


def test_run_jwt_bearer_gate_rejects_invalid_signature() -> None:
    raw = jwt.encode(
        {
            "sub": "agent-claude",
            "run_id": "run-1",
            "jti": "jti-1",
            "aud": "https://hub-a.fleet-example.ts.net",
            "iss": svc.ISSUER,
            "iat": int(time.time()),
            "nbf": int(time.time()),
            "exp": int(time.time()) + 60,
        },
        "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
        algorithm="HS256",
    )

    assert svc.run_jwt_bearer_gate(raw) == "run_jwt_invalid"


def test_run_jwt_rejection_never_logs_raw_token(
    identity_manager: IdentityManager, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    signing_key = "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"
    monkeypatch.setenv("RUN_JWT_SIGNING_KEY", signing_key)
    raw = jwt.encode(
        {
            "sub": "agent-claude",
            "run_id": "run-x",
            "jti": "abc",
            "aud": "https://hub-a.fleet-example.ts.net",
            "iss": svc.ISSUER,
            "iat": int(time.time()),
            "nbf": int(time.time()),
            "exp": int(time.time()) + 60,
        },
        signing_key,
        algorithm="HS256",
    )
    caplog.set_level(logging.WARNING)
    identity_manager.resolve_bearer_token(raw)
    assert raw not in caplog.text


def test_static_svc_token_unaffected(identity_manager: IdentityManager) -> None:
    raw = identity_manager.mint_service_token("agent-claude", name="test-token")

    principal, reason = identity_manager.resolve_bearer_token(raw)

    assert reason is None
    assert principal is not None
