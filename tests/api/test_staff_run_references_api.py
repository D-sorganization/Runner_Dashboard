"""Owner-authorized, text-free run reference ledger API tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from library.codebase_retrieval_ledger import record_codebase_references
from library.db import init_library_db, library_db_path
from library.retrieval_ledger import record_retrievals
from routers import staff_references, staff_v1
from staff import run_references
from staff.store import RunRecord, RunStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[RunStore]:
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(tmp_path / "library"))
    value = RunStore(tmp_path / "runs.sqlite3")
    monkeypatch.setattr(staff_references, "get_run_store", lambda: value)
    yield value
    value.close()


def client(principal: str = "reader", scopes: list[str] | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(staff_v1.router)
    identity = Principal(
        id=principal, type="human", name=principal, roles=[], scopes=["staff.read"] if scopes is None else scopes
    )
    app.dependency_overrides[require_principal] = lambda: identity
    return TestClient(app, raise_server_exceptions=False)


def add_run(store: RunStore, run_id: str, owner: str = "reader") -> None:
    store.create_run(
        RunRecord(
            id=run_id,
            role="cartographer",
            provider="claude",
            model="sonnet",
            machine="hub-a",
            repo="Runner_Dashboard",
            target_kind="prompt",
            target_ref="",
            prompt="summarize contracts",
            status="succeeded",
            requested_by=owner,
        )
    )


def add_library(run_id: str) -> None:
    path = init_library_db(library_db_path())
    record_retrievals(
        path,
        run_id=run_id,
        references=[
            {
                "node": "hub-a",
                "source_id": source,
                "work_id": wid,
                "passage_id": wid + 10,
                "file_sha256": f"{wid:064x}",
                "page_start": 2,
                "page_end": 3,
            }
            for source, wid in [("desk-x9-library", 2), ("drive", 1)]
        ],
    )


def add_codebase(run_id: str) -> None:
    path, commit = init_library_db(library_db_path()), "a" * 40
    record_codebase_references(
        path,
        run_id=run_id,
        references=[
            {
                "node": "hub-a",
                "repo": "Runner_Dashboard",
                "commit_sha": commit,
                "relative_path": "backend/contracts.py",
                "line_start": 4,
                "line_end": 8,
                "content_hash": "b" * 64,
                "citation": f"[ref:hub-a:codebase:Runner_Dashboard@{commit[:8]}:backend/contracts.py#L4-L8]",
            }
        ],
    )


@pytest.mark.unit
def test_mixed_references_are_stable_whitelisted_and_text_free(store: RunStore) -> None:
    add_run(store, "mixed")
    add_library("mixed")
    add_codebase("mixed")
    api = client()
    first, second = api.get("/api/v1/staff/runs/mixed/references"), api.get("/api/v1/staff/runs/mixed/references")
    assert first.status_code == 200 and first.json() == second.json()
    body = first.json()
    assert body["run_id"] == "mixed"
    assert [r["kind"] for r in body["references"]] == ["library", "library", "codebase"]
    assert [r["source_id"] for r in body["references"][:2]] == ["desk-x9-library", "drive"]
    assert body["references"][-1]["repo"] == "Runner_Dashboard"
    assert set(body["references"][0]) == {
        "kind",
        "node",
        "source_id",
        "work_id",
        "passage_id",
        "file_sha256",
        "page_start",
        "page_end",
        "citation",
    }
    assert set(body["references"][-1]) == {
        "kind",
        "node",
        "repo",
        "commit_sha",
        "relative_path",
        "line_start",
        "line_end",
        "content_hash",
        "citation",
    }
    assert "evidence" not in first.text.lower() and "text" not in first.text.lower()


@pytest.mark.unit
def test_empty_run_does_not_leak_other_run_references(store: RunStore) -> None:
    add_run(store, "empty")
    add_run(store, "other")
    add_library("other")
    add_codebase("other")
    response = client().get("/api/v1/staff/runs/empty/references")
    assert response.status_code == 200 and response.json() == {"run_id": "empty", "references": []}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("run_id", "owner", "principal", "expected"),
    [
        ("absent", "reader", "reader", 404),
        ("private", "someone", "reader", 403),
    ],
)
def test_authorization_precedes_database_initialization(
    store: RunStore, monkeypatch: pytest.MonkeyPatch, run_id: str, owner: str, principal: str, expected: int
) -> None:
    if run_id != "absent":
        add_run(store, run_id, owner)
    monkeypatch.setattr(run_references, "init_library_db", lambda _path: pytest.fail("unauthorized database init"))
    assert client(principal).get(f"/api/v1/staff/runs/{run_id}/references").status_code == expected


@pytest.mark.unit
def test_reference_endpoint_requires_staff_read(store: RunStore) -> None:
    add_run(store, "scoped")
    assert client(scopes=[]).get("/api/v1/staff/runs/scoped/references").status_code == 403
