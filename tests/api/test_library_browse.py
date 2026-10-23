"""Tests for node-served library folder browser GET /api/v1/library/browse (#2227)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from routers import library as routes

_XHR = {"X-Requested-With": "XMLHttpRequest"}


def _client(db: Path, scopes: list[str]) -> TestClient:
    routes.set_db_path(db)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_principal] = lambda: Principal(id="u", type="u", name="U", roles=[], scopes=scopes)
    return TestClient(app)


def test_browse_scopes_dirs_traversal_and_denied(tmp_path: Path, monkeypatch: Any) -> None:
    from library import browse

    root = tmp_path / "root"
    root.mkdir()
    (root / "docs" / "sub").mkdir(parents=True)
    (root / "docs" / "f.txt").write_text("hi")
    (root / "empty").mkdir()
    (root / "ignore.md").write_text("x")
    (root / ".ssh").mkdir()
    (root / "data").mkdir()
    monkeypatch.setattr(browse, "allowed_browse_roots", lambda: [root])
    monkeypatch.setattr(browse, "library_data_dir", lambda: root / "data")

    assert (
        _client(tmp_path / "lib.sqlite", ["fleet.read"]).get("/api/v1/library/browse", headers=_XHR).status_code == 403
    )
    client = _client(tmp_path / "lib.sqlite", ["fleet.control"])
    res = client.get(f"/api/v1/library/browse?path={root}", headers=_XHR)
    assert res.status_code == 200
    names = {e["name"]: e for e in res.json()["entries"]}
    assert "docs" in names and "empty" in names and "ignore.md" not in names
    assert names["docs"]["child_dirs"] == 1 and names["docs"]["file_count"] == 1
    assert client.get(f"/api/v1/library/browse?path={root}/../../..", headers=_XHR).status_code in (403, 422)
    for name in (".ssh", "data"):
        assert client.get(f"/api/v1/library/browse?path={root / name}", headers=_XHR).status_code == 403
