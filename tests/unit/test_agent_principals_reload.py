"""Live agent-principal reload without a hub restart (#4256 ask 1).

Every token here is a fixture written into a temporary identity store; the
reload report and its logs must never echo a token value or its hash.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import identity
import pytest
import yaml
from agent_principal_defaults import reload_agent_principals
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import IdentityManager, principal_has_scope
from middleware import csrf_check
from routers import admin

_RELOAD = "/api/admin/agent-principals/reload"
_CSRF = {"X-Requested-With": "XMLHttpRequest"}
_NEW_ID = "agent-newcomer"
_NEW_TOKEN = "svc_fixture_newcomer_reload"  # noqa: S105 - fixture value, not a credential
_GROK_TOKEN = "svc_fixture_grok_reload"  # noqa: S105 - fixture value, not a credential


def _write_config(path: Path, *, with_newcomer: bool, grok_scopes: tuple[str, ...] = ()) -> None:
    agents: list[dict] = [{"id": "agent-grok", "kind": "grokbot", "roles": ["bot"], "scopes": list(grok_scopes)}]
    if with_newcomer:
        agents.append({"id": _NEW_ID, "kind": "codex", "roles": ["bot"], "scopes": ["fleet.read"]})
    path.write_text(yaml.safe_dump({"agents": agents}), encoding="utf-8")


def _write_token(id_dir: Path, agent_id: str, raw: str) -> None:
    (id_dir / "agent-tokens" / f"{agent_id}.token").write_text(raw + "\n", encoding="utf-8")


def _bearer(raw: str) -> dict[str, str]:
    return {**_CSRF, "Authorization": f"Bearer {raw}"}


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "agent_principals.yml"
    _write_config(path, with_newcomer=False)
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(path))
    monkeypatch.delenv("RD_OVERLAY_PATH", raising=False)
    monkeypatch.delenv("HUB_FLEET_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_LOOPBACK_AUTH", raising=False)
    return path


@pytest.fixture
def mgr(tmp_path: Path, config_path: Path, monkeypatch: pytest.MonkeyPatch) -> IdentityManager:
    """A started identity manager: one admin bot, one plain bot, agent-grok ingested."""
    id_dir = tmp_path / "id"
    (id_dir / "agent-tokens").mkdir(parents=True)
    principals = [
        {"id": "ops-admin", "type": "bot", "name": "Ops admin", "roles": ["admin"]},
        {"id": "plain-bot", "type": "bot", "name": "Plain bot", "roles": ["bot"]},
    ]
    (id_dir / "principals.yml").write_text(yaml.safe_dump({"principals": principals}), encoding="utf-8")
    _write_token(id_dir, "agent-grok", _GROK_TOKEN)
    manager = IdentityManager(config_dir=id_dir)
    monkeypatch.setattr(identity, "identity_manager", manager)
    monkeypatch.setattr(admin, "identity_manager", manager)
    return manager


@pytest.fixture
def client(mgr: IdentityManager) -> TestClient:
    app = FastAPI()
    app.middleware("http")(csrf_check)
    app.include_router(admin.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def admin_token(mgr: IdentityManager) -> str:
    return mgr.mint_service_token("ops-admin", name="fixture-admin")


def _add_newcomer(mgr: IdentityManager, config_path: Path) -> None:
    _write_config(config_path, with_newcomer=True)
    _write_token(mgr.config_dir, _NEW_ID, _NEW_TOKEN)


def test_new_principal_is_live_only_after_reload(
    mgr: IdentityManager, config_path: Path, client: TestClient, admin_token: str
) -> None:
    _add_newcomer(mgr, config_path)
    assert mgr.resolve_bearer_token(_NEW_TOKEN) == (None, "token_hash_not_registered")
    assert client.get("/api/admin/principals", headers=_bearer(_NEW_TOKEN)).status_code == 401

    resp = client.post(_RELOAD, headers=_bearer(admin_token))

    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] == [_NEW_ID]
    assert body["tokens_ingested"] == [_NEW_ID]
    assert body["updated"] == []
    assert body["total"] == len(mgr.principals) == 4
    prin = mgr.verify_token(_NEW_TOKEN)
    assert prin is not None and prin.id == _NEW_ID
    assert prin.roles == ["bot"]
    assert prin.scopes == ["fleet.read"]
    assert prin.assistant_kind == "codex"
    assert principal_has_scope(prin, "fleet.read")
    assert not principal_has_scope(prin, "admin")
    # The newcomer now authenticates (and is refused the admin scope, not the credential).
    assert client.get("/api/admin/principals", headers=_bearer(_NEW_TOKEN)).status_code == 403


def test_reload_is_idempotent(mgr: IdentityManager, config_path: Path, client: TestClient, admin_token: str) -> None:
    _add_newcomer(mgr, config_path)
    assert client.post(_RELOAD, headers=_bearer(admin_token)).json()["added"] == [_NEW_ID]
    principals_before = {pid: p.model_dump() for pid, p in mgr.principals.items()}
    tokens_before = [t.model_dump() for t in mgr.tokens]

    body = client.post(_RELOAD, headers=_bearer(admin_token)).json()

    assert body == {"added": [], "updated": [], "tokens_ingested": [], "total": 4}
    assert {pid: p.model_dump() for pid, p in mgr.principals.items()} == principals_before
    assert [t.model_dump() for t in mgr.tokens] == tokens_before


def test_reload_with_nothing_new_changes_nothing(mgr: IdentityManager, client: TestClient, admin_token: str) -> None:
    before = {pid: p.model_dump() for pid, p in mgr.principals.items()}
    body = client.post(_RELOAD, headers=_bearer(admin_token)).json()
    assert body == {"added": [], "updated": [], "tokens_ingested": [], "total": 3}
    assert {pid: p.model_dump() for pid, p in mgr.principals.items()} == before
    assert mgr.verify_token(_GROK_TOKEN) is mgr.principals["agent-grok"]


def test_reload_reports_an_existing_principal_the_config_changed(mgr: IdentityManager, config_path: Path) -> None:
    """The startup merge unions config scopes into an existing principal; reload says so by id."""
    assert mgr.principals["agent-grok"].scopes == []
    _write_config(config_path, with_newcomer=False, grok_scopes=("fleet.read",))

    result = reload_agent_principals(mgr)

    assert result == {"added": [], "updated": ["agent-grok"], "tokens_ingested": [], "total": 3}
    assert mgr.principals["agent-grok"].scopes == ["fleet.read"]
    assert mgr.verify_token(_GROK_TOKEN) is mgr.principals["agent-grok"]


def test_reload_never_removes_principals_or_tokens(mgr: IdentityManager, config_path: Path) -> None:
    """A deleted config row or token file does not revoke anything (same as a restart-free merge)."""
    config_path.write_text(yaml.safe_dump({"agents": []}), encoding="utf-8")
    (mgr.config_dir / "agent-tokens" / "agent-grok.token").unlink()

    result = reload_agent_principals(mgr)

    assert result == {"added": [], "updated": [], "tokens_ingested": [], "total": 3}
    assert mgr.verify_token(_GROK_TOKEN) is mgr.principals["agent-grok"]


def test_non_admin_caller_is_forbidden(mgr: IdentityManager, config_path: Path, client: TestClient) -> None:
    _add_newcomer(mgr, config_path)
    bot_token = mgr.mint_service_token("plain-bot", name="fixture-bot")
    resp = client.post(_RELOAD, headers=_bearer(bot_token))
    assert resp.status_code == 403
    assert _NEW_ID not in mgr.principals


def test_barb_role_is_not_enough(mgr: IdentityManager, client: TestClient) -> None:
    mgr.principals["plain-bot"] = mgr.principals["plain-bot"].model_copy(update={"roles": ["bot", "barb"]})
    bot_token = mgr.mint_service_token("plain-bot", name="fixture-barb")
    assert client.post(_RELOAD, headers=_bearer(bot_token)).status_code == 403


def test_unauthenticated_caller_is_rejected(mgr: IdentityManager, config_path: Path, client: TestClient) -> None:
    _add_newcomer(mgr, config_path)
    assert client.post(_RELOAD, headers=_CSRF).status_code == 401
    assert client.post(_RELOAD, headers=_bearer("svc_fixture_unknown")).status_code == 401
    assert _NEW_ID not in mgr.principals


def test_missing_csrf_sentinel_is_rejected(
    mgr: IdentityManager, config_path: Path, client: TestClient, admin_token: str
) -> None:
    _add_newcomer(mgr, config_path)
    resp = client.post(_RELOAD, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 403
    assert _NEW_ID not in mgr.principals


def test_response_and_logs_carry_ids_only(
    mgr: IdentityManager,
    config_path: Path,
    client: TestClient,
    admin_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _add_newcomer(mgr, config_path)
    with caplog.at_level(logging.DEBUG):
        resp = client.post(_RELOAD, headers=_bearer(admin_token))

    assert set(resp.json()) == {"added", "updated", "tokens_ingested", "total"}
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert _NEW_ID in logged
    assert "ops-admin" in logged
    secrets_ = [_NEW_TOKEN, _GROK_TOKEN, admin_token]
    secrets_ += [hashlib.sha256(raw.encode()).hexdigest() for raw in secrets_]
    secrets_ += [t.token_hash for t in mgr.tokens]
    for haystack in (resp.text, json.dumps(resp.json()), logged, caplog.text):
        assert all(secret not in haystack for secret in secrets_)


def test_unreadable_config_fails_closed_without_leaking(
    mgr: IdentityManager, config_path: Path, client: TestClient, admin_token: str
) -> None:
    _write_token(mgr.config_dir, _NEW_ID, _NEW_TOKEN)
    config_path.write_text("agents: [unterminated\n", encoding="utf-8")
    before = {pid: p.model_dump() for pid, p in mgr.principals.items()}

    resp = client.post(_RELOAD, headers=_bearer(admin_token))

    assert resp.status_code == 500
    assert _NEW_TOKEN not in resp.text
    assert str(config_path) not in resp.text
    assert {pid: p.model_dump() for pid, p in mgr.principals.items()} == before
    assert mgr.resolve_bearer_token(_NEW_TOKEN) == (None, "token_hash_not_registered")


def test_route_is_a_sync_admin_post() -> None:
    import inspect

    route = next(r for r in admin.router.routes if getattr(r, "path", "") == _RELOAD)
    assert route.methods == {"POST"}
    assert not inspect.iscoroutinefunction(route.endpoint)
