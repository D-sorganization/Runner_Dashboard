from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from library.db import init_library_db
from library.sources import add_source
from routers import library as library_routes

READER = Principal(id="r", type="user", name="Reader", roles=["viewer"], scopes=["fleet.read"])


def _app(tmp_path: Path) -> Path:
    root = tmp_path / "shared"
    root.mkdir(exist_ok=True)
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
            "INSERT INTO passages(id,work_id,page_start,page_end,text,token_estimate) "
            "VALUES(7,1,4,5,'Bounded passage text',3)"
        )
    library_routes.set_db_path(db)
    return db


def _client(tmp_path: Path) -> TestClient:
    db = _app(tmp_path)
    return _client_with_existing_db(db)


def _client_with_existing_db(db: Path) -> TestClient:
    library_routes.set_db_path(db)
    app = FastAPI()
    app.include_router(library_routes.router)
    app.dependency_overrides[require_scope("fleet.read")] = lambda: READER
    return TestClient(app)


def test_fetch_resolves_source_work_page_locator(tmp_path: Path, monkeypatch) -> None:
    import dashboard_config

    monkeypatch.setattr(dashboard_config, "HOSTNAME", "local")
    response = _client(tmp_path).get(
        "/api/v1/library/fetch",
        params={"source_id": "shared", "work_id": 1, "page": 5, "scope": "local"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source_id"] == "shared"
    assert body["work_id"] == 1 and body["passage_id"] == 7
    assert body["page_start"] == 4 and body["page_end"] == 5
    assert body["cite_key"] == "[ref:local:shared:w1:p7#sha256=bbbbbbbb:p.4-5]"
    assert body["file_sha256"] == "b" * 64
    assert "file_path" not in body and "guide.txt" not in response.text


def test_page_locator_requires_complete_coordinates_and_excludes_cite(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/v1/library/fetch", params={"source_id": "shared"}).status_code == 422
    assert (
        client.get("/api/v1/library/fetch", params={"source_id": "shared", "work_id": 1, "scope": "local"}).status_code
        == 422
    )
    assert (
        client.get(
            "/api/v1/library/fetch",
            params={
                "cite": "[ref:local:shared:w1:p7#sha256=bbbbbbbb:p.4-5]",
                "source_id": "shared",
                "work_id": 1,
                "page": 5,
            },
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/v1/library/fetch", params={"source_id": "../shared", "work_id": 1, "page": 5, "scope": "local"}
        ).status_code
        == 422
    )


def test_peer_page_locator_cannot_discover_private_source(tmp_path: Path) -> None:
    db = _app(tmp_path)
    root = tmp_path / "private"
    root.mkdir()
    add_source(db, source_id="private", label="Private", root_path=root, shared=False)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO works(id,sha256,title,format,status) VALUES(2,?,'Private','txt','active')",
            ("d" * 64,),
        )
        conn.execute(
            "INSERT INTO files(work_id,source_id,rel_path,mtime,size,sha256,status,source_root) "
            "VALUES(2,'private','hidden.txt',0,4,?,'present',?)",
            ("e" * 64, str(root)),
        )
        conn.execute(
            "INSERT INTO passages(id,work_id,page_start,page_end,text,token_estimate) VALUES(8,2,1,1,'secret',1)"
        )
    app = FastAPI()
    app.include_router(library_routes.router)
    peer = Principal(id="fleet-peer", type="bot", name="Peer", roles=["fleet-peer"])
    app.dependency_overrides[library_routes.require_library_peer] = lambda: peer
    response = TestClient(app).get(
        "/api/v1/library/fetch",
        params={"source_id": "private", "work_id": 2, "page": 1, "scope": "local"},
    )
    assert response.status_code == 404
    assert "secret" not in response.text and "hidden.txt" not in response.text


def test_page_locator_records_owned_run_reference(tmp_path: Path, monkeypatch) -> None:
    db = _app(tmp_path)
    runner = SimpleNamespace(store=SimpleNamespace(get_run=lambda _run_id: SimpleNamespace(requested_by="principal:r")))
    monkeypatch.setattr("staff.store.get_run_store", lambda: runner.store)
    response = _client_with_existing_db(db).get(
        "/api/v1/library/fetch",
        params={"source_id": "shared", "work_id": 1, "page": 5, "scope": "local"},
        headers={"X-Staff-Run-ID": "run-locator"},
    )
    assert response.status_code == 200
    from library.retrieval_ledger import references_for_run

    rows = references_for_run(db, "run-locator")
    assert len(rows) == 1
    assert rows[0]["source_id"] == "shared" and rows[0]["work_id"] == 1
    assert rows[0]["passage_id"] == 7 and rows[0]["file_sha256"] == "b" * 64
    assert rows[0]["page_start"] == 4 and rows[0]["page_end"] == 5
    assert rows[0]["node"] == response.json()["node"]


def test_page_locator_rejects_unowned_run_before_lookup(tmp_path: Path, monkeypatch) -> None:
    db = _app(tmp_path)
    runner = SimpleNamespace(store=SimpleNamespace(get_run=lambda _run_id: None))
    monkeypatch.setattr("staff.store.get_run_store", lambda: runner.store)

    def fail_lookup(*_args, **_kwargs):
        raise AssertionError("page lookup happened before run authorization")

    monkeypatch.setattr("library.fetch_locator.fetch_locator", fail_lookup)
    response = _client_with_existing_db(db).get(
        "/api/v1/library/fetch",
        params={"source_id": "shared", "work_id": 1, "page": 5, "scope": "local"},
        headers={"X-Staff-Run-ID": "unknown"},
    )
    assert response.status_code == 404
