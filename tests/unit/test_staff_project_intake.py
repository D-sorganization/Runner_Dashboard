"""Project intake store and API (#2157 slice 5)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from identity import Principal, require_principal, require_scope  # noqa: E402
from server import app  # noqa: E402
from staff.project_intake import ProjectIntakeStore, decompose_project, get_project_store  # noqa: E402

_WRITE = Principal(id="op", type="human", name="Op", roles=["operator"], scopes=["staff.read", "staff.write"])


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "projects.sqlite3"
    monkeypatch.setenv("STAFF_PROJECTS_DB", str(path))
    monkeypatch.setattr("staff.project_intake._store", None)
    return path


@pytest.fixture(autouse=True)
def api_auth() -> Any:
    app.dependency_overrides[require_principal] = lambda: _WRITE
    app.dependency_overrides[require_scope("staff.read")] = lambda: _WRITE
    app.dependency_overrides[require_scope("staff.write")] = lambda: _WRITE
    yield
    app.dependency_overrides.clear()


def test_create_and_decompose(project_db: Path) -> None:
    store = ProjectIntakeStore(project_db)
    rec = store.create(title="Fleet epic", goal="Ship skill registry", repos=["Runner_Dashboard"], created_by="op")
    tasks = decompose_project(rec)
    assert len(tasks) == 3
    updated = store.save_tasks(rec.id, tasks)
    loaded = store.get(rec.id)
    assert loaded is not None
    assert len(loaded.tasks) == 3


def test_projects_api(project_db: Path) -> None:
    client = TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"}, raise_server_exceptions=False)
    created = client.post(
        "/api/v1/staff/projects",
        json={"title": "T", "goal": "G", "repos": ["Runner_Dashboard"]},
    )
    assert created.status_code == 200
    pid = created.json()["id"]
    decomposed = client.post(f"/api/v1/staff/projects/{pid}/decompose")
    assert decomposed.status_code == 200
    assert len(decomposed.json()["tasks"]) == 3
    listed = client.get("/api/v1/staff/projects")
    assert listed.json()["count"] >= 1
