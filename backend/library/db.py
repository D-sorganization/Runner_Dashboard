"""SQLite schema and paths for Alexandria."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

_DEFAULT_DATA = Path.home() / ".local" / "share" / "runner-dashboard" / "library"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
    id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, author TEXT, title TEXT, year INTEGER,
    doi TEXT, isbn TEXT, format TEXT NOT NULL, topics TEXT, provenance TEXT,
    status TEXT NOT NULL DEFAULT 'active', needs_ocr INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY, work_id INTEGER NOT NULL REFERENCES works(id), rel_path TEXT NOT NULL UNIQUE,
    drive_id TEXT, drive_link TEXT, mtime REAL NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'present');
CREATE TABLE IF NOT EXISTS passages (
    id INTEGER PRIMARY KEY, work_id INTEGER NOT NULL REFERENCES works(id), page_start INTEGER NOT NULL,
    page_end INTEGER NOT NULL, section TEXT, text TEXT NOT NULL, token_estimate INTEGER NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(title, text, content='', tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ingest_runs (
    id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, source_root TEXT NOT NULL,
    files_seen INTEGER NOT NULL DEFAULT 0, files_indexed INTEGER NOT NULL DEFAULT 0,
    files_skipped INTEGER NOT NULL DEFAULT 0, files_rejected INTEGER NOT NULL DEFAULT 0,
    files_removed INTEGER NOT NULL DEFAULT 0, notes TEXT);
CREATE TABLE IF NOT EXISTS ingest_rejections (
    id INTEGER PRIMARY KEY, source_root TEXT NOT NULL, rel_path TEXT NOT NULL,
    reason TEXT NOT NULL, seen_at TEXT NOT NULL, UNIQUE(source_root, rel_path));
"""


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(files)")}
    if "source_root" not in cols:
        conn.execute("ALTER TABLE files ADD COLUMN source_root TEXT NOT NULL DEFAULT ''")
    run_cols = {row[1] for row in conn.execute("PRAGMA table_info(ingest_runs)")}
    for name in ("files_skipped", "files_rejected", "files_removed"):
        if name not in run_cols:
            conn.execute(f"ALTER TABLE ingest_runs ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")


def library_data_dir() -> Path:
    raw = os.environ.get("LIBRARY_DATA_DIR", "").strip()
    return Path(raw) if raw else _DEFAULT_DATA


def library_db_path() -> Path:
    return library_data_dir() / "library.sqlite"


def library_source_dir() -> Path | None:
    raw = os.environ.get("LIBRARY_SOURCE_DIR", "").strip()
    return Path(raw) if raw else None


def init_library_db(path: Path | None = None) -> Path:
    db = Path(path) if path is not None else library_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.commit()
    return db


class LibraryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
