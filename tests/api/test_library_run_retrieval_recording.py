from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from library.db import init_library_db
from library.sources import add_source
from routers import library as library_routes

READER = Principal(id="researcher", type="user", name="Researcher", roles=["viewer"], scopes=["fleet.read"])


def _app(tmp_path: Path, monkeypatch) -> FastAPI:
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
        conn.commit()
    library_routes.set_db_path(db)
    app = FastAPI()
    app.include_router(library_routes.router)
    app.dependency_overrides[require_scope("fleet.read")] = lambda: READER
    return app


def test_search_records_canonical_references_for_owned_run(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    runner = SimpleNamespace(
        store=SimpleNamespace(get_run=lambda run_id: SimpleNamespace(requested_by="principal:researcher"))
    )
    monkeypatch.setattr("staff.store.get_run_store", lambda: runner.store)
    from library.search import SearchHit

    monkeypatch.setattr(
        library_routes,
        "search_passages",
        lambda *_args, **_kwargs: [
            SearchHit(1, 1, None, "Guide", None, 2, 2, "Evidence", 1.0, None, 4, "shared", "b" * 64, "a" * 64)
        ],
    )

    response = TestClient(app).get(
        "/api/v1/library/search",
        params={"q": "retrieval ledger", "scope": "local"},
        headers={"X-Staff-Run-ID": "run-1"},
    )

    assert response.status_code == 200
    from library.retrieval_ledger import references_for_run

    rows = references_for_run(library_routes._db(), "run-1")
    assert len(rows) == 1
    assert rows[0]["node"] == response.json()["passages"][0]["node"]
    assert rows[0]["file_sha256"] == "b" * 64
    assert rows[0]["page_start"] == 2 and rows[0]["page_end"] == 2


def test_search_rejects_unknown_and_other_owner_runs_before_search(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    runner = SimpleNamespace(store=SimpleNamespace(get_run=lambda _run_id: None))
    monkeypatch.setattr("staff.store.get_run_store", lambda: runner.store)
    search_calls: list[bool] = []
    monkeypatch.setattr(library_routes, "search_passages", lambda *_args, **_kwargs: search_calls.append(True) or [])
    client = TestClient(app)
    malformed = client.get(
        "/api/v1/library/search",
        params={"q": "retrieval ledger", "scope": "local"},
        headers={"X-Staff-Run-ID": "../other"},
    )
    assert malformed.status_code == 422

    unknown = client.get(
        "/api/v1/library/search",
        params={"q": "retrieval ledger", "scope": "local"},
        headers={"X-Staff-Run-ID": "unknown"},
    )
    assert unknown.status_code == 404

    runner.store.get_run = lambda _run_id: SimpleNamespace(requested_by="principal:someone-else")
    forbidden = client.get(
        "/api/v1/library/search",
        params={"q": "retrieval ledger", "scope": "local"},
        headers={"X-Staff-Run-ID": "run-1"},
    )
    assert forbidden.status_code == 403
    assert search_calls == []


def test_search_without_run_id_does_not_create_ledger(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    response = TestClient(app).get("/api/v1/library/search", params={"q": "retrieval ledger", "scope": "local"})
    assert response.status_code == 200
    with sqlite3.connect(library_routes._db()) as conn:
        assert (
            conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='staff_run_references'").fetchone()
            is None
        )


def test_federated_search_records_peer_reference_for_owned_run(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    runner = SimpleNamespace(store=SimpleNamespace(get_run=lambda _run_id: SimpleNamespace(requested_by="researcher")))
    monkeypatch.setattr("staff.store.get_run_store", lambda: runner.store)

    async def federated_search(*_args, **_kwargs):
        return SimpleNamespace(
            query="retrieval",
            topic=None,
            repo=None,
            budget_tokens=100,
            tokens_used=4,
            passages=[
                {
                    "node": "peer-a",
                    "source_id": "shared",
                    "work_id": 2,
                    "passage_id": 9,
                    "file_sha256": "c" * 64,
                    "page_start": 6,
                    "page_end": 7,
                    "text": "peer evidence",
                }
            ],
            unavailable_sources=[],
        )

    monkeypatch.setattr(library_routes, "federated_search", federated_search)
    response = TestClient(app).get(
        "/api/v1/library/search",
        params={"q": "retrieval"},
        headers={"X-Staff-Run-ID": "run-2"},
    )

    assert response.status_code == 200
    from library.retrieval_ledger import references_for_run

    rows = references_for_run(library_routes._db(), "run-2")
    assert [(row["node"], row["passage_id"], row["file_sha256"]) for row in rows] == [("peer-a", 9, "c" * 64)]


def test_fetch_records_canonical_references_for_owned_run(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    runner = SimpleNamespace(
        store=SimpleNamespace(get_run=lambda _run_id: SimpleNamespace(requested_by="principal:researcher"))
    )
    monkeypatch.setattr("staff.store.get_run_store", lambda: runner.store)
    from library.search import SearchHit

    monkeypatch.setattr(
        library_routes,
        "fetch_passage",
        lambda *_args, **_kwargs: SearchHit(
            1,
            1,
            None,
            "Guide",
            None,
            2,
            2,
            "Evidence for the retrieval ledger.",
            1.0,
            None,
            4,
            "shared",
            "b" * 64,
            "a" * 64,
        ),
    )
    cite = "[ref:local:shared:w1:p1#sha256=bbbbbbbb:p.2-2]"
    response = TestClient(app).get(
        "/api/v1/library/fetch",
        params={"cite": cite, "scope": "local"},
        headers={"X-Staff-Run-ID": "run-3"},
    )

    assert response.status_code == 200
    from library.retrieval_ledger import references_for_run

    rows = references_for_run(library_routes._db(), "run-3")
    assert len(rows) == 1
    assert rows[0]["source_id"] == "shared"
    assert rows[0]["work_id"] == 1 and rows[0]["passage_id"] == 1
    assert rows[0]["file_sha256"] == "b" * 64
    assert rows[0]["page_start"] == 2 and rows[0]["page_end"] == 2
    assert rows[0]["node"] == response.json()["node"]


def test_fetch_rejects_unowned_run_before_fetching(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    runner = SimpleNamespace(store=SimpleNamespace(get_run=lambda _run_id: None))
    monkeypatch.setattr("staff.store.get_run_store", lambda: runner.store)
    monkeypatch.setattr(
        library_routes,
        "fetch_passage",
        lambda *_args, **_kwargs: pytest.fail("fetch happened before run authorization"),
    )
    response = TestClient(app).get(
        "/api/v1/library/fetch",
        params={"cite": "[ref:local:shared:w1:p1#sha256=bbbbbbbb:p.2-2]", "scope": "local"},
        headers={"X-Staff-Run-ID": "unknown"},
    )
    assert response.status_code == 404
