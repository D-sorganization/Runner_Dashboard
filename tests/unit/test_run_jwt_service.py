"""Tests for the per-run JWT mint helper (#3879 phase B).

Contract: docs/architecture/per-run-jwt-credentials.md. This is the mint-path
helper only — verification wiring at real call sites is a later, separate PR
per the sequencing doc.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import jwt
import pytest
from fastapi import HTTPException
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


def test_mint_run_jwt_registers_a_resolvable_ephemeral_token(identity_manager: IdentityManager) -> None:
    cred = svc.mint_run_jwt(identity_manager, run_id="run-1", principal_id="agent-claude", caller_node="hub-a")

    principal, reason = identity_manager.resolve_bearer_token(cred.token)
    assert reason is None
    assert principal is not None
    assert principal.id == "agent-claude"


def test_mint_run_jwt_claims_match_the_sequencing_contract(identity_manager: IdentityManager) -> None:
    cred = svc.mint_run_jwt(identity_manager, run_id="run-1", principal_id="agent-claude", caller_node="hub-a")

    claims = jwt.decode(
        cred.token,
        "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        algorithms=["HS256"],
        audience="https://hub-a.fleet-example.ts.net",
        issuer=svc.ISSUER,
    )
    assert claims["sub"] == "agent-claude"
    assert claims["run_id"] == "run-1"
    assert claims["jti"] == cred.jti
    assert claims["exp"] - claims["iat"] == svc.DEFAULT_TTL_SECONDS


def test_mint_run_jwt_two_runs_for_one_principal_get_independent_tokens(identity_manager: IdentityManager) -> None:
    cred_a = svc.mint_run_jwt(identity_manager, run_id="run-a", principal_id="agent-claude", caller_node="hub-a")
    cred_b = svc.mint_run_jwt(identity_manager, run_id="run-b", principal_id="agent-claude", caller_node="hub-a")

    assert cred_a.token != cred_b.token
    assert cred_a.jti != cred_b.jti
    for cred in (cred_a, cred_b):
        principal, reason = identity_manager.resolve_bearer_token(cred.token)
        assert reason is None
        assert principal is not None


def test_revoking_one_run_token_leaves_its_sibling_run_valid(identity_manager: IdentityManager) -> None:
    cred_a = svc.mint_run_jwt(identity_manager, run_id="run-a", principal_id="agent-claude", caller_node="hub-a")
    cred_b = svc.mint_run_jwt(identity_manager, run_id="run-b", principal_id="agent-claude", caller_node="hub-a")

    identity_manager.revoke_ephemeral_token(hashlib.sha256(cred_a.token.encode()).hexdigest())

    principal_a, reason_a = identity_manager.resolve_bearer_token(cred_a.token)
    assert principal_a is None
    assert reason_a == "token_hash_not_registered"

    principal_b, reason_b = identity_manager.resolve_bearer_token(cred_b.token)
    assert reason_b is None
    assert principal_b is not None


def test_mint_run_jwt_rejects_blank_run_id(identity_manager: IdentityManager) -> None:
    with pytest.raises(HTTPException) as exc_info:
        svc.mint_run_jwt(identity_manager, run_id="   ", principal_id="agent-claude", caller_node="hub-a")
    assert exc_info.value.status_code == 422


def test_mint_run_jwt_rejects_unknown_principal(identity_manager: IdentityManager) -> None:
    with pytest.raises(HTTPException) as exc_info:
        svc.mint_run_jwt(identity_manager, run_id="run-1", principal_id="nobody", caller_node="hub-a")
    assert exc_info.value.status_code == 422


def test_mint_run_jwt_refuses_off_hub(identity_manager: IdentityManager, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc.dashboard_config, "MACHINE_ROLE", "node")
    with pytest.raises(HTTPException) as exc_info:
        svc.mint_run_jwt(identity_manager, run_id="run-1", principal_id="agent-claude", caller_node="hub-a")
    assert exc_info.value.status_code == 404


def test_mint_run_jwt_requires_signing_key_configured(
    identity_manager: IdentityManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RUN_JWT_SIGNING_KEY", raising=False)
    with pytest.raises(HTTPException) as exc_info:
        svc.mint_run_jwt(identity_manager, run_id="run-1", principal_id="agent-claude", caller_node="hub-a")
    assert exc_info.value.status_code == 503


def test_mint_run_jwt_requires_a_unique_hub_origin(
    identity_manager: IdentityManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(svc, "load_machine_registry", lambda: {"machines": []})
    with pytest.raises(HTTPException) as exc_info:
        svc.mint_run_jwt(identity_manager, run_id="run-1", principal_id="agent-claude", caller_node="hub-a")
    assert exc_info.value.status_code == 503
