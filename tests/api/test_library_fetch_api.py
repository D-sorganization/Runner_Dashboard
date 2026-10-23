from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from library.db import init_library_db
from library.sources import add_source
from routers import library as library_routes

READER = Principal(id="r", type="user", name="Reader", roles=["viewer"], scopes=["fleet.read"])


def _app(tmp_path: Path) -> tuple[FastAPI, str]:
    root = tmp_path / "shared"
    root.mkdir()
    db = init_library_db(tmp_path / "library.sqlite")
    add_source(db, source_id="shared", label="Shared", root_path=root, shared=True)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO works(id,sha256,title,format,status) VALUES(1,?,?,'txt','active')",
            ("a" * 64, "A book"),
        )
        conn.execute(
            "INSERT INTO files(work_id,source_id,rel_path,mtime,size,sha256,status,source_root) "
            "VALUES(1,'shared','book.txt',0,12,?,'present',?)",
            ("b" * 64, str(root)),
        )
        conn.execute(
            "INSERT INTO passages(id,work_id,page_start,page_end,text,token_estimate) VALUES(7,1,4,5,?,3)",
            ("Bounded passage text",),
        )
        conn.commit()
    library_routes.set_db_path(db)
    app = FastAPI()
    app.include_router(library_routes.router)
    app.dependency_overrides[require_scope("fleet.read")] = lambda: READER
    return app, "[ref:local:shared:w1:p7#sha256=bbbbbbbb:p.4-5]"


def test_fetch_resolves_canonical_reference_without_exposing_paths(tmp_path: Path) -> None:
    app, cite = _app(tmp_path)
    response = TestClient(app).get("/api/v1/library/fetch", params={"cite": cite})
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "Bounded passage text"
    assert body["citation"] == "Unknown, A book, p. 4-5"
    assert body["cite_key"].startswith("[ref:") and body["cite_key"].endswith(":p.4-5]")
    assert body["cite_uri"] == "lib://shared/w1/p7@bbbbbbbb#p4-5"
    assert body["file_sha256"] == "b" * 64
    assert "file_path" not in body and "book.txt" not in response.text


def test_fetch_rejects_hash_and_page_mismatch(tmp_path: Path) -> None:
    app, cite = _app(tmp_path)
    client = TestClient(app)
    bad_hash = cite.replace("bbbbbbbb", "cccccccc")
    bad_pages = cite.replace("p.4-5", "p.4-6")
    assert client.get("/api/v1/library/fetch", params={"cite": bad_hash}).status_code == 404
    assert client.get("/api/v1/library/fetch", params={"cite": bad_pages}).status_code == 404
    assert client.get("/api/v1/library/fetch", params={"cite": "http://attacker.invalid/a"}).status_code == 422


def test_fetch_hard_caps_utf8_bytes_for_unbroken_unicode(tmp_path: Path) -> None:
    app, cite = _app(tmp_path)
    for sample, expected_bytes in (("界" * 4000, 399), ("x" * 4000, 400)):
        with sqlite3.connect(library_routes._db()) as conn:
            conn.execute("UPDATE passages SET text=?, token_estimate=1 WHERE id=7", (sample,))
        response = TestClient(app).get("/api/v1/library/fetch", params={"cite": cite})
        assert len(response.json()["text"].encode("utf-8")) == expected_bytes


def test_peer_fetch_cannot_resolve_unshared_source(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    private_root = tmp_path / "private"
    private_root.mkdir()
    add_source(library_routes._db(), source_id="private", label="Private", root_path=private_root, shared=False)
    with sqlite3.connect(library_routes._db()) as conn:
        conn.execute(
            "INSERT INTO works(id,sha256,title,format,status) VALUES(2,?,'Private book','txt','active')", ("d" * 64,)
        )
        conn.execute(
            "INSERT INTO files(work_id,source_id,rel_path,mtime,size,sha256,status,source_root) "
            "VALUES(2,'private','secret.txt',0,6,?,'present',?)",
            ("e" * 64, str(private_root)),
        )
        conn.execute(
            "INSERT INTO passages(id,work_id,page_start,page_end,text,token_estimate) VALUES(8,2,1,1,'private text',2)"
        )
        conn.commit()
    peer = Principal(id="fleet-peer", type="bot", name="Peer", roles=["fleet-peer"])
    app.dependency_overrides[library_routes.require_library_peer] = lambda: peer
    cite = "[ref:local:private:w2:p8#sha256=eeeeeeee:p.1-1]"
    response = TestClient(app).get("/api/v1/library/fetch", params={"cite": cite, "scope": "local"})
    assert response.status_code == 404


def test_hub_fetch_only_uses_registered_node_url(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    import dashboard_config
    import httpx

    app, _ = _app(tmp_path)
    monkeypatch.setattr(dashboard_config, "HOSTNAME", "hub")
    monkeypatch.setattr(dashboard_config, "FLEET_NODES", {"peer-a": "https://peer-a.tailnet:8321"})
    monkeypatch.setenv("HUB_FLEET_TOKEN", "unit-test-value")
    seen_urls = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            seen_urls.append(url)
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "node": "peer-a",
                    "source_id": "shared",
                    "work_id": 1,
                    "passage_id": 7,
                    "page_start": 4,
                    "page_end": 5,
                    "file_sha256": "b" * 64,
                    "work_sha256": "a" * 64,
                    "text": "Bounded passage text",
                    "cite_key": "[ref:peer-a:shared:w1:p7#sha256=bbbbbbbb:p.4-5]",
                    "cite_uri": "lib://shared/w1/p7@bbbbbbbb#p4-5",
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    cite = "[ref:peer-a:shared:w1:p7#sha256=bbbbbbbb:p.4-5]"
    response = TestClient(app).get("/api/v1/library/fetch", params={"cite": cite})
    assert response.status_code == 200
    assert seen_urls == ["https://peer-a.tailnet:8321/api/v1/library/fetch"]


def test_fetch_rejects_ambiguous_sha_prefix(tmp_path: Path) -> None:
    app, cite = _app(tmp_path)
    with sqlite3.connect(library_routes._db()) as conn:
        conn.execute(
            "INSERT INTO files(work_id,source_id,rel_path,mtime,size,sha256,status,source_root) "
            "VALUES(1,'shared','duplicate.txt',0,12,?,'present',?)",
            ("b" * 8 + "c" * 56, str(tmp_path / "shared")),
        )
        conn.commit()
    response = TestClient(app).get("/api/v1/library/fetch", params={"cite": cite})
    assert response.status_code == 409
