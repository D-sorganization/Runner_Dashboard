from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from library.db import init_library_db
from library.retrieval_ledger import authorize_run_owner, record_retrievals, references_for_run

REF = {
    "node": "desk",
    "source_id": "shared",
    "work_id": 4,
    "passage_id": 19,
    "file_sha256": "a" * 64,
    "page_start": 7,
    "page_end": 8,
}


def test_retrieval_ledger_records_canonical_tuple_once(tmp_path: Path) -> None:
    db = init_library_db(tmp_path / "library.sqlite")

    recorded = record_retrievals(db, run_id="run-1", references=[REF, REF])

    assert recorded == 1
    rows = references_for_run(db, "run-1")
    assert rows == [
        {
            **REF,
            "run_id": "run-1",
            "citation": "[ref:desk:shared:w4:p19#sha256=aaaaaaaa:p.7-8]",
            "valid": False,
            "reason": "retrieved",
            "evidence": "{}",
            "verified_at": None,
            "retrieved_at": rows[0]["retrieved_at"],
        }
    ]


@pytest.mark.parametrize(
    "reference",
    [
        {**REF, "file_sha256": "short"},
        {**REF, "node": "https://attacker.invalid"},
        {**REF, "source_id": "../private"},
        {**REF, "page_start": 0},
        {**REF, "page_end": 6},
        {**REF, "work_id": True},
    ],
)
def test_retrieval_ledger_rejects_noncanonical_identity(tmp_path: Path, reference: dict[str, object]) -> None:
    db = init_library_db(tmp_path / "library.sqlite")

    with pytest.raises(ValueError):
        record_retrievals(db, run_id="run-1", references=[reference])

    assert references_for_run(db, "run-1") == []


def test_retrieval_ledger_bounds_input_and_run_id(tmp_path: Path) -> None:
    db = init_library_db(tmp_path / "library.sqlite")

    with pytest.raises(ValueError):
        record_retrievals(db, run_id="../run", references=[REF])
    with pytest.raises(ValueError):
        record_retrievals(db, run_id="run-1", references=[REF] * 101)
    with pytest.raises(ValueError):
        record_retrievals(db, run_id="run-1", references=[{**REF, "text": "do not persist"}])

    with sqlite3.connect(db) as conn:
        assert (
            conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='staff_run_retrievals'").fetchone()
            is None
        )


def test_retrieval_ledger_requires_existing_run_and_matching_requester() -> None:
    run = SimpleNamespace(requested_by="principal:researcher")
    store = SimpleNamespace(get_run=lambda run_id: run if run_id == "run-1" else None)
    assert authorize_run_owner(store, run_id="run-1", principal_id="researcher") is None
    owner_store = SimpleNamespace(get_run=lambda _run_id: SimpleNamespace(requested_by="barb"))
    assert authorize_run_owner(owner_store, run_id="run-1", principal_id="barb") is None
    with pytest.raises(LookupError):
        authorize_run_owner(store, run_id="unknown", principal_id="researcher")
    with pytest.raises(PermissionError):
        authorize_run_owner(store, run_id="run-1", principal_id="different-user")


def test_retrieval_ledger_caps_total_rows_per_run(tmp_path: Path) -> None:
    db = init_library_db(tmp_path / "library.sqlite")
    for start in range(0, 500, 100):
        batch = [{**REF, "passage_id": start + offset + 1} for offset in range(100)]
        assert record_retrievals(db, run_id="run-1", references=batch) == 100

    with pytest.raises(ValueError, match="exceeds 500"):
        record_retrievals(db, run_id="run-1", references=[{**REF, "passage_id": 501}])
    assert len(references_for_run(db, "run-1")) == 500
