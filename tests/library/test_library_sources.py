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


def test_source_availability_tracks_disconnect_without_deleting_index(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    item = root / "keep.pdf"
    item.write_bytes(make_text_pdf("A source document which remains available after reconnect."))
    original = item.read_bytes()
    db = tmp_path / "library.sqlite"
    add_source(db, source_id="desk-x9-library", label="Desk X9", root_path=root)
    ingest_directory(root, db_path=db, source_id="desk-x9-library")

    assert list_sources(db)[0]["availability"] == "available"
    disconnected = tmp_path / "disconnected"
    root.rename(disconnected)
    unavailable = list_sources(db)
    assert len(unavailable) == 1
    assert unavailable[0]["availability"] == "unavailable"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT status FROM files WHERE source_id='desk-x9-library'").fetchone() == ("present",)
        assert conn.execute("SELECT id FROM library_sources WHERE id='desk-x9-library'").fetchone() == (
            "desk-x9-library",
        )
    assert (disconnected / "keep.pdf").read_bytes() == original

    disconnected.rename(root)
    assert list_sources(db)[0]["availability"] == "available"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM files WHERE source_id='desk-x9-library'").fetchone() == (1,)
        assert conn.execute("SELECT COUNT(*) FROM works WHERE status='active'").fetchone() == (1,)


def test_source_list_cli_reports_unavailable_without_network_or_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import socket
    import sys

    from library.__main__ import main

    data = tmp_path / "data"
    root = tmp_path / "source"
    root.mkdir()
    item = root / "notes.md"
    item.write_text("still on the disconnected source", encoding="utf-8")
    db = data / "library.sqlite"
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(data))
    add_source(db, source_id="drive-mirror", label="Drive mirror", root_path=root)
    disconnected = tmp_path / "disconnected"
    root.rename(disconnected)
    monkeypatch.setattr(sys, "argv", ["library", "sources", "list"])
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("unexpected network access"))

    assert main() == 0
    output = capsys.readouterr().out
    assert "drive-mirror" in output
    assert "availability=unavailable" in output
    assert item.name == "notes.md"
    assert (disconnected / item.name).read_text(encoding="utf-8") == "still on the disconnected source"


def test_google_drive_connector_and_multi_backend_priority_ordering(tmp_path: Path) -> None:
    from library.sources import extract_drive_folder_id, list_shared_drive_sources, update_source

    db = tmp_path / "library.sqlite"
    local_dir = tmp_path / "local_docs"
    drive_local_dir = tmp_path / "drive_desktop"
    local_dir.mkdir()
    drive_local_dir.mkdir()

    fid, url = extract_drive_folder_id(
        "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345?usp=sharing"
    )
    assert fid == "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    assert url == "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345"

    with pytest.raises(ValueError, match="valid Google Drive folder ID"):
        extract_drive_folder_id("not-a-drive-url/bad")

    add_source(
        db, source_id="local-docs", label="Local Docs", root_path=local_dir, backend_type="local_folder", shared=True
    )
    add_source(
        db,
        source_id="gdrive-desktop",
        label="Drive Desktop Mirror",
        root_path=drive_local_dir,
        backend_type="google_drive_local",
        shared=True,
    )
    conn_src = add_source(
        db,
        source_id="gdrive-agent",
        label="Agent Shared Drive",
        backend_type="google_drive_connector",
        drive_folder_url="https://drive.google.com/drive/u/0/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345",
        shared=True,
        repos=["Runner_Dashboard"],
    )
    assert conn_src["drive_folder_id"] == fid
    assert conn_src["root_path"] == f"gdrive://{fid}"
    assert conn_src["preferential"] is True
    assert conn_src["priority"] == 1
    assert conn_src["availability"] == "available"

    ordered = [s["id"] for s in list_sources(db)]
    assert ordered == ["gdrive-agent", "gdrive-desktop", "local-docs"]
    assert [s["id"] for s in list_shared_drive_sources(db, repo="Runner_Dashboard")] == ["gdrive-agent"]
    assert list_shared_drive_sources(db, repo="OtherRepo") == []

    # Duplicate-ID restrictive toggle rule: if local mirror of same Drive folder is unshared, connector is restricted
    update_source(db, "gdrive-desktop", drive_folder_id=fid, shared=False)
    assert list_shared_drive_sources(db, repo="Runner_Dashboard") == []
    update_source(db, "gdrive-desktop", shared=True)
    assert [s["id"] for s in list_shared_drive_sources(db, repo="Runner_Dashboard")] == ["gdrive-agent"]
