from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from routers import library as routes

_XHR = {"X-Requested-With": "XMLHttpRequest"}


def _client(db: Path, principal: Principal) -> TestClient:
    routes.set_db_path(db)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(app)


def test_source_write_requires_fleet_control(tmp_path: Path) -> None:
    for role in ("bot", "fleet-peer"):
        principal = Principal(id=role, type="bot", name=role, roles=[role], scopes=[])
        response = _client(tmp_path / f"{role}.sqlite", principal).post(
            "/api/v1/library/sources", json={"id": "docs", "label": "Docs", "root_path": str(tmp_path)}, headers=_XHR
        )
        assert response.status_code == 403


def test_source_api_register_update_and_unregister(tmp_path: Path) -> None:
    root = tmp_path / "source"; root.mkdir()
    principal = Principal(id="operator", type="user", name="Operator", roles=["operator"], scopes=["fleet.control", "fleet.read"])
    client = _client(tmp_path / "library.sqlite", principal)
    post = client.post("/api/v1/library/sources", json={"id": "docs", "label": "Docs", "root_path": str(root)}, headers=_XHR)
    assert post.status_code == 200
    assert client.patch("/api/v1/library/sources/docs", json={"shared": True}, headers=_XHR).json()["shared"]
    assert client.get("/api/v1/library/sources", headers=_XHR).json()["sources"][0]["id"] == "docs"
    assert client.delete("/api/v1/library/sources/docs", headers=_XHR).status_code == 200


def test_source_api_rejects_overlapping_roots_with_422(tmp_path: Path) -> None:
    root = tmp_path / "source"; root.mkdir()
    child = root / "child"; child.mkdir()
    principal = Principal(id="operator", type="user", name="Operator", roles=["operator"], scopes=["fleet.control", "fleet.read"])
    client = _client(tmp_path / "library.sqlite", principal)
    assert client.post("/api/v1/library/sources", json={"id": "one", "label": "One", "root_path": str(root)}, headers=_XHR).status_code == 200
    response = client.post("/api/v1/library/sources", json={"id": "two", "label": "Two", "root_path": str(child)}, headers=_XHR)
    assert response.status_code == 422
