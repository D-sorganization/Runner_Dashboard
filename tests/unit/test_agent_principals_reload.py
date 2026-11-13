"""Live agent-principal reload without a hub restart (#4256 ask 1).

Every token here is a fixture written into a temporary identity store; the
reload report and its logs must never echo a token value or its hash.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import agent_principal_defaults
import identity
import pytest
import yaml
from agent_principal_defaults import AgentPrincipalReloadError, reload_agent_principals
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


def test_malformed_row_applies_nothing(mgr: IdentityManager, config_path: Path) -> None:
    """A row the merge cannot parse (``roles: 5``) must not leave the rows before it applied."""
    _add_newcomer(mgr, config_path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["agents"].append({"id": "agent-broken", "roles": 5})
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    _write_token(mgr.config_dir, "agent-broken", "svc_fixture_broken_reload")
    principals_before, tokens_before = dict(mgr.principals), list(mgr.tokens)

    with pytest.raises(AgentPrincipalReloadError):
        reload_agent_principals(mgr)

    assert mgr.principals == principals_before
    assert mgr.tokens == tokens_before
    assert mgr.verify_token(_NEW_TOKEN) is None


def test_failed_reload_does_not_resurrect_a_token_revoked_meanwhile(
    mgr: IdentityManager, config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A revoke that lands while a reload is failing must stay revoked."""
    doomed = mgr.mint_service_token("plain-bot", name="fixture-doomed")
    doomed_hash = hashlib.sha256(doomed.encode()).hexdigest()

    def revoke_then_fail(manager: IdentityManager) -> dict[str, Path]:
        mgr.revoke_token(doomed_hash)
        raise OSError("token directory unreadable")

    monkeypatch.setattr(agent_principal_defaults, "agent_token_files", revoke_then_fail)
    with pytest.raises(AgentPrincipalReloadError):
        reload_agent_principals(mgr)

    assert mgr.verify_token(doomed) is None
    assert all(t.token_hash != doomed_hash for t in mgr.tokens)


def test_successful_reload_keeps_a_concurrent_revoke_and_mint(
    mgr: IdentityManager, config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_newcomer(mgr, config_path)
    doomed = mgr.mint_service_token("plain-bot", name="fixture-doomed")
    real_token_files = agent_principal_defaults.agent_token_files
    minted: list[str] = []

    def revoke_and_mint_then_list(manager: IdentityManager) -> dict[str, Path]:
        mgr.revoke_token(hashlib.sha256(doomed.encode()).hexdigest())
        minted.append(mgr.mint_service_token("plain-bot", name="fixture-concurrent"))
        return real_token_files(manager)

    monkeypatch.setattr(agent_principal_defaults, "agent_token_files", revoke_and_mint_then_list)
    result = reload_agent_principals(mgr)

    assert result["added"] == [_NEW_ID]
    assert mgr.verify_token(doomed) is None
    assert mgr.verify_token(minted[0]) is mgr.principals["plain-bot"]
    assert mgr.verify_token(_NEW_TOKEN) is mgr.principals[_NEW_ID]


def _concurrent_writer(mgr: IdentityManager, monkeypatch: pytest.MonkeyPatch, write, *, times: int) -> list[int]:
    """Run ``write`` right after the merge computed its result, as another caller would mid-reload."""
    real_merge = agent_principal_defaults.merge_agent_principal_defaults
    calls: list[int] = []

    def merge_then_write(manager: IdentityManager) -> None:
        real_merge(manager)
        calls.append(1)
        if len(calls) <= times:
            write()

    monkeypatch.setattr(agent_principal_defaults, "merge_agent_principal_defaults", merge_then_write)
    return calls


def test_reload_keeps_a_principal_replaced_by_another_caller_meanwhile(
    mgr: IdentityManager, config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(config_path, with_newcomer=False, grok_scopes=("fleet.read",))

    def replace() -> None:
        mgr.principals["agent-grok"] = mgr.principals["agent-grok"].model_copy(update={"scopes": ["staff.read"]})

    calls = _concurrent_writer(mgr, monkeypatch, replace, times=1)
    result = reload_agent_principals(mgr)

    assert set(mgr.principals["agent-grok"].scopes) == {"staff.read", "fleet.read"}
    assert result["updated"] == ["agent-grok"]
    assert len(calls) == 2  # the stale merge was redone on the fresh state


def test_reload_keeps_a_principal_edited_in_place_meanwhile(
    mgr: IdentityManager, config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(config_path, with_newcomer=False, grok_scopes=("fleet.read",))

    def edit() -> None:
        mgr.principals["agent-grok"].scopes = ["staff.read"]

    _concurrent_writer(mgr, monkeypatch, edit, times=1)
    reload_agent_principals(mgr)

    assert set(mgr.principals["agent-grok"].scopes) == {"staff.read", "fleet.read"}


def test_reload_that_cannot_settle_fails_and_applies_nothing(
    mgr: IdentityManager, config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_newcomer(mgr, config_path)
    _write_config(config_path, with_newcomer=True, grok_scopes=("fleet.read",))
    seen: list[str] = []

    def replace() -> None:
        seen.append(f"staff.read.{len(seen)}")
        mgr.principals["agent-grok"] = mgr.principals["agent-grok"].model_copy(update={"scopes": [seen[-1]]})

    calls = _concurrent_writer(mgr, monkeypatch, replace, times=99)
    with pytest.raises(AgentPrincipalReloadError):
        reload_agent_principals(mgr)

    assert len(calls) == agent_principal_defaults.RELOAD_ATTEMPTS
    assert mgr.principals["agent-grok"].scopes == [seen[-1]]
    assert _NEW_ID not in mgr.principals
    assert mgr.verify_token(_NEW_TOKEN) is None


def test_audit_line_names_every_change_kind(
    mgr: IdentityManager,
    config_path: Path,
    client: TestClient,
    admin_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Scope widening on an existing principal and a newly attached bearer are both audited by id."""
    _write_config(config_path, with_newcomer=True, grok_scopes=("fleet.read",))
    _write_token(mgr.config_dir, _NEW_ID, _NEW_TOKEN)
    with caplog.at_level(logging.INFO):
        assert client.post(_RELOAD, headers=_bearer(admin_token)).status_code == 200
    audit = [r.getMessage() for r in caplog.records if r.getMessage().startswith("audit: agent-principals reload")]
    assert len(audit) == 1
    assert f"added=['{_NEW_ID}']" in audit[0]
    assert "updated=['agent-grok']" in audit[0]
    assert f"tokens_ingested=['{_NEW_ID}']" in audit[0]
