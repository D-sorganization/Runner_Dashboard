from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from library.ingest import ingest_directory
from library.search import search_passages

from tests.library.conftest import make_text_pdf

GOLF = "hip joint torque golf swing"


def test_incremental_and_ingest_run(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    root.mkdir()
    pdf_path = root / "Ada - Golf EPUB Primer.pdf"
    pdf_path.write_bytes(make_text_pdf(GOLF))
    db = tmp_path / "library.sqlite"
    first = ingest_directory(root, db_path=db)
    second = ingest_directory(root, db_path=db)
    assert first["files_indexed"] == 1 and second["files_skipped"] == 1
    with sqlite3.connect(db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM ingest_runs WHERE finished_at IS NOT NULL").fetchone()[0]
    assert n == 2


def test_removed_copy_and_reindex_on_change(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    root.mkdir(parents=True)
    (root / "a").mkdir()
    (root / "b").mkdir()
    pdf = make_text_pdf(GOLF)
    (root / "a" / "Smith - Shared Work.pdf").write_bytes(pdf)
    (root / "b" / "copy.pdf").write_bytes(pdf)
    db = tmp_path / "library.sqlite"
    ingest_directory(root, db_path=db)
    (root / "b" / "copy.pdf").unlink()
    ingest_directory(root, db_path=db)
    path = root / "Smith - Touch.pdf"
    path.write_bytes(make_text_pdf("version one"))
    ingest_directory(root, db_path=db)
    time.sleep(0.05)
    path.write_bytes(make_text_pdf("version two different text layer"))
    out = ingest_directory(root, db_path=db)
    with sqlite3.connect(db) as conn:
        removed = conn.execute("SELECT status FROM files WHERE rel_path='b/copy.pdf'").fetchone()
    assert removed and removed[0] == "removed" and out["files_indexed"] == 1
    assert search_passages(db, "version two", budget_tokens=2000)


def test_epub_ingest_searchable(tmp_path: Path) -> None:
    from io import BytesIO
    from zipfile import ZIP_DEFLATED, ZipFile

    root = tmp_path / "lib"
    root.mkdir()
    buf = BytesIO()
    with ZipFile(buf, "w", ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        zf.writestr(
            "content.opf",
            '<package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<dc:title>Motor Learning</dc:title><dc:creator>Ada Author</dc:creator></metadata>"
            '<manifest><item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="ch1"/></spine></package>',
        )
        zf.writestr("ch1.xhtml", f"<html><body><p>{GOLF}</p></body></html>")
    (root / "Ada - Motor Learning Notes.epub").write_bytes(buf.getvalue())
    db = tmp_path / "library.sqlite"
    assert ingest_directory(root, db_path=db)["files_indexed"] == 1
    hits = search_passages(db, "hip joint torque", budget_tokens=4000)
    assert hits and hits[0].title == "Motor Learning Notes"


def test_rejections_recorded_for_tiny_pdf(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    root.mkdir()
    (root / "tiny.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 3000)
    db = tmp_path / "library.sqlite"
    assert ingest_directory(root, db_path=db)["files_rejected"] == 1
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT reason FROM ingest_rejections").fetchone()
    assert row and "too_small" in row[0]
