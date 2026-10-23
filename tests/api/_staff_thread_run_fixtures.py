"""Shared fixtures for staff run and conversation thread API tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from routers.staff import router as staff_router
from routers.staff_local_runs import router as staff_local_runs_router
from routers.staff_threads import router as staff_threads_router
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.store import RunStore, get_store, reset_store
from staff.thread_bus import reset_thread_bus

TEST_PRINCIPAL = Principal(
    id="test-user",
    type="human",
    name="Test User",
    roles=["admin"],
    scopes=["staff.read", "staff.dispatch", "staff.cancel", "staff.write"],
)


@pytest.fixture(name="run_store")
def run_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[RunStore]:
    db = tmp_path / "test_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db))
    reset_store()
    store = get_store()
    yield store
    reset_store()


@pytest.fixture(name="conv_store")
def conv_store(tmp_path: Path) -> Any:
    reset_conversation_store()
    reset_thread_bus()
    return get_conversation_store(tmp_path / "test_conv.sqlite3")


@pytest.fixture(name="client")
def client(run_store: RunStore, conv_store: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(staff_router)
    app.include_router(staff_local_runs_router)
    app.include_router(staff_threads_router, prefix="/api/v1/staff")
    app.dependency_overrides[require_principal] = lambda: TEST_PRINCIPAL
    for scope in ("staff.read", "staff.dispatch", "staff.cancel", "staff.write"):
        app.dependency_overrides[require_scope(scope)] = lambda: TEST_PRINCIPAL
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    reset_store()
    reset_conversation_store()
    reset_thread_bus()
