from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from library.collections import load_collections, topics_for_rel_path
from library.db import LibraryStore, init_library_db, library_source_dir
from library.search import estimate_tokens
from library.sources import get_source, matches_source_globs, seed_default_source
from pypdf import PdfReader

log = logging.getLogger("dashboard.library.ingest")
_INGEST_INTERVAL_S = float(os.environ.get("LIBRARY_INGEST_INTERVAL_S", "3600"))
_MIN_PAGE_CHARS = 12
_MIN_PDF_BYTES = 4096
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_INGEST_SUFFIXES = {".pdf", ".epub"}


def ingest_directory(source_root: Path, *, db_path: Path | None = None, source_id: str = "default") -> dict[str, int]:
    source_root = source_root.resolve()
    if not source_root.is_dir():
        raise ValueError(f"source is not a directory: {source_root}")
    db = init_library_db(db_path)
    seed_default_source(db)
    try:
        source = get_source(db, source_id)
        include_globs, exclude_globs = source["include_globs"], source["exclude_globs"]
    except LookupError:
        include_globs, exclude_globs = [], []
    started = datetime.now(UTC).isoformat()
    stats = {"files_seen": 0, "files_indexed": 0, "files_skipped": 0, "files_rejected": 0, "files_removed": 0}
    seen_paths: set[str] = set()
    with closing(LibraryStore(db).connect()) as conn:
        run_id = conn.execute(
            "INSERT INTO ingest_runs (started_at, source_root) VALUES (?, ?)",
            (started, source_root.as_posix()),
        ).lastrowid
        paths = sorted(
            (
                p
                for p in source_root.rglob("*")
                if p.is_file()
                and p.suffix.lower() in _INGEST_SUFFIXES
                and matches_source_globs(p.relative_to(source_root).as_posix(), include_globs, exclude_globs)
            ),
            key=lambda p: (0 if " - " in p.stem else 1, p.as_posix()),
        )
        for path in paths:
            stats["files_seen"] += 1
            rel = path.relative_to(source_root).as_posix()
            seen_paths.add(rel)
            stat = path.stat()
            if _should_skip(conn, source_id, rel, stat.st_mtime, stat.st_size):
                stats["files_skipped"] += 1
                continue
            raw = path.read_bytes()
            reason = _reject_reason(path, raw)
            if reason:
                _record_rejection(conn, source_root, rel, reason, started)
                stats["files_rejected"] += 1
                continue
            conn.execute(
                "DELETE FROM ingest_rejections WHERE source_root=? AND rel_path=?",
                (source_root.as_posix(), rel),
            )
            digest = hashlib.sha256(raw).hexdigest()
            if path.suffix.lower() == ".pdf":
                if not _index_pdf(conn, path, rel, source_root, stat, raw, digest, source_id):
                    stats["files_rejected"] += 1
                    continue
            else:
                if not _index_epub(conn, path, rel, source_root, stat, raw, digest, source_id):
                    stats["files_rejected"] += 1
                    continue
            stats["files_indexed"] += 1
        stats["files_removed"] = _mark_removals(conn, source_root, seen_paths, source_id)
        _retire_orphan_works(conn)
        conn.execute(
            "UPDATE ingest_runs SET finished_at=?, files_seen=?, files_indexed=?, files_skipped=?,"
            " files_rejected=?, files_removed=? WHERE id=?",
            (
                datetime.now(UTC).isoformat(),
                stats["files_seen"],
                stats["files_indexed"],
                stats["files_skipped"],
                stats["files_rejected"],
                stats["files_removed"],
                run_id,
            ),
        )
        conn.commit()
    return stats


def _should_skip(conn: sqlite3.Connection, source_id: str, rel: str, mtime: float, size: int) -> bool:
    row = conn.execute(
        "SELECT mtime, size, status FROM files WHERE source_id=? AND rel_path=?",
        (source_id, rel),
    ).fetchone()
    return bool(row and row[0] == mtime and row[1] == size and row[2] == "present")


def _reject_reason(path: Path, raw: bytes) -> str | None:
    if path.suffix.lower() != ".pdf" or len(raw) >= _MIN_PDF_BYTES:
        return None
    try:
        reader = PdfReader(BytesIO(raw))
        if reader.pages:
            return None
    except Exception:
        pass
    return "too_small"


def _record_rejection(conn: sqlite3.Connection, source_root: Path, rel: str, reason: str, seen_at: str) -> None:
    conn.execute(
        "INSERT INTO ingest_rejections (source_root, rel_path, reason, seen_at) VALUES (?,?,?,?)"
        " ON CONFLICT(source_root, rel_path) DO UPDATE SET reason=excluded.reason, seen_at=excluded.seen_at",
        (source_root.as_posix(), rel, reason, seen_at),
    )


def _index_pdf(
    conn: sqlite3.Connection,
    path: Path,
    rel: str,
    source_root: Path,
    stat: Any,
    raw: bytes,
    digest: str,
    source_id: str = "default",
) -> bool:
    try:
        reader = PdfReader(BytesIO(raw))
    except Exception:
        _record_rejection(conn, source_root, rel, "corrupt_pdf", datetime.now(UTC).isoformat())
        return False
    if not reader.pages:
        _record_rejection(conn, source_root, rel, "zero_pages", datetime.now(UTC).isoformat())
        return False
    work_id = _ensure_work(conn, digest, reader, path, fmt="pdf")
    _upsert_file(conn, work_id, rel, source_root, stat, digest, source_id)
    _merge_work_topics(conn, work_id, rel)
    _replace_passages(conn, work_id, reader)
    return True


def _index_epub(
    conn: sqlite3.Connection,
    path: Path,
    rel: str,
    source_root: Path,
    stat: Any,
    raw: bytes,
    digest: str,
    source_id: str = "default",
) -> bool:
    try:
        meta_author, meta_title, chapters = _extract_epub(raw)
    except Exception:
        _record_rejection(conn, source_root, rel, "corrupt_epub", datetime.now(UTC).isoformat())
        return False
    if not chapters:
        _record_rejection(conn, source_root, rel, "empty_epub", datetime.now(UTC).isoformat())
        return False
    author, title, year = _parse_filename(path.stem)
    author = author or meta_author
    title = title or meta_title
    work_id = _ensure_work_epub(conn, digest, author, title, year)
    _upsert_file(conn, work_id, rel, source_root, stat, digest, source_id)
    _merge_work_topics(conn, work_id, rel)
    _replace_epub_passages(conn, work_id, title or "", chapters)
    return True


def _merge_work_topics(conn: sqlite3.Connection, work_id: int, rel: str) -> None:
    cfg = load_collections()
    new = topics_for_rel_path(rel, cfg)
    if not new:
        return
    row = conn.execute("SELECT topics FROM works WHERE id=?", (work_id,)).fetchone()
    cur = json.loads(row[0] or "[]") if row and row[0] else []
    conn.execute("UPDATE works SET topics=? WHERE id=?", (json.dumps(sorted(set(cur) | set(new))), work_id))


def _upsert_file(
    conn: sqlite3.Connection,
    work_id: int,
    rel: str,
    source_root: Path,
    stat: Any,
    digest: str,
    source_id: str = "default",
) -> None:
    conn.execute(
        "INSERT INTO files (work_id, source_id, rel_path, source_root, mtime, size, sha256, status)"
        " VALUES (?,?,?,?,?,?,?,'present')"
        " ON CONFLICT(source_id,rel_path) DO UPDATE SET work_id=excluded.work_id, source_root=excluded.source_root,"
        " mtime=excluded.mtime, size=excluded.size, sha256=excluded.sha256, status='present'",
        (work_id, source_id, rel, source_root.as_posix(), stat.st_mtime, stat.st_size, digest),
    )


def _mark_removals(
    conn: sqlite3.Connection, source_root: Path, seen_paths: set[str], source_id: str = "default"
) -> int:
    root = source_root.as_posix()
    rows = conn.execute(
        "SELECT rel_path FROM files WHERE source_id=? AND source_root=? AND status='present'",
        (source_id, root),
    ).fetchall()
    removed = 0
    for (rel_path,) in rows:
        if rel_path in seen_paths:
            continue
        conn.execute("UPDATE files SET status='removed' WHERE source_id=? AND rel_path=?", (source_id, rel_path))
        removed += 1
    return removed


def _retire_orphan_works(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE works SET status='retired' WHERE status='active' AND id NOT IN"
        " (SELECT DISTINCT work_id FROM files WHERE status='present')"
    )


def _ensure_work(conn: sqlite3.Connection, digest: str, reader: PdfReader, path: Path, *, fmt: str) -> int:
    row = conn.execute("SELECT id FROM works WHERE sha256 = ?", (digest,)).fetchone()
    if row:
        return int(row[0])
    author, title, year = _parse_filename(path.stem)
    author, title, year = _merge_pdf_meta(author, title, year, reader.metadata)
    needs_ocr = int(any(len(_page_text(reader, i).strip()) < _MIN_PAGE_CHARS for i in range(len(reader.pages))))
    cur = conn.execute(
        "INSERT INTO works (sha256, author, title, year, format, status, needs_ocr) VALUES (?,?,?,?,?,'active',?)",
        (digest, author, title, year, fmt, needs_ocr),
    )
    if cur.lastrowid is None:
        raise RuntimeError("insert work failed")
    return int(cur.lastrowid)


def _ensure_work_epub(
    conn: sqlite3.Connection, digest: str, author: str | None, title: str | None, year: int | None
) -> int:
    row = conn.execute("SELECT id FROM works WHERE sha256 = ?", (digest,)).fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        "INSERT INTO works (sha256, author, title, year, format, status, needs_ocr) VALUES (?,?,?,?,'epub','active',0)",
        (digest, author, title, year),
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


def _replace_epub_passages(
    conn: sqlite3.Connection, work_id: int, title_s: str, chapters: list[tuple[str, str]]
) -> None:
    conn.execute("DELETE FROM passages WHERE work_id = ?", (work_id,))
    for idx, (section, text) in enumerate(chapters, start=1):
        cur = conn.execute(
            "INSERT INTO passages (work_id, page_start, page_end, section, text, token_estimate)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (work_id, idx, idx, section, text, estimate_tokens(text)),
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


def _extract_epub(raw: bytes) -> tuple[str | None, str | None, list[tuple[str, str]]]:
    import zipfile
    from xml.etree import ElementTree

    ns = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        container = ElementTree.fromstring(zf.read("META-INF/container.xml"))
        rootfile = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
        if rootfile is None:
            return None, None, []
        opf_path = rootfile.attrib.get("full-path", "")
        if not opf_path:
            return None, None, []
        opf = ElementTree.fromstring(zf.read(opf_path))
        base = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""

        def _dc(tag: str) -> str | None:
            node = opf.find(f".//dc:{tag}", ns)
            return (node.text or "").strip() or None if node is not None else None

        manifest = {item.attrib["id"]: item.attrib["href"] for item in opf.findall(".//opf:item", ns)}
        chapters: list[tuple[str, str]] = []
        for itemref in opf.findall(".//opf:itemref", ns):
            href = manifest.get(itemref.attrib.get("idref", ""), "")
            if not href.endswith((".xhtml", ".html", ".htm")):
                continue
            try:
                html = zf.read(base + href).decode("utf-8", errors="replace")
            except KeyError:
                continue
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
            if text:
                chapters.append((href.rsplit("/", 1)[-1], text))
        return _dc("creator"), _dc("title"), chapters


def start_library_ingest_loop() -> None:
    if library_source_dir() is None:
        return

    async def _loop() -> None:
        while True:
            root = library_source_dir()
            if root is not None and root.is_dir():
                try:
                    log.info("library ingest: %s", ingest_directory(root))
                except Exception as exc:  # noqa: BLE001
                    log.warning("library ingest failed: %s", exc)
            await asyncio.sleep(_INGEST_INTERVAL_S)

    asyncio.create_task(_loop())
