from __future__ import annotations

from pathlib import Path

import pytest
from library.codebase_retrieval_ledger import codebase_references_for_run, record_codebase_references
from library.db import init_library_db
from library.retrieval_ledger import record_retrievals, references_for_run


def _reference(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "node": "DeskComputer",
        "repo": "Runner_Dashboard",
        "commit_sha": "1234567890abcdef1234567890abcdef12345678",
        "relative_path": "backend/staff/review.py",
        "line_start": 10,
        "line_end": 16,
        "content_hash": "a" * 64,
        "citation": ("[ref:DeskComputer:codebase:Runner_Dashboard@12345678:backend/staff/review.py#L10-L16]"),
    }
    fields.update(overrides)
    return fields


def test_codebase_run_references_are_append_only_idempotent_and_readable(tmp_path: Path) -> None:
    db = init_library_db(tmp_path / "library.sqlite")
    reference = _reference()

    assert record_codebase_references(db, run_id="run-1", references=[reference, reference]) == 1
    assert record_codebase_references(db, run_id="run-1", references=[reference]) == 0
    assert codebase_references_for_run(db, "run-1") == [
        {
            "run_id": "run-1",
            "node": "DeskComputer",
            "repo": "Runner_Dashboard",
            "commit_sha": "1234567890abcdef1234567890abcdef12345678",
            "relative_path": "backend/staff/review.py",
            "line_start": 10,
            "line_end": 16,
            "content_hash": "a" * 64,
            "citation": reference["citation"],
        }
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"node": "../node"},
        {"repo": "../repo"},
        {"commit_sha": "not-a-commit"},
        {"relative_path": "../secret.txt"},
        {"relative_path": "/absolute/path"},
        {"relative_path": "C:/absolute/path"},
        {"relative_path": "docs/line\nbreak.md"},
        {"relative_path": "folder\\file.py"},
        {"line_start": 0},
        {"line_end": 9},
        {"content_hash": "short"},
        {"citation": "[ref:other:codebase:Runner_Dashboard@12345678:backend/staff/review.py#L10-L16]"},
    ],
)
def test_codebase_run_reference_rejects_invalid_identity(tmp_path: Path, overrides: dict[str, object]) -> None:
    db = init_library_db(tmp_path / "library.sqlite")
    with pytest.raises(ValueError):
        record_codebase_references(db, run_id="run-1", references=[_reference(**overrides)])
    assert codebase_references_for_run(db, "run-1") == []


def test_codebase_ledger_preserves_existing_library_retrievals(tmp_path: Path) -> None:
    db = init_library_db(tmp_path / "library.sqlite")
    library_reference = {
        "node": "DeskComputer",
        "source_id": "shared",
        "work_id": 1,
        "passage_id": 2,
        "file_sha256": "b" * 64,
        "page_start": 3,
        "page_end": 4,
    }
    record_retrievals(db, run_id="run-1", references=[library_reference])
    record_codebase_references(db, run_id="run-1", references=[_reference()])

    assert len(references_for_run(db, "run-1")) == 1
    assert len(codebase_references_for_run(db, "run-1")) == 1


def test_codebase_run_reference_limits_are_enforced(tmp_path: Path) -> None:
    db = init_library_db(tmp_path / "library.sqlite")

    def references(start: int, count: int) -> list[dict[str, object]]:
        values = []
        for index in range(start, start + count):
            path = f"docs/item-{index}.md"
            citation = f"[ref:DeskComputer:codebase:Runner_Dashboard@12345678:{path}#L{index + 1}-L{index + 1}]"
            values.append(
                _reference(
                    relative_path=path,
                    line_start=index + 1,
                    line_end=index + 1,
                    citation=citation,
                )
            )
        return values

    with pytest.raises(ValueError, match="at most 100"):
        record_codebase_references(db, run_id="run-1", references=references(0, 101))

    for page in range(5):
        assert record_codebase_references(db, run_id="run-1", references=references(page * 100, 100)) == 100
    with pytest.raises(ValueError, match="exceeds 500"):
        record_codebase_references(db, run_id="run-1", references=references(500, 1))
    assert len(codebase_references_for_run(db, "run-1")) == 500
