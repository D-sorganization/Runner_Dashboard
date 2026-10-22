from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from library.db import init_library_db
from library.ingest import ingest_directory
from library.sources import add_source, list_sources, remove_source, seed_default_source

from tests.library.conftest import make_text_pdf


def _legacy_db(path: Path) -> None:
    """Create the on-disk Alexandria schema shipped before source registries."""
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE works (
            id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, author TEXT, title TEXT, year INTEGER,
            doi TEXT, isbn TEXT, format TEXT NOT NULL, topics TEXT, provenance TEXT,
            status TEXT NOT NULL DEFAULT 'active', needs_ocr INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE files (
            id INTEGER PRIMARY KEY, work_id INTEGER NOT NULL REFERENCES works(id), rel_path TEXT NOT NULL UNIQUE,
            drive_id TEXT, drive_link TEXT, mtime REAL NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'present', source_root TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE passages (
            id INTEGER PRIMARY KEY, work_id INTEGER NOT NULL REFERENCES works(id), page_start INTEGER NOT NULL,
            page_end INTEGER NOT NULL, section TEXT, text TEXT NOT NULL, token_estimate INTEGER NOT NULL
        );
        CREATE VIRTUAL TABLE passages_fts USING fts5(title, text, content='', tokenize='porter unicode61');
        CREATE TABLE submissions (
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL
        );
        CREATE TABLE ingest_runs (
            id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, source_root TEXT NOT NULL,
            files_seen INTEGER NOT NULL DEFAULT 0, files_indexed INTEGER NOT NULL DEFAULT 0,
            files_skipped INTEGER NOT NULL DEFAULT 0, files_rejected INTEGER NOT NULL DEFAULT 0,
            files_removed INTEGER NOT NULL DEFAULT 0, notes TEXT
        );
        CREATE TABLE ingest_rejections (
            id INTEGER PRIMARY KEY, source_root TEXT NOT NULL, rel_path TEXT NOT NULL,
            reason TEXT NOT NULL, seen_at TEXT NOT NULL, UNIQUE(source_root, rel_path)
        );
        """)
        conn.execute("INSERT INTO works(sha256,format) VALUES ('legacy','pdf')")
        conn.execute("INSERT INTO files(work_id,rel_path,mtime,size,sha256) VALUES (1,'old.pdf',1,4,'legacy')")


def test_additive_migration_preserves_legacy_file_and_composite_path_key(tmp_path: Path) -> None:
    db = tmp_path / "library.sqlite"
    _legacy_db(db)
    init_library_db(db)
    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(files)")}
        assert {"source_id", "source_root"} <= columns
        conn.execute(
            "INSERT INTO library_sources(id,label,root_path,shared,include_globs,exclude_globs,created_at,status) "
            "VALUES ('a','A','/a',0,'[]','[]','now','active')"
        )
        conn.execute(
            "INSERT INTO library_sources(id,label,root_path,shared,include_globs,exclude_globs,created_at,status) "
            "VALUES ('b','B','/b',0,'[]','[]','now','active')"
        )
        conn.execute("UPDATE files SET source_id='a' WHERE rel_path='old.pdf'")
        conn.execute("INSERT INTO works(sha256,format) VALUES ('second','pdf')")
        conn.execute(
            "INSERT INTO files(work_id,source_id,rel_path,mtime,size,sha256) VALUES (2,'b','old.pdf',1,4,'second')"
        )
        assert conn.execute("SELECT COUNT(*) FROM files WHERE rel_path='old.pdf'").fetchone()[0] == 2


def test_same_content_in_two_sources_is_one_work_and_two_copies(tmp_path: Path) -> None:
    db = tmp_path / "library.sqlite"
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    raw = make_text_pdf("A shared source text with enough content")
    (left / "same.pdf").write_bytes(raw)
    (right / "same.pdf").write_bytes(raw)
    add_source(db, source_id="left", label="Left", root_path=left)
    add_source(db, source_id="right", label="Right", root_path=right)
    ingest_directory(left, db_path=db, source_id="left")
    ingest_directory(right, db_path=db, source_id="right")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM files WHERE status='present'").fetchone()[0] == 2


def test_default_source_seed_and_root_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "source"
    data = tmp_path / "data"
    root.mkdir()
    data.mkdir()
    db = data / "library.sqlite"
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(data))
    monkeypatch.setenv("LIBRARY_SOURCE_DIR", str(root))
    seed_default_source(db)
    assert list_sources(db)[0]["id"] == "default"
    inside = data / "nested"
    inside.mkdir()
    with pytest.raises(ValueError):
        add_source(db, source_id="inside", label="bad", root_path=inside)
    nested = root / "nested"
    nested.mkdir()
    with pytest.raises(ValueError):
        add_source(db, source_id="nested", label="nested", root_path=nested)


def test_unregister_does_not_touch_source_tree(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    item = root / "keep.pdf"
    item.write_bytes(b"original")
    before = (item.read_bytes(), item.stat().st_mtime_ns)
    db = tmp_path / "library.sqlite"
    add_source(db, source_id="keep", label="Keep", root_path=root)
    remove_source(db, "keep")
    assert (item.read_bytes(), item.stat().st_mtime_ns) == before
