from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from library.ingest import ingest_directory
from routers import library as library_routes

from tests.library.conftest import make_text_pdf

READER = Principal(id="r", type="user", name="Reader", roles=["viewer"], scopes=["fleet.read"])
_XHR = {"X-Requested-With": "XMLHttpRequest"}


def _app(tmp_path: Path, monkeypatch) -> FastAPI:
    cfg = tmp_path / "collections.yml"
    cfg.write_text("repos: {}\ntopics:\n  Golf:\n    folders: [Golf]\n")
    monkeypatch.setenv("LIBRARY_COLLECTIONS_PATH", str(cfg))
    root = tmp_path / "src"
    (root / "Golf").mkdir(parents=True)
    (root / "Golf" / "Jones - Swing Science.pdf").write_bytes(make_text_pdf("hip joint torque golf swing"))
    db = tmp_path / "library.sqlite"
    ingest_directory(root, db_path=db)
    library_routes.set_db_path(db)
    app = FastAPI()
    app.include_router(library_routes.router)
    app.dependency_overrides[require_scope("fleet.read")] = lambda: READER
    return app


def test_library_search(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(_app(tmp_path, monkeypatch))
    # Operator-local search can read the default private ingest source.
    resp = client.get(
        "/api/v1/library/search", params={"q": "hip joint torque golf", "budget": 1500, "scope": "local"}, headers=_XHR
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tokens_used"] <= 1500 and body["passages"][0]["file_path"]
    assert client.get("/api/v1/library/stats", headers=_XHR).status_code == 200
    wid = body["passages"][0]["work_id"]
    assert client.get(f"/api/v1/library/works/{wid}", headers=_XHR).json()["topics"] == ["Golf"]
    assert client.get("/api/v1/library/search", params={"q": "", "topic": "Golf"}, headers=_XHR).status_code == 422
    assert client.get("/api/v1/library/search", params={"q": "golf", "topic": "nope"}, headers=_XHR).status_code == 422
