from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from routers import library as library_routes

READER = Principal(id="r", type="user", name="Reader", roles=["viewer"], scopes=["fleet.read"])
WRITER = Principal(id="w", type="agent", name="Writer", roles=["agent"], scopes=["fleet.write"])
_XHR = {"X-Requested-With": "XMLHttpRequest"}
_BODY = {
    "doi": "10.5555/abc",
    "title": "Reference Work",
    "authors": ["Ada Lovelace"],
    "reason": "needed for simulation",
    "requesting_ref": "UpstreamDrift#9",
}


def test_submissions_api(tmp_path: Path) -> None:
    db = tmp_path / "library.sqlite"
    library_routes.set_db_path(db)
    app = FastAPI()
    app.include_router(library_routes.router)
    app.dependency_overrides[require_scope("fleet.write")] = lambda: WRITER
    app.dependency_overrides[require_scope("fleet.read")] = lambda: READER
    client = TestClient(app)
    post = client.post("/api/v1/library/submissions", json=_BODY, headers=_XHR)
    assert post.status_code == 200 and post.json()["status"] == "pending"
    listed = client.get("/api/v1/library/submissions", headers=_XHR)
    assert listed.status_code == 200 and len(listed.json()["submissions"]) == 1
