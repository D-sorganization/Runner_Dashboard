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
