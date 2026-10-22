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
    (root / "Smith - Stats Book.pdf").write_bytes(make_text_pdf("stats body golf torque"))
    db = tmp_path / "library.sqlite"
    ingest_directory(root, db_path=db)
    library_routes.set_db_path(db)
    app = FastAPI()
    app.include_router(library_routes.router)
    app.dependency_overrides[require_scope("fleet.read")] = lambda: READER
    return app


def test_library_stats(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    resp = client.get("/api/v1/library/stats", headers=_XHR)
    assert resp.status_code == 200
    body = resp.json()
    assert body["works_active"] == 1
    assert body["files_present"] == 1
    assert body["passages"] >= 1
    assert body["last_ingest"]["files_indexed"] == 1
