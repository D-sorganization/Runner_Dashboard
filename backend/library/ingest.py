from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import closing
from io import BytesIO
from pathlib import Path
from typing import Any

from library.db import LibraryStore, init_library_db
from library.search import estimate_tokens
from pypdf import PdfReader

_MIN_PAGE_CHARS = 12
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def ingest_directory(source_root: Path, *, db_path: Path | None = None) -> dict[str, int]:
    source_root = source_root.resolve()
    if not source_root.is_dir():
        raise ValueError(f"source is not a directory: {source_root}")
    db = init_library_db(db_path)
    pdfs = sorted(
        (p for p in source_root.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"),
        key=lambda p: (0 if " - " in p.stem else 1, p.as_posix()),
    )
    seen = indexed = 0
    with closing(LibraryStore(db).connect()) as conn:
        for path in pdfs:
            seen += 1
            rel = path.relative_to(source_root).as_posix()
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            stat = path.stat()
            try:
                reader = PdfReader(BytesIO(raw))
            except Exception:
                continue
            work_id = _ensure_work(conn, digest, reader, path)
            conn.execute(
                "INSERT INTO files (work_id, rel_path, mtime, size, sha256, status) VALUES (?,?,?,?,?,'present')"
                " ON CONFLICT(rel_path) DO UPDATE SET work_id=excluded.work_id, mtime=excluded.mtime,"
                " size=excluded.size, sha256=excluded.sha256, status='present'",
                (work_id, rel, stat.st_mtime, stat.st_size, digest),
            )
            _replace_passages(conn, work_id, reader)
            indexed += 1
        conn.commit()
    return {"files_seen": seen, "files_indexed": indexed}


def _ensure_work(conn: sqlite3.Connection, digest: str, reader: PdfReader, path: Path) -> int:
    row = conn.execute("SELECT id FROM works WHERE sha256 = ?", (digest,)).fetchone()
    if row:
        return int(row[0])
    author, title, year = _parse_filename(path.stem)
    author, title, year = _merge_pdf_meta(author, title, year, reader.metadata)
    needs_ocr = int(any(len(_page_text(reader, i).strip()) < _MIN_PAGE_CHARS for i in range(len(reader.pages))))
    cur = conn.execute(
        "INSERT INTO works (sha256, author, title, year, format, status, needs_ocr) VALUES (?,?,?,?,'pdf','active',?)",
        (digest, author, title, year, needs_ocr),
    )
    if cur.lastrowid is None:
        raise RuntimeError("insert work failed")
    return int(cur.lastrowid)


def _replace_passages(conn: sqlite3.Connection, work_id: int, reader: PdfReader) -> None:
    conn.execute("DELETE FROM passages WHERE work_id = ?", (work_id,))
    title = conn.execute("SELECT title FROM works WHERE id = ?", (work_id,)).fetchone()
    title_s = (title[0] if title else None) or ""
    for i in range(len(reader.pages)):
        text = _page_text(reader, i)
        if not text.strip():
            continue
        cur = conn.execute(
            "INSERT INTO passages (work_id, page_start, page_end, section, text, token_estimate)"
            " VALUES (?, ?, ?, NULL, ?, ?)",
            (work_id, i + 1, i + 1, text, estimate_tokens(text)),
        )
        conn.execute(
            "INSERT INTO passages_fts (rowid, title, text) VALUES (?, ?, ?)",
            (cur.lastrowid, title_s, text),
        )


def _parse_filename(stem: str) -> tuple[str | None, str | None, int | None]:
    stem = stem.strip()
    if not stem:
        return None, None, None
    if " - " in stem:
        author, title = (p.strip() or None for p in stem.split(" - ", 1))
        return author, title, _year(stem)
    return None, stem, _year(stem)


def _merge_pdf_meta(
    author: str | None, title: str | None, year: int | None, pdf_meta: dict[str, Any] | None
) -> tuple[str | None, str | None, int | None]:
    if not pdf_meta:
        return author, title, year

    def _s(v: object) -> str | None:
        t = str(v).strip() if v is not None else ""
        return t or None

    author = author or _s(pdf_meta.get("/Author"))
    title = title or _s(pdf_meta.get("/Title"))
    return author, title, year or _year(f"{author or ''} {title or ''}")


def _year(text: str) -> int | None:
    match = _YEAR_RE.search(text)
    if not match:
        return None
    year = int(match.group(0))
    return year if 1800 <= year <= 2100 else None


def _page_text(reader: PdfReader, page_index: int) -> str:
    try:
        return reader.pages[page_index].extract_text() or ""
    except Exception:
        return ""
