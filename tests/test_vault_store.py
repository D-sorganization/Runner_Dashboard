"""Tests for Secure Storage Vault (/api/v1/vault) encryption, permissions, and audit trail (#2542)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal
from routers import vault as vault_router
from vault_store import resolve_vault_handle


@pytest.fixture()
def vault_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path, Path]:
    key_path = tmp_path / "config" / "vault.key"
    db_path = tmp_path / "state" / "vault.sqlite3"
    monkeypatch.setenv("RUNNER_DASHBOARD_VAULT_KEY_PATH", str(key_path))
    monkeypatch.setenv("RUNNER_DASHBOARD_VAULT_DB_PATH", str(db_path))
    app = FastAPI()
    app.include_router(vault_router.router)
    operator = Principal(id="operator", type="human", name="Dieter", roles=["admin"], scopes=["*"])
    app.dependency_overrides[vault_router._require_vault_operator] = lambda: operator
    return TestClient(app), key_path, db_path


def test_vault_encrypts_at_rest_and_never_leaks_canary_across_surfaces(
    vault_env: tuple[TestClient, Path, Path],
) -> None:
    client, key_path, db_path = vault_env
    canary = "vault-test-canary-secret-phrase"
    res = client.post(
        "/api/v1/vault/secrets",
        json={
            "key": "PROD_API_TOKEN",
            "value": canary,
            "scope": "repo:Runner_Dashboard",
            "description": "Production token",
        },
    )
    assert res.status_code == 200, res.text
    assert res.headers.get("cache-control") == "no-store"
    body = res.json()
    assert body["key"] == "PROD_API_TOKEN"
    assert body["handle"] == "$vault:PROD_API_TOKEN"
    assert body["masked_value"] == "••••••••"
    assert canary not in res.text

    # Master key created with strict 0600 mode
    assert (key_path.stat().st_mode & 0o777) == 0o600
    # Raw SQLite file on disk must not contain plaintext canary
    assert canary.encode("utf-8") not in db_path.read_bytes()

    # List endpoint never returns plaintext canary
    list_res = client.get("/api/v1/vault/secrets")
    assert list_res.status_code == 200
    assert list_res.headers.get("cache-control") == "no-store"
    assert canary not in list_res.text
    assert list_res.json()["secrets"][0]["handle"] == "$vault:PROD_API_TOKEN"

    # Internal handle resolution decrypts with AAD and logs 'resolve'
    resolved = resolve_vault_handle("$vault:PROD_API_TOKEN", actor="worker-1", scope="repo:Runner_Dashboard")
    assert resolved == canary
    with pytest.raises(PermissionError):
        resolve_vault_handle("$vault:PROD_API_TOKEN", actor="worker-1", scope="other")

    # Delete secret and verify hash-chained audit trail
    del_res = client.delete("/api/v1/vault/secrets/PROD_API_TOKEN")
    assert del_res.status_code == 200
    audit_res = client.get("/api/v1/vault/audit")
    assert audit_res.status_code == 200
    audit_data = audit_res.json()
    assert audit_data["chain_valid"] is True
    assert [e["action"] for e in audit_data["entries"]] == ["delete", "resolve", "resolve", "list", "write"]
    assert canary not in audit_res.text


def test_vault_fails_closed_when_master_key_permissions_are_unsafe(
    vault_env: tuple[TestClient, Path, Path],
) -> None:
    client, key_path, _db_path = vault_env
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(os.urandom(32))
    os.chmod(key_path, 0o644)
    res = client.post(
        "/api/v1/vault/secrets",
        json={"key": "UNSAFE_KEY", "value": "secret-val"},
    )
    assert res.status_code == 503
    assert "unsafe permissions" in res.text
