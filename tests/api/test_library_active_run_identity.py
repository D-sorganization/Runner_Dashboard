"""Real run-token identity must bind only to its own run's citation ledger."""

from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import identity
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from library.db import init_library_db
from library.retrieval_ledger import authorize_run_owner, references_for_run
from library.search import SearchHit
from library.sources import add_source
from routers import library as library_routes
from staff import tokens

pytestmark = pytest.mark.unit
CITATION = "[ref:desk:shared:w1:p1#sha256=bbbbbbbb:p.2-2]"


@pytest.fixture
def run_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    manager = identity.IdentityManager(tmp_path / "identity")
    monkeypatch.setattr(identity, "identity_manager", manager)
    monkeypatch.setattr(tokens, "identity_manager", manager)
    monkeypatch.setattr(tokens, "_active_run_tokens", {})
    presets = dict(identity.SCOPE_PRESETS)
    monkeypatch.setattr(identity, "SCOPE_PRESETS", presets)
    monkeypatch.setattr(tokens, "SCOPE_PRESETS", presets)
    runs = {
        run_id: SimpleNamespace(role="research-scout", requested_by="principal:operator")
        for run_id in ("run-agent", "run-other")
    }
    store = SimpleNamespace(get_run=runs.get)
    monkeypatch.setattr("staff.store.get_run_store", lambda: store)
    token = tokens.mint_run_token("research-scout", "run-agent", ["runner.start"], 60)
    principal = manager.verify_token(token)
    assert principal is not None
    return SimpleNamespace(manager=manager, runs=runs, store=store, token=token, principal=principal)


def test_actual_active_run_token_can_authorize_its_own_ledger(run_identity: SimpleNamespace) -> None:
    assert authorize_run_owner(run_identity.store, run_id="run-agent", principal_id=run_identity.principal.id) is None
    assert authorize_run_owner(run_identity.store, run_id="run-agent", principal_id="operator") is None


@pytest.mark.parametrize("mode", ["other-run", "wrong-role", "unbound", "revoked"])
def test_run_identity_requires_exact_stored_role_and_active_binding(run_identity: SimpleNamespace, mode: str) -> None:
    run_id = "run-agent"
    if mode == "other-run":
        run_id = "run-other"
    elif mode == "wrong-role":
        run_identity.runs[run_id].role = "different-role"
    elif mode == "unbound":
        tokens._active_run_tokens.pop(run_id)
    else:
        tokens.revoke_run_token(run_id)
    with pytest.raises(PermissionError):
        authorize_run_owner(run_identity.store, run_id=run_id, principal_id=run_identity.principal.id)


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_identity: SimpleNamespace) -> TestClient:
    monkeypatch.setenv("HUB_FLEET_TOKEN", secrets.token_urlsafe(32))
    monkeypatch.setattr(library_routes, "_loopback_auth_enabled", lambda: False)
    root = tmp_path / "shared"
    root.mkdir()
    db = init_library_db(tmp_path / "library.sqlite")
    add_source(db, source_id="shared", label="Shared", root_path=root, shared=True)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO works(id,sha256,title,format,status) VALUES(1,?,'Guide','txt','active')", ("a" * 64,))
        conn.execute(
            "INSERT INTO files(work_id,source_id,rel_path,mtime,size,sha256,status,source_root) "
            "VALUES(1,'shared','guide.txt',0,5,?,'present',?)",
            ("b" * 64, str(root)),
        )
        conn.execute(
            "INSERT INTO passages(id,work_id,page_start,page_end,text,token_estimate) VALUES(1,1,2,2,?,4)",
            ("Evidence for the retrieval ledger.",),
        )
    monkeypatch.setattr(library_routes, "_db_override", db)
    monkeypatch.setattr("dashboard_config.HOSTNAME", "desk")
    hit = SearchHit(1, 1, None, "Guide", None, 2, 2, "Evidence", 1.0, None, 4, "shared", "b" * 64, "a" * 64)
    monkeypatch.setattr(library_routes, "fetch_passage", lambda *_args, **_kwargs: hit)
    monkeypatch.setattr(library_routes, "search_passages", lambda *_args, **_kwargs: [hit])
    app = FastAPI()
    app.include_router(library_routes.router)
    return TestClient(app)


@pytest.mark.parametrize("route", ["fetch", "search"])
def test_authenticated_run_retrieves_records_and_verifies_its_own_citation(
    api: TestClient, run_identity: SimpleNamespace, route: str
) -> None:
    headers = {"Authorization": "Bearer " + run_identity.token, "X-Staff-Run-ID": "run-agent"}
    params = {"scope": "local", **({"cite": CITATION} if route == "fetch" else {"q": "Evidence"})}
    response = api.get("/api/v1/library/" + route, params=params, headers=headers)
    assert response.status_code == 200
    assert len(references_for_run(library_routes._db(), "run-agent")) == 1
    verified = api.post(
        "/api/v1/library/verify",
        headers=headers,
        json={"content": "Evidence for the retrieval ledger. " + CITATION, "run_id": "run-agent"},
    )
    assert verified.status_code == 200 and verified.json()["verified"] is True
    assert verified.json()["citations"][0]["status"] == "verified"


@pytest.mark.parametrize("route", ["fetch", "search"])
def test_authenticated_run_without_header_records_its_own_retrieval(
    api: TestClient, run_identity: SimpleNamespace, route: str
) -> None:
    response = api.get(
        "/api/v1/library/" + route,
        params={"scope": "local", **({"cite": CITATION} if route == "fetch" else {"q": "Evidence"})},
        headers={"Authorization": "Bearer " + run_identity.token},
    )
    assert response.status_code == 200
    assert len(references_for_run(library_routes._db(), "run-agent")) == 1


def test_derived_run_identity_still_checks_the_stored_role(api: TestClient, run_identity: SimpleNamespace) -> None:
    run_identity.runs["run-agent"].role = "different-role"
    response = api.get(
        "/api/v1/library/search",
        params={"q": "Evidence", "scope": "local"},
        headers={"Authorization": "Bearer " + run_identity.token},
    )
    assert response.status_code == 403
    assert references_for_run(library_routes._db(), "run-agent") == []


def test_active_run_token_cannot_select_another_run_it_requested(
    api: TestClient, run_identity: SimpleNamespace
) -> None:
    run_identity.runs["run-other"].requested_by = run_identity.principal.id
    response = api.get(
        "/api/v1/library/search",
        params={"q": "Evidence", "scope": "local"},
        headers={"Authorization": "Bearer " + run_identity.token, "X-Staff-Run-ID": "run-other"},
    )
    assert response.status_code == 403
    assert references_for_run(library_routes._db(), "run-other") == []


def test_active_run_token_cannot_verify_against_another_owned_run_ledger(
    api: TestClient, run_identity: SimpleNamespace
) -> None:
    run_identity.runs["run-other"].requested_by = run_identity.principal.id
    response = api.post(
        "/api/v1/library/verify",
        headers={"Authorization": "Bearer " + run_identity.token},
        json={"content": CITATION, "run_id": "run-other"},
    )
    assert response.status_code == 403
    assert references_for_run(library_routes._db(), "run-other") == []


def test_authenticated_run_verification_without_run_id_uses_its_ledger(
    api: TestClient, run_identity: SimpleNamespace
) -> None:
    headers = {"Authorization": "Bearer " + run_identity.token, "X-Staff-Run-ID": "run-agent"}
    retrieved = api.get(
        "/api/v1/library/search",
        params={"q": "Evidence", "scope": "local"},
        headers=headers,
    )
    assert retrieved.status_code == 200
    assert len(references_for_run(library_routes._db(), "run-agent")) == 1
    verified = api.post(
        "/api/v1/library/verify",
        headers={"Authorization": "Bearer " + run_identity.token},
        json={"content": "Evidence for the retrieval ledger. " + CITATION},
    )
    assert verified.status_code == 200
    assert verified.json()["citations"][0]["status"] == "verified"


def test_authenticated_run_without_retrieval_cannot_verify_unrecorded_citation(
    api: TestClient, run_identity: SimpleNamespace
) -> None:
    verified = api.post(
        "/api/v1/library/verify",
        headers={"Authorization": "Bearer " + run_identity.token},
        json={"content": "Evidence for the retrieval ledger. " + CITATION},
    )
    assert verified.status_code == 200
    assert verified.json()["citations"][0]["status"] == "unretrieved_reference"


def test_verification_rejects_conflicting_explicit_run_id_sources(
    api: TestClient, run_identity: SimpleNamespace
) -> None:
    response = api.post(
        "/api/v1/library/verify",
        headers={"Authorization": "Bearer " + run_identity.token, "X-Staff-Run-ID": "run-agent"},
        json={"content": CITATION, "run_id": "run-other"},
    )
    assert response.status_code == 422


def test_non_run_operator_without_run_id_keeps_non_ledger_behavior(api: TestClient) -> None:
    from identity import Principal

    api.app.dependency_overrides[library_routes.require_library_peer] = lambda: Principal(
        id="operator", type="human", name="Operator", roles=["admin"]
    )
    response = api.get("/api/v1/library/search", params={"q": "Evidence", "scope": "local"})
    assert response.status_code == 200
    assert references_for_run(library_routes._db(), "run-agent") == []


@pytest.mark.parametrize("run_id", ["run-other", "missing"])
def test_authenticated_run_cannot_record_or_verify_other_runs(
    api: TestClient, run_identity: SimpleNamespace, run_id: str
) -> None:
    headers = {"Authorization": "Bearer " + run_identity.token, "X-Staff-Run-ID": run_id}
    response = api.get("/api/v1/library/fetch", params={"cite": CITATION, "scope": "local"}, headers=headers)
    assert response.status_code == 403
    verified = api.post("/api/v1/library/verify", headers=headers, json={"content": CITATION, "run_id": run_id})
    assert verified.status_code == response.status_code
    assert references_for_run(library_routes._db(), run_id) == []


def test_run_ownership_does_not_grant_missing_read_scopes(api: TestClient, run_identity: SimpleNamespace) -> None:
    token = tokens.mint_run_token("research-scout", "run-agent", [], 60)
    response = api.get(
        "/api/v1/library/fetch",
        params={"cite": CITATION},
        headers={"Authorization": "Bearer " + token, "X-Staff-Run-ID": "run-agent"},
    )
    assert response.status_code == 403
    assert references_for_run(library_routes._db(), "run-agent") == []


@pytest.mark.parametrize("mode", ["revoked", "expired"])
def test_inactive_token_cannot_authenticate_ledger_writes(
    api: TestClient, run_identity: SimpleNamespace, mode: str
) -> None:
    if mode == "revoked":
        tokens.revoke_run_token("run-agent")
    else:
        for record in run_identity.manager._ephemeral_tokens.values():
            record.expires_at = 1
    assert run_identity.manager.verify_token(run_identity.token) is None
    response = api.get(
        "/api/v1/library/fetch",
        params={"cite": CITATION},
        headers={"Authorization": "Bearer " + run_identity.token, "X-Staff-Run-ID": "run-agent"},
    )
    assert response.status_code == 401
    assert references_for_run(library_routes._db(), "run-agent") == []
