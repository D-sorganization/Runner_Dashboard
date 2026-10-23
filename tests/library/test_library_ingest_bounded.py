from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from library.ingest import ingest_directory
from library.sources import add_source


def test_bounded_ingest_keeps_full_root_and_skips_removal_reconciliation(tmp_path: Path) -> None:
    source_root = tmp_path / "x9-library"
    source_root.mkdir()
    for name in ("one.txt", "two.txt", "three.txt"):
        (source_root / name).write_text(f"Distinct searchable text for {name}.", encoding="utf-8")
    db = tmp_path / "data" / "library.sqlite"
    source_id = "desk-x9-library"
    add_source(db, source_id=source_id, label="X9", root_path=source_root)

    first = ingest_directory(source_root, db_path=db, source_id=source_id, max_files=1)
    assert first["files_seen"] == 1
    assert first["files_indexed"] == 1
    assert first["files_removed"] == 0
    assert first["scan_complete"] is False

    with sqlite3.connect(db) as conn:
        indexed_rel = conn.execute(
            "SELECT rel_path FROM files WHERE source_id=? AND status='present'", (source_id,)
        ).fetchone()[0]
        stored_root = conn.execute("SELECT source_root FROM files WHERE source_id=?", (source_id,)).fetchone()[0]
        run_root, note = conn.execute("SELECT source_root, notes FROM ingest_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert stored_root == source_root.resolve().as_posix()
    assert run_root == source_root.resolve().as_posix()
    assert "bounded" in note

    (source_root / indexed_rel).unlink()
    second = ingest_directory(source_root, db_path=db, source_id=source_id, max_files=1)
    assert second["files_removed"] == 0
    with sqlite3.connect(db) as conn:
        status = conn.execute(
            "SELECT status FROM files WHERE source_id=? AND rel_path=?", (source_id, indexed_rel)
        ).fetchone()[0]
        work_status = conn.execute(
            "SELECT status FROM works WHERE id=(SELECT work_id FROM files WHERE source_id=? AND rel_path=?)",
            (source_id, indexed_rel),
        ).fetchone()[0]
    assert status == "present"
    assert work_status == "active"

    complete = ingest_directory(source_root, db_path=db, source_id=source_id)
    assert complete["scan_complete"] is True
    assert complete["files_removed"] == 1
    with sqlite3.connect(db) as conn:
        status = conn.execute(
            "SELECT status FROM files WHERE source_id=? AND rel_path=?", (source_id, indexed_rel)
        ).fetchone()[0]
    assert status == "removed"


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_bounded_ingest_rejects_non_positive_integer_limits(tmp_path: Path, limit: object) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    with pytest.raises(ValueError, match="positive integer"):
        ingest_directory(source_root, db_path=tmp_path / "library.sqlite", max_files=limit)  # type: ignore[arg-type]


def test_cli_bounded_ingest_requires_registered_source_and_uses_its_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source_root = tmp_path / "mounted" / "Library"
    source_root.mkdir(parents=True)
    (source_root / "one.txt").write_text("A searchable local X9 test item.", encoding="utf-8")
    db = tmp_path / "data" / "library.sqlite"
    add_source(db, source_id="desk-x9-library", label="X9", root_path=source_root)
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(db.parent))
    monkeypatch.setattr(sys, "argv", ["library", "ingest", "--source-id", "desk-x9-library", "--max-files", "1"])

    from library.__main__ import main

    assert main() == 0
    assert "indexed 1 file(s)" in capsys.readouterr().out
    with sqlite3.connect(db) as conn:
        root, source_id = conn.execute("SELECT source_root, source_id FROM files").fetchone()
    assert root == source_root.resolve().as_posix()
    assert source_id == "desk-x9-library"


def test_cli_bounded_ingest_requires_source_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["library", "ingest", "--max-files", "1"])
    from library.__main__ import main

    assert main() == 2
    assert "--max-files requires --source-id" in capsys.readouterr().err


def test_cli_help_explains_bounded_scan_safety(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["library", "ingest", "--help"])
    from library.__main__ import main

    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "registered source" in help_text
    assert "never reconcile removals" in help_text
