from __future__ import annotations

import asyncio
import sqlite3
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from library.db import init_library_db
from library.sources import add_source
from routers import library as library_routes

READER = Principal(id="r", type="user", name="Reader", roles=["viewer"], scopes=["fleet.read"])


def _client(tmp_path: Path) -> TestClient:
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
            "INSERT INTO passages(id,work_id,page_start,page_end,text,token_estimate) VALUES(7,1,4,5,'Local passage',3)"
        )
    library_routes.set_db_path(db)
    app = FastAPI()
    app.include_router(library_routes.router)
    app.dependency_overrides[require_scope("fleet.read")] = lambda: READER
    return TestClient(app)


def _peer_payload(node: str, digest: str = "b" * 64) -> dict[str, object]:
    return {
        "citation": "citation",
        "cite_key": f"[ref:{node}:shared:w1:p7#sha256={digest[:8]}:p.4-5]",
        "cite_uri": "cite://library",
        "file_sha256": digest,
        "work_sha256": "a" * 64,
        "source_id": "shared",
        "node": node,
        "work_id": 1,
        "passage_id": 7,
        "page_start": 4,
        "page_end": 5,
        "text": f"passage from {node}",
        "token_estimate": 3,
    }


def _mock_nodes(monkeypatch, handler) -> list[str]:
    import dashboard_config

    requested: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.host or "")
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: client)
    monkeypatch.setattr(
        dashboard_config, "FLEET_NODES", {"hub": "http://hub", "laptop": "http://laptop", "x9": "http://x9"}
    )
    monkeypatch.setattr(dashboard_config, "HOSTNAME", "hub")
    monkeypatch.setenv("HUB_FLEET_TOKEN", "test-token")
    return requested


def test_explicit_registered_node_fetches_without_querying_other_offline_peers(tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "laptop"
        assert request.url.params.get("scope") == "local"
        payload = _peer_payload("laptop")
        payload["text"] = "🧭" * 500
        return httpx.Response(200, json=payload)

    requested = _mock_nodes(monkeypatch, handler)
    response = _client(tmp_path).get(
        "/api/v1/library/fetch",
        params={"source_id": "shared", "work_id": 1, "page": 5, "scope": "local", "node": "laptop"},
    )

    assert response.status_code == 200
    assert response.json()["node"] == "laptop"
    assert len(response.json()["text"].encode("utf-8")) <= 400
    assert requested == ["laptop"]


def test_local_scope_can_fetch_private_source_without_contacting_peers(tmp_path: Path, monkeypatch) -> None:
    import dashboard_config

    client = _client(tmp_path)
    db = library_routes._db()
    root = tmp_path / "private"
    root.mkdir()
    add_source(db, source_id="private", label="Private", root_path=root, shared=False)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO works(id,sha256,title,format,status) VALUES(2,?,'Private','txt','active')", ("d" * 64,)
        )
        conn.execute(
            "INSERT INTO files(work_id,source_id,rel_path,mtime,size,sha256,status,source_root) "
            "VALUES(2,'private','secret.txt',0,6,?,'present',?)",
            ("e" * 64, str(root)),
        )
        conn.execute(
            "INSERT INTO passages(id,work_id,page_start,page_end,text,token_estimate) "
            "VALUES(8,2,1,1,'private passage',2)"
        )
    monkeypatch.setattr(dashboard_config, "FLEET_NODES", {"hub": "http://hub", "laptop": "http://offline"})
    monkeypatch.setattr(dashboard_config, "HOSTNAME", "hub")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: pytest.fail("local scope must not access peers"))

    response = client.get(
        "/api/v1/library/fetch",
        params={"source_id": "private", "work_id": 2, "page": 1, "scope": "local"},
    )

    assert response.status_code == 200
    assert response.json()["text"] == "private passage"
    assert response.json()["node"] == "hub"


def test_unqualified_federation_fails_closed_when_a_registered_peer_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "laptop":
            return httpx.Response(200, json=_peer_payload("laptop"))
        raise httpx.ConnectError("peer is offline", request=request)

    requested = _mock_nodes(monkeypatch, handler)
    response = _client(tmp_path).get("/api/v1/library/fetch", params={"source_id": "shared", "work_id": 1, "page": 5})

    assert response.status_code == 503
    assert set(requested) == {"laptop", "x9"}
    assert "Local passage" not in response.text and "passage from laptop" not in response.text


def test_unqualified_federation_rejects_conflicting_registered_identities(tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        node = request.url.host or ""
        digest = "b" * 64 if node == "laptop" else "c" * 64
        return httpx.Response(200, json=_peer_payload(node, digest))

    _mock_nodes(monkeypatch, handler)
    response = _client(tmp_path).get("/api/v1/library/fetch", params={"source_id": "shared", "work_id": 1, "page": 5})

    assert response.status_code == 409
    assert "passage from" not in response.text


def test_unqualified_federation_uses_one_global_deadline(tmp_path: Path, monkeypatch) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.3)
        return httpx.Response(200, json=_peer_payload(request.url.host or ""))

    requested = _mock_nodes(monkeypatch, handler)
    monkeypatch.setattr("routers.library_fetch.PAGE_FETCH_DEADLINE_S", 0.05)
    create_client = _client

    def slow_client_startup(path: Path) -> TestClient:
        time.sleep(0.3)
        return create_client(path)

    monkeypatch.setattr(sys.modules[__name__], "_client", slow_client_startup)
    client = _client(tmp_path)
    started = time.monotonic()
    response = client.get("/api/v1/library/fetch", params={"source_id": "shared", "work_id": 1, "page": 5})

    assert response.status_code == 503
    assert time.monotonic() - started < 0.25
    assert set(requested) == {"laptop", "x9"}


def test_identical_federated_copies_collapse_to_one_result(tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        node = request.url.host or ""
        payload = _peer_payload(node)
        payload["text"] = "identical shared passage"
        return httpx.Response(200, json=payload)

    _mock_nodes(monkeypatch, handler)
    client = _client(tmp_path)
    with sqlite3.connect(library_routes._db()) as conn:
        conn.execute("UPDATE passages SET text='identical shared passage' WHERE id=7")
    response = client.get("/api/v1/library/fetch", params={"source_id": "shared", "work_id": 1, "page": 5})

    assert response.status_code == 200
    assert response.json()["node"] == "hub"
    assert response.json()["text"] == "identical shared passage"


def test_unknown_node_is_rejected_before_any_peer_request(tmp_path: Path, monkeypatch) -> None:
    requested = _mock_nodes(monkeypatch, lambda _request: httpx.Response(200, json={}))
    response = _client(tmp_path).get(
        "/api/v1/library/fetch",
        params={"source_id": "shared", "work_id": 1, "page": 5, "scope": "local", "node": "arbitrary"},
    )

    assert response.status_code == 422
    assert requested == []


def test_unowned_run_is_rejected_before_targeted_peer_request(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    requested = _mock_nodes(monkeypatch, lambda _request: httpx.Response(200, json=_peer_payload("laptop")))
    monkeypatch.setattr("staff.store.get_run_store", lambda: SimpleNamespace(get_run=lambda _run_id: None))
    response = _client(tmp_path).get(
        "/api/v1/library/fetch",
        params={"source_id": "shared", "work_id": 1, "page": 5, "scope": "local", "node": "laptop"},
        headers={"X-Staff-Run-ID": "unowned"},
    )

    assert response.status_code == 404
    assert requested == []


def test_unowned_run_is_rejected_before_unqualified_federation(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    requested = _mock_nodes(monkeypatch, lambda _request: httpx.Response(200, json=_peer_payload("laptop")))
    monkeypatch.setattr("staff.store.get_run_store", lambda: SimpleNamespace(get_run=lambda _run_id: None))
    response = _client(tmp_path).get(
        "/api/v1/library/fetch",
        params={"source_id": "shared", "work_id": 1, "page": 5},
        headers={"X-Staff-Run-ID": "unowned"},
    )

    assert response.status_code == 404
    assert requested == []


def test_federated_conflict_does_not_record_run_reference(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    runner = SimpleNamespace(
        store=SimpleNamespace(get_run=lambda _run_id: SimpleNamespace(requested_by="principal:r"))
    )
    monkeypatch.setattr("staff.store.get_run_store", lambda: runner.store)

    def handler(request: httpx.Request) -> httpx.Response:
        node = request.url.host or ""
        digest = "b" * 64 if node == "laptop" else "c" * 64
        return httpx.Response(200, json=_peer_payload(node, digest))

    _mock_nodes(monkeypatch, handler)
    response = _client(tmp_path).get(
        "/api/v1/library/fetch",
        params={"source_id": "shared", "work_id": 1, "page": 5},
        headers={"X-Staff-Run-ID": "run-fed-conflict"},
    )

    assert response.status_code == 409
    from library.retrieval_ledger import references_for_run

    assert references_for_run(library_routes._db(), "run-fed-conflict") == []


def test_federated_page_locator_records_owned_run_reference(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    runner = SimpleNamespace(
        store=SimpleNamespace(get_run=lambda _run_id: SimpleNamespace(requested_by="principal:r"))
    )
    monkeypatch.setattr("staff.store.get_run_store", lambda: runner.store)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = _peer_payload(request.url.host or "")
        payload["text"] = "identical shared passage"
        return httpx.Response(200, json=payload)

    _mock_nodes(monkeypatch, handler)
    client = _client(tmp_path)
    with sqlite3.connect(library_routes._db()) as conn:
        conn.execute("UPDATE passages SET text='identical shared passage' WHERE id=7")
    response = client.get(
        "/api/v1/library/fetch",
        params={"source_id": "shared", "work_id": 1, "page": 5},
        headers={"X-Staff-Run-ID": "run-fed-ok"},
    )

    assert response.status_code == 200
    from library.retrieval_ledger import references_for_run

    rows = references_for_run(library_routes._db(), "run-fed-ok")
    assert len(rows) == 1
    assert rows[0]["source_id"] == "shared" and rows[0]["work_id"] == 1
    assert rows[0]["passage_id"] == 7 and rows[0]["file_sha256"] == "b" * 64
    assert rows[0]["node"] == response.json()["node"]
