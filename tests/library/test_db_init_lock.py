from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from library.db import init_library_db
from library.pool import PooledCatalog
from routers import library as library_routes


def test_repeated_init_is_read_only_while_another_writer_holds_lock(tmp_path) -> None:
    db_path = init_library_db(tmp_path / "library.sqlite")
    writer = sqlite3.connect(db_path, timeout=0.01)
    writer.execute("BEGIN EXCLUSIVE")
    try:
        assert init_library_db(db_path) == db_path
    finally:
        writer.rollback()
        writer.close()


def test_replaced_database_gets_initialized(tmp_path) -> None:
    db_path = init_library_db(tmp_path / "library.sqlite")
    old_path = db_path.with_suffix(".old")
    db_path.rename(old_path)
    db_path.touch()

    init_library_db(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='library_sources'").fetchone()


def test_sources_api_reads_during_ingest_writer(tmp_path, monkeypatch) -> None:
    db_path = init_library_db(tmp_path / "library.sqlite")
    monkeypatch.setattr(library_routes, "_db_override", db_path)
    catalog = PooledCatalog()
    monkeypatch.setattr(library_routes, "get_pooled_catalog", lambda: catalog)
    monkeypatch.setattr(library_routes, "refresh_catalog", AsyncMock(return_value=[]))
    app = FastAPI()
    app.include_router(library_routes.router)
    operator = Principal(id="operator", type="user", name="Operator", roles=["operator"])
    app.dependency_overrides[require_principal] = lambda: operator
    writer = sqlite3.connect(db_path, timeout=0.01)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(
        "INSERT INTO ingest_runs (started_at, source_root) VALUES (?, ?)",
        ("2026-10-23T00:00:00+00:00", "/tmp/source"),
    )
    try:
        response = TestClient(app).get("/api/v1/library/sources")
        assert response.status_code == 200
        assert response.json()["sources"] == []
    finally:
        writer.rollback()
        writer.close()
