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


def _app(tmp_path: Path) -> FastAPI:
    root = tmp_path / "src"
    root.mkdir()
    (root / "Jones - Swing Science.pdf").write_bytes(make_text_pdf("hip joint torque golf swing"))
    db = tmp_path / "library.sqlite"
    ingest_directory(root, db_path=db)
    library_routes.set_db_path(db)
    app = FastAPI()
    app.include_router(library_routes.router)
    app.dependency_overrides[require_scope("fleet.read")] = lambda: READER
    return app


def test_library_search(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    resp = client.get("/api/v1/library/search", params={"q": "hip joint torque golf", "budget": 1500}, headers=_XHR)
    assert resp.status_code == 200
    body = resp.json()
    assert body["tokens_used"] <= 1500 and body["passages"][0]["file_path"]
    assert client.get("/api/v1/library/search", params={"q": ""}, headers=_XHR).status_code == 422
    assert client.get("/api/v1/library/search", params={"q": "golf", "budget": 9000}, headers=_XHR).status_code == 422
