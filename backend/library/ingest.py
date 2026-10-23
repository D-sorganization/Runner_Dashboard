from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from library.collections import load_collections, topics_for_rel_path
from library.db import LibraryStore, init_library_db
from library.search import estimate_tokens
from library.sources import get_source, matches_source_globs, seed_default_source
from pypdf import PdfReader

log = logging.getLogger("dashboard.library.ingest")
_INGEST_INTERVAL_S = float(os.environ.get("LIBRARY_INGEST_INTERVAL_S", "3600"))
_BACKGROUND_INGEST_MAX_FILES = int(os.environ.get("LIBRARY_BACKGROUND_INGEST_MAX_FILES", "250"))
_MIN_PAGE_CHARS = 12
_MIN_PDF_BYTES = 4096
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_MAX_FILE_BYTES = 10 * 1024 * 1024
_INGEST_SUFFIXES = {".pdf", ".epub", ".md", ".markdown", ".txt"}


def ingest_directory(
    source_root: Path,
    *,
    db_path: Path | None = None,
    source_id: str = "default",
    max_files: int | None = None,
) -> dict[str, int | bool]:
    if max_files is not None and (isinstance(max_files, bool) or not isinstance(max_files, int) or max_files <= 0):
        raise ValueError("max_files must be a positive integer")
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
    paths: list[Path] = []
    for path in _eligible_paths(source_root, include_globs, exclude_globs):
        paths.append(path)
        if max_files is not None and len(paths) == max_files:
            break
    # If the limit was reached, treat the pass as incomplete conservatively:
    # a limited traversal must never infer that unseen files were removed.
    scan_complete = max_files is None or len(paths) < max_files
    if max_files is None:
        paths.sort(key=lambda p: (0 if " - " in p.stem else 1, p.as_posix()))
    started = datetime.now(UTC).isoformat()
    stats = {"files_seen": 0, "files_indexed": 0, "files_skipped": 0, "files_rejected": 0, "files_removed": 0}
    seen_paths: set[str] = set()
    with closing(LibraryStore(db).connect()) as conn:
        run_id = conn.execute(
            "INSERT INTO ingest_runs (started_at, source_root) VALUES (?, ?)",
            (started, source_root.as_posix()),
        ).lastrowid
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
            ext = path.suffix.lower()
            if ext == ".pdf":
                if not _index_pdf(conn, path, rel, source_root, stat, raw, digest, source_id):
                    stats["files_rejected"] += 1
                    continue
            elif ext == ".epub":
                if not _index_epub(conn, path, rel, source_root, stat, raw, digest, source_id):
                    stats["files_rejected"] += 1
                    continue
            elif ext in (".md", ".markdown", ".txt"):
                fmt = "txt" if ext == ".txt" else "md"
                if not _index_text(conn, path, rel, source_root, stat, raw, digest, source_id, fmt=fmt):
                    stats["files_rejected"] += 1
                    continue
            stats["files_indexed"] += 1
        if scan_complete:
            stats["files_removed"] = _mark_removals(conn, source_root, seen_paths, source_id)
            _retire_orphan_works(conn)
        notes = None if scan_complete else f"bounded incomplete scan (max_files={max_files}); removals not reconciled"
        conn.execute(
            "UPDATE ingest_runs SET finished_at=?, files_seen=?, files_indexed=?, files_skipped=?,"
            " files_rejected=?, files_removed=?, notes=? WHERE id=?",
            (
                datetime.now(UTC).isoformat(),
                stats["files_seen"],
                stats["files_indexed"],
                stats["files_skipped"],
                stats["files_rejected"],
                stats["files_removed"],
                notes,
                run_id,
            ),
        )
        conn.commit()
    return {**stats, "scan_complete": scan_complete}


def _eligible_paths(source_root: Path, include_globs: list[str], exclude_globs: list[str]) -> Iterator[Path]:
    for current, directories, filenames in os.walk(source_root):
        directories.sort()
        for filename in sorted(filenames):
            path = Path(current) / filename
            if (
                path.is_file()
                and path.suffix.lower() in _INGEST_SUFFIXES
                and matches_source_globs(path.relative_to(source_root).as_posix(), include_globs, exclude_globs)
            ):
                yield path


def _should_skip(conn: sqlite3.Connection, source_id: str, rel: str, mtime: float, size: int) -> bool:
    row = conn.execute(
        "SELECT mtime, size, status FROM files WHERE source_id=? AND rel_path=?",
        (source_id, rel),
    ).fetchone()
    return bool(row and row[0] == mtime and row[1] == size and row[2] == "present")


def _reject_reason(path: Path, raw: bytes) -> str | None:
    if len(raw) > _MAX_FILE_BYTES:
        return "file_too_large"
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
    work_id = _ensure_work_simple(conn, digest, author, title, year, fmt="epub")
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


def _ensure_work_simple(
    conn: sqlite3.Connection, digest: str, author: str | None, title: str | None, year: int | None, fmt: str = "epub"
) -> int:
    row = conn.execute("SELECT id FROM works WHERE sha256 = ?", (digest,)).fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        "INSERT INTO works (sha256, author, title, year, format, status, needs_ocr) VALUES (?,?,?,?,?,'active',0)",
        (digest, author, title, year, fmt),
    )
    if cur.lastrowid is None:
        raise RuntimeError("insert work failed")
    return int(cur.lastrowid)


def _insert_passages(
    conn: sqlite3.Connection, work_id: int, title_s: str, passages: Sequence[tuple[str | None, int, int, str]]
) -> None:
    conn.execute("DELETE FROM passages WHERE work_id = ?", (work_id,))
    for section, start, end, text in passages:
        cur = conn.execute(
            "INSERT INTO passages (work_id, page_start, page_end, section, text, token_estimate)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (work_id, start, end, section, text, estimate_tokens(text)),
        )
        conn.execute(
            "INSERT INTO passages_fts (rowid, title, text) VALUES (?, ?, ?)",
            (cur.lastrowid, title_s, text),
        )


def _replace_passages(conn: sqlite3.Connection, work_id: int, reader: PdfReader) -> None:
    title = conn.execute("SELECT title FROM works WHERE id = ?", (work_id,)).fetchone()
    passages: list[tuple[str | None, int, int, str]] = [
        (None, i + 1, i + 1, txt) for i in range(len(reader.pages)) if (txt := _page_text(reader, i).strip())
    ]
    _insert_passages(conn, work_id, (title[0] if title else None) or "", passages)


def _replace_epub_passages(
    conn: sqlite3.Connection, work_id: int, title_s: str, chapters: list[tuple[str, str]]
) -> None:
    _insert_passages(conn, work_id, title_s, [(sec, idx, idx, txt) for idx, (sec, txt) in enumerate(chapters, start=1)])


def _extract_text_passages(raw: bytes, fmt: str) -> list[tuple[str | None, int, int, str]]:
    lines = raw.decode("utf-8", errors="replace").splitlines(keepends=True)
    if not lines:
        return []
    passages: list[tuple[str | None, int, int, str]] = []
    sec: str | None = None
    cur: list[str] = []
    start: int = 1
    chars: int = 0
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        is_hd = fmt in ("md", "markdown") and stripped.startswith("#")
        if (is_hd or chars + len(line) > 1500) and cur:
            txt = "".join(cur).strip()
            if txt:
                passages.append((sec, start, idx - 1, txt))
            cur, chars, start = [], 0, idx
        if is_hd:
            sec = stripped.lstrip("#").strip() or None
        cur.append(line)
        chars += len(line)
    if cur and (txt := "".join(cur).strip()):
        passages.append((sec, start, len(lines), txt))
    return passages


def _index_text(
    conn: sqlite3.Connection,
    path: Path,
    rel: str,
    source_root: Path,
    stat: Any,
    raw: bytes,
    digest: str,
    source_id: str = "default",
    fmt: str = "md",
) -> bool:
    passages = _extract_text_passages(raw, fmt)
    if not passages:
        _record_rejection(conn, source_root, rel, "empty_file", datetime.now(UTC).isoformat())
        return False
    author, title, year = _parse_filename(path.stem)
    work_id = _ensure_work_simple(conn, digest, author, title, year, fmt=fmt)
    _upsert_file(conn, work_id, rel, source_root, stat, digest, source_id)
    _merge_work_topics(conn, work_id, rel)
    _insert_passages(conn, work_id, title or path.stem, passages)
    return True


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

    from defusedxml import ElementTree

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


def ingest_sources(db_path: Path | None = None, *, max_files: int | None = None) -> dict[str, dict[str, int | bool]]:
    db = init_library_db(db_path)
    seed_default_source(db)
    results: dict[str, dict[str, int | bool]] = {}
    with closing(LibraryStore(db).connect()) as conn:
        sources = conn.execute("SELECT id, root_path FROM library_sources WHERE status='active' ORDER BY id").fetchall()
    for source_id, root_path in sources:
        p = Path(root_path)
        if not p.is_dir():
            continue
        stats = ingest_directory(p, db_path=db, source_id=source_id, max_files=max_files)
        results[source_id] = stats
        with closing(LibraryStore(db).connect()) as conn:
            conn.execute(
                "UPDATE library_sources SET last_ingest_at=? WHERE id=?",
                (datetime.now(UTC).isoformat(), source_id),
            )
            conn.commit()
    return results


def start_library_ingest_loop() -> asyncio.Task[None]:
    async def _loop() -> None:
        while True:
            try:
                await asyncio.to_thread(ingest_sources, max_files=_BACKGROUND_INGEST_MAX_FILES)
            except Exception as exc:  # noqa: BLE001
                log.warning("library ingest failed: %s", exc)
            await asyncio.sleep(_INGEST_INTERVAL_S)

    return asyncio.create_task(_loop())
