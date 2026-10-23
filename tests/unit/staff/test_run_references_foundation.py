"""Typed, owner-authorized run reference ledger service tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from library.codebase_retrieval_ledger import record_codebase_references
from library.db import init_library_db, library_db_path
from library.retrieval_ledger import record_retrievals
from staff import run_references
from staff.store import RunRecord, RunStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RunStore:
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(tmp_path / "library"))
    return RunStore(tmp_path / "runs.sqlite3")


def add_run(store: RunStore, run_id: str, owner: str = "reader") -> None:
    store.create_run(
        RunRecord(
            id=run_id,
            role="cartographer",
            provider="claude",
            model="sonnet",
            machine="DeskComputer",
            repo="Runner_Dashboard",
            target_kind="prompt",
            target_ref="",
            prompt="summarize contracts",
            status="succeeded",
            requested_by=owner,
        )
    )


def add_refs(run_id: str) -> None:
    path = init_library_db(library_db_path())
    record_retrievals(
        path,
        run_id=run_id,
        references=[
            {
                "node": "DeskComputer",
                "source_id": "drive",
                "work_id": 1,
                "passage_id": 11,
                "file_sha256": "1" * 64,
                "page_start": 2,
                "page_end": 3,
            }
        ],
    )
    commit = "a" * 40
    record_codebase_references(
        path,
        run_id=run_id,
        references=[
            {
                "node": "DeskComputer",
                "repo": "Runner_Dashboard",
                "commit_sha": commit,
                "relative_path": "backend/contracts.py",
                "line_start": 4,
                "line_end": 8,
                "content_hash": "b" * 64,
                "citation": f"[ref:DeskComputer:codebase:Runner_Dashboard@{commit[:8]}:backend/contracts.py#L4-L8]",
            }
        ],
    )


@pytest.mark.unit
def test_service_returns_stable_whitelisted_mixed_identities(store: RunStore) -> None:
    add_run(store, "mixed")
    add_refs("mixed")
    first = run_references.load_run_references(store, run_id="mixed", principal_id="reader")
    second = run_references.load_run_references(store, run_id="mixed", principal_id="reader")
    assert first == second
    assert [ref.kind for ref in first.references] == ["library", "codebase"]
    assert "evidence" not in first.model_dump_json().lower()
    assert "passage text" not in first.model_dump_json().lower()
    assert set(type(first.references[0]).model_fields) == {
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


@pytest.mark.unit
def test_empty_run_is_isolated_from_another_runs_library_and_codebase_refs(store: RunStore) -> None:
    add_run(store, "empty")
    add_run(store, "other")
    add_refs("other")
    result = run_references.load_run_references(store, run_id="empty", principal_id="reader")
    assert result.references == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("run_id", "owner", "principal", "error"),
    [
        ("absent", "reader", "reader", LookupError),
        ("private", "someone", "reader", PermissionError),
    ],
)
def test_unauthorized_requests_stop_before_database_initialization(
    store: RunStore, monkeypatch: pytest.MonkeyPatch, run_id: str, owner: str, principal: str, error: type[Exception]
) -> None:
    if run_id != "absent":
        add_run(store, run_id, owner)
    monkeypatch.setattr(run_references, "init_library_db", lambda _path: pytest.fail("database initialized early"))
    with pytest.raises(error):
        run_references.load_run_references(store, run_id=run_id, principal_id=principal)
