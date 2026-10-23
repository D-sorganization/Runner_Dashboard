"""Slow extraction must not monopolize the shared reference-ledger writer."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from library import ingest
from library.db import init_library_db
from library.sources import add_source
from pypdf import PdfReader, PdfWriter

pytestmark = pytest.mark.unit


def pdf(path: Path, title: str) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/Title": title})
    writer.write(path)


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "books"
    root.mkdir()
    db = init_library_db(tmp_path / "library.sqlite")
    add_source(db, source_id="books", label="Books", root_path=root)
    return root, db


def test_extraction_allows_a_second_writer_and_exposes_active_run(
    source: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, db = source
    pdf(root / "book.pdf", "Good")
    extracting, release = threading.Event(), threading.Event()
    extracted: list[int] = []

    def page_text(reader: PdfReader, page: int) -> str:
        extracted.append(page)
        extracting.set()
        assert release.wait(timeout=5), "test did not release extraction"
        return f"Useful energy transfer passage on page {page}."

    monkeypatch.setattr(ingest, "_page_text", page_text)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(ingest.ingest_directory, root, db_path=db, source_id="books", max_files=1)
        try:
            assert extracting.wait(timeout=5)
            with sqlite3.connect(db, timeout=0) as reader:
                active = reader.execute("SELECT COUNT(*) FROM ingest_runs WHERE finished_at IS NULL").fetchone()[0]
                visible_files = reader.execute("SELECT COUNT(*) FROM files").fetchone()[0]
                try:
                    reader.execute("BEGIN IMMEDIATE")
                    reader.execute(
                        "INSERT INTO ingest_rejections(source_root,rel_path,reason,seen_at) "
                        "VALUES('test','unrelated.txt','test','now')"
                    )
                    reader.commit()
                    writer_available = True
                except sqlite3.OperationalError:
                    writer_available = False
        finally:
            release.set()
        result = future.result(timeout=5)
    assert active == 1, "ingest activity must be visible before parsing finishes"
    assert writer_available, "PDF extraction held the shared SQLite writer"
    assert visible_files == 0 and result["files_indexed"] == 1
    assert extracted == [0, 1], "page text must be extracted once, before writes"
    with sqlite3.connect(db) as reader:
        assert reader.execute("SELECT COUNT(*) FROM passages").fetchone()[0] == 2
        assert reader.execute("SELECT COUNT(*) FROM ingest_runs WHERE finished_at IS NULL").fetchone()[0] == 0


def test_failed_later_file_keeps_completed_file_and_finishes_run(
    source: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, db = source
    pdf(root / "01-good.pdf", "Good")
    pdf(root / "02-bad.pdf", "Bad")

    def page_text(reader: PdfReader, page: int) -> str:
        if reader.metadata.title == "Bad":
            raise RuntimeError("controlled extraction failure")
        return "A completed, verifiable energy transfer passage."

    monkeypatch.setattr(ingest, "_page_text", page_text)
    with pytest.raises(RuntimeError, match="controlled extraction failure"):
        ingest.ingest_directory(root, db_path=db, source_id="books", max_files=2)
    with sqlite3.connect(db) as reader:
        assert reader.execute("SELECT COUNT(*) FROM files WHERE status='present'").fetchone()[0] == 1
        assert reader.execute("SELECT COUNT(*) FROM passages").fetchone()[0] == 2
        runs = reader.execute("SELECT finished_at,notes FROM ingest_runs").fetchall()
        assert len(runs) == 1 and runs[0][0] and "interrupted" in runs[0][1]


def test_failed_file_replacement_preserves_previous_digest_and_passages(
    source: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, db = source
    path = root / "book.pdf"
    pdf(path, "Good")
    monkeypatch.setattr(ingest, "_page_text", lambda *_: "Existing verifiable energy transfer content.")
    ingest.ingest_directory(root, db_path=db, source_id="books", max_files=1)
    with sqlite3.connect(db) as reader:
        original_file = reader.execute("SELECT work_id,sha256 FROM files").fetchone()
        original_passages = reader.execute("SELECT id,work_id,text FROM passages ORDER BY id").fetchall()
    insert = ingest._insert_passages

    def fail_after_insert(*args: Any, **kwargs: Any) -> None:
        insert(*args, **kwargs)
        raise RuntimeError("controlled database write failure")

    pdf(path, "Changed document metadata and digest")
    monkeypatch.setattr(ingest, "_insert_passages", fail_after_insert)
    with pytest.raises(RuntimeError, match="controlled database write failure"):
        ingest.ingest_directory(root, db_path=db, source_id="books", max_files=1)
    with sqlite3.connect(db) as reader:
        assert reader.execute("SELECT work_id,sha256 FROM files").fetchone() == original_file
        assert reader.execute("SELECT id,work_id,text FROM passages ORDER BY id").fetchall() == original_passages
        assert reader.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 1
        assert reader.execute("SELECT COUNT(*) FROM ingest_runs WHERE finished_at IS NULL").fetchone()[0] == 0


def test_busy_commit_does_not_count_a_rolled_back_file(
    source: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, db = source
    pdf(root / "book.pdf", "Good")
    observer: sqlite3.Connection | None = None

    class BusyCommit(sqlite3.Connection):
        def __exit__(self, *args: Any) -> Any:
            nonlocal observer
            try:
                return super().__exit__(*args)
            finally:
                if observer is not None:
                    observer.close()
                    observer = None

    def connection(store: ingest.LibraryStore) -> sqlite3.Connection:
        conn = sqlite3.connect(store.path, timeout=0, factory=BusyCommit)
        conn.row_factory = sqlite3.Row
        return conn

    def page_text(reader: PdfReader, page: int) -> str:
        nonlocal observer
        if observer is None:
            observer = sqlite3.connect(db)
            observer.execute("BEGIN")
            observer.execute("SELECT COUNT(*) FROM works").fetchone()
        return "A verifiable energy transfer passage."

    monkeypatch.setattr(ingest.LibraryStore, "connect", connection)
    monkeypatch.setattr(ingest, "_page_text", page_text)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        ingest.ingest_directory(root, db_path=db, source_id="books", max_files=1)
    with sqlite3.connect(db) as reader:
        assert reader.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
        indexed, finished = reader.execute("SELECT files_indexed,finished_at FROM ingest_runs").fetchone()
        assert indexed == 0 and finished
