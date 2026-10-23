from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from routers import library as routes

_XHR = {"X-Requested-With": "XMLHttpRequest"}


def test_operator_refreshes_catalog_but_peer_never_federates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from library.pool import PooledCatalog

    catalog = PooledCatalog()
    catalog.update_node_sources("peer", [{"id": "shared", "label": "Shared"}])
    refresh = AsyncMock(return_value=["offline"])
    monkeypatch.setattr(routes, "refresh_catalog", refresh)
    monkeypatch.setattr(routes, "get_pooled_catalog", lambda: catalog)
    principal = Principal(id="operator", type="user", name="Operator", roles=["operator"])
    client = _client(tmp_path / "catalog.sqlite", principal)
    result = client.get("/api/v1/library/sources").json()
    assert result["pooled_sources"][0]["node"] == "peer"
    assert result["unavailable_sources"] == ["offline"]
    refresh.assert_awaited_once()
    refresh.reset_mock()
    for peer in (False, True):
        if peer:
            client.app.dependency_overrides[require_principal] = lambda: Principal(
                id="fleet-peer", type="bot", name="Peer", roles=["fleet-peer"]
            )
        result = client.get("/api/v1/library/sources" + ("" if peer else "?shared=1")).json()
        assert result["pooled_sources"] == []
    refresh.assert_not_awaited()


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
    root = tmp_path / "source"
    root.mkdir()
    principal = Principal(
        id="operator", type="user", name="Operator", roles=["operator"], scopes=["fleet.control", "fleet.read"]
    )
    client = _client(tmp_path / "library.sqlite", principal)
    post = client.post(
        "/api/v1/library/sources",
        json={"id": "docs", "label": "Docs", "root_path": str(root)},
        headers=_XHR,
    )
    assert post.status_code == 200
    assert client.patch("/api/v1/library/sources/docs", json={"shared": True}, headers=_XHR).json()["shared"]
    sources_payload = client.get("/api/v1/library/sources", headers=_XHR).json()
    assert sources_payload["sources"][0]["id"] == "docs" and sources_payload["sources"][0]["file_count"] == 0
    assert {"is_hub", "current_node", "nodes"}.issubset(sources_payload)
    assert client.delete("/api/v1/library/sources/docs", headers=_XHR).status_code == 200


def test_source_api_rejects_overlapping_roots_with_422(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    child = root / "child"
    child.mkdir()
    principal = Principal(
        id="operator", type="user", name="Operator", roles=["operator"], scopes=["fleet.control", "fleet.read"]
    )
    client = _client(tmp_path / "library.sqlite", principal)
    resp_one = client.post(
        "/api/v1/library/sources",
        json={"id": "one", "label": "One", "root_path": str(root)},
        headers=_XHR,
    )
    assert resp_one.status_code == 200
    response = client.post(
        "/api/v1/library/sources",
        json={"id": "two", "label": "Two", "root_path": str(child)},
        headers=_XHR,
    )
    assert response.status_code == 422


def test_reindex_source_endpoint(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    principal = Principal(
        id="operator", type="user", name="Operator", roles=["operator"], scopes=["fleet.control", "fleet.read"]
    )
    client = _client(tmp_path / "library.sqlite", principal)
    client.post(
        "/api/v1/library/sources",
        json={"id": "reindex-me", "label": "Reindex Me", "root_path": str(root)},
        headers=_XHR,
    )
    res = client.post("/api/v1/library/sources/reindex-me/reindex", headers=_XHR)
    assert res.status_code == 200
    assert res.json().get("status") in ("queued", "completed", "ok")
    assert res.json().get("source_id") == "reindex-me"


def test_source_api_availability_recovers_without_reindexing_or_deletion(tmp_path: Path) -> None:
    from library.ingest import ingest_directory

    from tests.library.conftest import make_text_pdf

    root = tmp_path / "source"
    root.mkdir()
    item = root / "guide.pdf"
    item.write_bytes(make_text_pdf("Offline availability check keeps source and indexed content intact."))
    original = item.read_bytes()
    principal = Principal(
        id="operator", type="user", name="Operator", roles=["operator"], scopes=["fleet.control", "fleet.read"]
    )
    client = _client(tmp_path / "library.sqlite", principal)
    response = client.post(
        "/api/v1/library/sources",
        json={"id": "desk-drive-library", "label": "Drive mirror", "root_path": str(root)},
        headers=_XHR,
    )
    assert response.status_code == 200
    ingest_directory(root, db_path=tmp_path / "library.sqlite", source_id="desk-drive-library")

    def source_payload() -> dict[str, object]:
        sources = client.get("/api/v1/library/sources", headers=_XHR).json()["sources"]
        return next(source for source in sources if source["id"] == "desk-drive-library")

    assert source_payload()["availability"] == "available"
    before = client.get("/api/v1/library/stats", headers=_XHR).json()
    disconnected = tmp_path / "disconnected"
    root.rename(disconnected)
    assert source_payload()["availability"] == "unavailable"
    during = client.get("/api/v1/library/stats", headers=_XHR).json()
    assert during["files_present"] == before["files_present"] == 1
    assert (disconnected / "guide.pdf").read_bytes() == original

    disconnected.rename(root)
    assert source_payload()["availability"] == "available"
    after = client.get("/api/v1/library/stats", headers=_XHR).json()
    assert after["files_present"] == before["files_present"]


def test_google_drive_connector_api_lifecycle(tmp_path: Path) -> None:
    principal = Principal(
        id="operator", type="user", name="Operator", roles=["operator"], scopes=["fleet.control", "fleet.read"]
    )
    client = _client(tmp_path / "library.sqlite", principal)
    post = client.post(
        "/api/v1/library/sources",
        json={
            "id": "agent-drive",
            "label": "Agent Shared Folder",
            "backend_type": "google_drive_connector",
            "drive_folder_url": "https://drive.google.com/drive/folders/1ZyXwVuTsRqPoNmLkJiHgFeDcBa987654",
            "shared": True,
            "repos": ["Runner_Dashboard"],
        },
        headers=_XHR,
    )
    assert post.status_code == 200
    created = post.json()
    assert created["backend_type"] == "google_drive_connector"
    assert created["drive_folder_id"] == "1ZyXwVuTsRqPoNmLkJiHgFeDcBa987654"
    assert created["preferential"] is True
    assert created["priority"] == 1
    assert created["shared"] is True

    patched = client.patch(
        "/api/v1/library/sources/agent-drive",
        json={"preferential": False, "priority": 2, "repos": ["Runner_Dashboard", "AffineDrift"]},
        headers=_XHR,
    )
    assert patched.status_code == 200
    assert patched.json()["preferential"] is False
    assert patched.json()["priority"] == 2
    assert patched.json()["repos"] == ["Runner_Dashboard", "AffineDrift"]
