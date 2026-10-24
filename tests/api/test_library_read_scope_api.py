"""A run-only capability retrieves shared content without fleet/staff access."""

from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import identity
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from library.db import init_library_db, library_db_path
from library.retrieval_ledger import references_for_run
from library.sources import add_source
from routers import library as routes
from routers import staff_references
from staff import tokens

pytestmark = pytest.mark.unit


@pytest.fixture
def reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    manager = identity.IdentityManager(tmp_path / "identity")
    monkeypatch.setattr(identity, "identity_manager", manager)
    monkeypatch.setattr(tokens, "identity_manager", manager)
    monkeypatch.setattr(tokens, "_active_run_tokens", {})
    presets = dict(identity.SCOPE_PRESETS)
    monkeypatch.setattr(identity, "SCOPE_PRESETS", presets)
    monkeypatch.setattr(tokens, "SCOPE_PRESETS", presets)
    monkeypatch.setattr(identity, "_loopback_auth_enabled", lambda: False)
    monkeypatch.setattr(routes, "_loopback_auth_enabled", lambda: False)
    monkeypatch.setenv("HUB_FLEET_TOKEN", secrets.token_urlsafe(32))
    monkeypatch.delenv("STAFF_RUN_ID", raising=False)
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(tmp_path / "library"))
    monkeypatch.setattr("dashboard_config.HOSTNAME", "desk")
    monkeypatch.setattr("dashboard_config.FLEET_NODES", {})
    db = init_library_db(library_db_path())
    for wid, (source, shared) in enumerate((("shared", True), ("private", False)), 1):
        root = tmp_path / source
        root.mkdir()
        add_source(db, source_id=source, label=source, root_path=root, shared=shared)
        with sqlite3.connect(db) as conn:
            digest = str(wid) * 64
            conn.execute("INSERT INTO works(id,sha256,title,format) VALUES(?,?,?,'txt')", (wid, digest, source))
            conn.execute(
                "INSERT INTO files(work_id,source_id,rel_path,mtime,size,sha256,source_root) VALUES(?,?,?,0,20,?,?)",
                (wid, source, "private-local-name.txt", digest, str(root)),
            )
            conn.execute(
                "INSERT INTO passages(id,work_id,page_start,page_end,text,token_estimate) VALUES(?,?,1,1,?,4)",
                (
                    wid,
                    wid,
                    f"Evidence {source} retrieval material",
                ),
            )
            conn.execute(
                "INSERT INTO passages_fts(rowid,title,text) VALUES(?,?,?)",
                (wid, source, f"Evidence {source} retrieval material"),
            )
    monkeypatch.setattr(routes, "_db_override", db)
    runs = {
        rid: SimpleNamespace(role="research-scout", requested_by="principal:operator")
        for rid in ("run-own", "run-other")
    }
    store = SimpleNamespace(get_run=runs.get)
    monkeypatch.setattr("staff.store.get_run_store", lambda: store)
    monkeypatch.setattr(staff_references, "get_run_store", lambda: store)
    token = tokens.mint_run_token("research-scout", "run-own", [], 60)
    # Simulate the explicit capability only; use real token authentication and registry.
    presets["staff-run:run-own"].append("library.read")
    manager._ephemeral_principals["staff:research-scout:run-own"].scopes.append("library.read")
    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(staff_references.router, prefix="/api/v1/staff")
    return SimpleNamespace(
        api=TestClient(app),
        token=token,
        manager=manager,
        runs=runs,
        db=db,
        headers={"Authorization": "Bearer " + token},
    )


@pytest.mark.parametrize("scope", ["local", "federated"])
def test_shared_search_ignores_visibility_override_and_records_own_ledger(reader: SimpleNamespace, scope: str) -> None:
    response = reader.api.get(
        "/api/v1/library/search", params={"q": "Evidence", "scope": scope, "shared": "false"}, headers=reader.headers
    )
    assert response.status_code == 200, response.text
    passages = response.json()["passages"]
    assert [p["source_id"] for p in passages] == ["shared"]
    assert all("file_path" not in p for p in passages)
    assert [row["source_id"] for row in references_for_run(reader.db, "run-own")] == ["shared"]


@pytest.mark.parametrize("locator", [False, True])
@pytest.mark.parametrize("source,wid,expected", [("shared", 1, 200), ("private", 2, 404)])
def test_fetch_forces_shared_filter(
    reader: SimpleNamespace, locator: bool, source: str, wid: int, expected: int
) -> None:
    params = {"scope": "local", "shared": "false"}
    params.update(
        {"source_id": source, "work_id": wid, "page": 1}
        if locator
        else {"cite": f"[ref:desk:{source}:w{wid}:p{wid}#sha256={str(wid) * 8}:p.1-1]"}
    )
    response = reader.api.get("/api/v1/library/fetch", params=params, headers=reader.headers)
    assert response.status_code == expected, response.text
    if expected == 404:
        assert "retrieval material" not in response.text
        assert references_for_run(reader.db, "run-own") == []


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/sources"),
        ("GET", "/sources/verify"),
        ("GET", "/stats"),
        ("GET", "/works/1"),
        ("GET", "/browse"),
        ("GET", "/submissions"),
        ("POST", "/submissions"),
        ("POST", "/sources"),
        ("PATCH", "/sources/shared"),
        ("DELETE", "/sources/shared"),
        ("POST", "/sources/shared/reindex"),
        ("POST", "/verify"),
        ("GET", "/verify?cite=placeholder"),
        ("GET", "/intake"),
        ("POST", "/intake"),
        ("POST", "/intake/1/promote"),
    ],
)
def test_scope_does_not_authorize_other_library_routes(reader: SimpleNamespace, method: str, path: str) -> None:
    response = reader.api.request(method, "/api/v1/library" + path, json={}, headers=reader.headers)
    assert response.status_code == 403, response.text


def test_owned_text_free_ledger_is_accessible_but_other_run_is_not(reader: SimpleNamespace) -> None:
    reader.api.get(
        "/api/v1/library/fetch",
        params={"source_id": "shared", "work_id": 1, "page": 1, "scope": "local"},
        headers=reader.headers,
    )
    response = reader.api.get("/api/v1/staff/runs/run-own/references", headers=reader.headers)
    assert response.status_code == 200, response.text
    assert response.json()["references"][0]["source_id"] == "shared"
    assert "text" not in response.text and "private-local-name" not in response.text
    reader.runs["run-other"].requested_by = "staff:research-scout:run-own"
    assert reader.api.get("/api/v1/staff/runs/run-other/references", headers=reader.headers).status_code == 403


def test_foreign_run_fails_before_search_or_ledger_access(
    reader: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader.runs["run-other"].requested_by = "staff:research-scout:run-own"
    monkeypatch.setattr(routes, "_db", lambda: pytest.fail("DB accessed before authorization"))
    response = reader.api.get(
        "/api/v1/library/search",
        params={"q": "Evidence", "scope": "local"},
        headers={**reader.headers, "X-Staff-Run-ID": "run-other"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("mode", ["revoked", "expired", "unbound", "wrong-role"])
def test_inactive_or_mismatched_run_cannot_read(reader: SimpleNamespace, mode: str) -> None:
    if mode == "revoked":
        tokens.revoke_run_token("run-own")
    elif mode == "expired":
        for record in reader.manager._ephemeral_tokens.values():
            record.expires_at = 1
    elif mode == "unbound":
        tokens._active_run_tokens.clear()
    else:
        reader.runs["run-own"].role = "other-role"
    response = reader.api.get(
        "/api/v1/library/search", params={"q": "Evidence", "scope": "local"}, headers=reader.headers
    )
    assert response.status_code in (401, 403)
    assert references_for_run(reader.db, "run-own") == []


@pytest.mark.parametrize(
    "path", ["/api/v1/library/search?q=Evidence&scope=local", "/api/v1/staff/runs/run-own/references"]
)
def test_revocation_after_authentication_precedes_data_access(
    reader: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    from routers import library_read

    def authenticate_then_revoke(*_args: object) -> identity.Principal:
        principal = reader.manager.verify_token(reader.token)
        assert principal is not None
        tokens.revoke_run_token("run-own")
        return principal

    monkeypatch.setattr(library_read, "_resolve_principal_optional", authenticate_then_revoke)
    monkeypatch.setattr(routes, "_db", lambda: pytest.fail("data access after revocation"))
    assert reader.api.get(path, headers=reader.headers).status_code == 403
