"""TDD for GET /api/staff/activity/summary (#2353): bounded aggregate probe."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from routers.staff import router as staff_router

TEST_PRINCIPAL = Principal(id="test-user", type="human", name="Test User", roles=["admin"], scopes=["staff.read"])


def _database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE runs (id TEXT, status TEXT);CREATE TABLE messages (id TEXT, author_kind TEXT, delivery TEXT);"
    )
    return connection


@pytest.fixture(name="activity_db")
def activity_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db))
    return db


@pytest.fixture(name="client")
def client(activity_db: Path) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(staff_router)
    app.dependency_overrides[require_principal] = lambda: TEST_PRINCIPAL
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.mark.unit
def test_activity_summary_counts_without_listing_runs(client: TestClient, activity_db: Path) -> None:
    connection = _database(activity_db)
    connection.executemany(
        "INSERT INTO runs VALUES (?, ?)",
        [("r1", "running"), ("r2", "queued"), ("r3", "preparing"), ("r4", "succeeded"), ("r5", "failed")],
    )
    connection.executemany(
        "INSERT INTO messages VALUES (?, ?, ?)",
        [("m1", "role", "pending"), ("m2", "user", "pending"), ("m3", "role", "complete")],
    )
    connection.commit()
    connection.close()

    resp = client.get("/api/staff/activity/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] == 3
    assert body["pending_chat"] == 1
    assert isinstance(body["generated_at"], str) and body["generated_at"]


@pytest.mark.unit
def test_activity_summary_503_on_locked_or_missing_db(client: TestClient, activity_db: Path) -> None:
    resp = client.get("/api/staff/activity/summary")
    assert resp.status_code == 503

    activity_db.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(activity_db).close()
    resp = client.get("/api/staff/activity/summary")
    assert resp.status_code == 503


@pytest.mark.unit
def test_activity_summary_does_not_initialise_runner_or_providers(
    client: TestClient, activity_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import routers.staff as staff_module

    def _boom() -> None:
        raise AssertionError("activity summary must not initialise the runner")

    monkeypatch.setattr(staff_module, "get_runner", _boom)
    _database(activity_db).close()

    resp = client.get("/api/staff/activity/summary")

    assert resp.status_code == 200
