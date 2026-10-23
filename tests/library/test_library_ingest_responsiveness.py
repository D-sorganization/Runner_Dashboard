from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path

import library.ingest as ingest
from library.db import init_library_db
from library.sources import add_source


def test_ingest_enumerates_before_opening_sqlite_write_transaction(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    database = tmp_path / "library.sqlite"
    init_library_db(database)
    add_source(database, source_id="source", label="Source", root_path=source_root)
    scanning = threading.Event()
    continue_scan = threading.Event()

    def slow_paths(*_args):
        scanning.set()
        assert continue_scan.wait(timeout=5)
        yield from ()

    monkeypatch.setattr(ingest, "_eligible_paths", slow_paths)
    result: list[object] = []
    worker = threading.Thread(
        target=lambda: result.append(ingest.ingest_directory(source_root, db_path=database, source_id="source"))
    )
    worker.start()
    try:
        assert scanning.wait(timeout=2)
        with sqlite3.connect(database, timeout=0.2) as conn:
            conn.execute(
                "INSERT INTO submissions(created_at,status,payload_json) VALUES(?,?,?)",
                ("now", "ok", "{}"),
            )
            conn.commit()
    finally:
        continue_scan.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert result and result[0]["scan_complete"] is True


def test_startup_ingest_runs_off_event_loop_and_passes_background_bound(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    database = tmp_path / "library.sqlite"
    add_source(database, source_id="source", label="Source", root_path=source_root)
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(database.parent))
    monkeypatch.setattr(ingest, "_BACKGROUND_INGEST_MAX_FILES", 7)
    monkeypatch.setattr(ingest, "_INGEST_INTERVAL_S", 3600)
    scanning = threading.Event()
    continue_scan = threading.Event()
    seen: list[int | None] = []

    def slow_sources(db_path=None, *, max_files=None):
        seen.append(max_files)
        scanning.set()
        assert continue_scan.wait(timeout=5)
        return {}

    monkeypatch.setattr(ingest, "ingest_sources", slow_sources)

    async def check_loop() -> None:
        task = ingest.start_library_ingest_loop()
        try:
            assert await asyncio.wait_for(asyncio.to_thread(scanning.wait, 2), timeout=3)
            await asyncio.wait_for(asyncio.sleep(0), timeout=0.2)
            assert seen == [7]
        finally:
            continue_scan.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(check_loop())


def test_bounded_source_batches_advance_until_the_entire_source_is_indexed(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    batch_size = 250
    file_count = batch_size * 2 + 1
    expected_names: set[str] = set()
    for index in range(file_count):
        name = f"Author - {index:04}.txt" if index % 2 == 0 else f"{index:04}.txt"
        expected_names.add(name)
        (source_root / name).write_text(f"Distinct searchable source content number {index}.", encoding="utf-8")
    database = tmp_path / "library.sqlite"
    add_source(database, source_id="source", label="Source", root_path=source_root)

    for _ in range(3):
        ingest.ingest_sources(database, max_files=batch_size)

    with sqlite3.connect(database) as conn:
        indexed = {
            row[0] for row in conn.execute("SELECT rel_path FROM files WHERE source_id='source' AND status='present'")
        }
    assert indexed == expected_names


def test_bounded_source_reconciles_removals_only_after_full_cycle(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    for index in range(5):
        (source_root / f"{index}.txt").write_text(
            f"Distinct searchable source content number {index}.", encoding="utf-8"
        )
    database = tmp_path / "library.sqlite"
    add_source(database, source_id="source", label="Source", root_path=source_root)

    first = ingest.ingest_sources(database, max_files=2)["source"]
    assert first["files_indexed"] == 2
    (source_root / "0.txt").unlink()
    middle = ingest.ingest_sources(database, max_files=2)["source"]
    assert middle["files_removed"] == 0
    final = ingest.ingest_sources(database, max_files=2)["source"]
    assert final["scan_complete"] is True
    assert final["files_removed"] == 1

    with sqlite3.connect(database) as conn:
        status = conn.execute("SELECT status FROM files WHERE source_id='source' AND rel_path='0.txt'").fetchone()[0]
        work_status = conn.execute(
            "SELECT status FROM works WHERE id=(SELECT work_id FROM files "
            "WHERE source_id='source' AND rel_path='0.txt')"
        ).fetchone()[0]
    assert status == "removed"
    assert work_status == "retired"


def test_failed_tail_enumeration_never_reconciles_removals(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    for index in range(3):
        (source_root / f"{index}.txt").write_text(
            f"Distinct searchable source content number {index}.", encoding="utf-8"
        )
    database = tmp_path / "library.sqlite"
    add_source(database, source_id="source", label="Source", root_path=source_root)

    initial = ingest.ingest_sources(database, max_files=2)["source"]
    assert initial["scan_complete"] is False
    (source_root / "0.txt").unlink()

    def failing_walk(_root, *, onerror):
        yield str(source_root), [], ["2.txt"]
        onerror(OSError("source disconnected during reconciliation"))

    monkeypatch.setattr(ingest.os, "walk", failing_walk)
    try:
        ingest.ingest_sources(database, max_files=2)
    except OSError as error:
        assert "disconnected" in str(error)
    else:
        raise AssertionError("failed traversal must abort the bounded ingestion pass")

    with sqlite3.connect(database) as conn:
        status = conn.execute("SELECT status FROM files WHERE source_id='source' AND rel_path='0.txt'").fetchone()[0]
        runs = conn.execute("SELECT COUNT(*) FROM ingest_runs").fetchone()[0]
    assert status == "present"
    assert runs == 1
