from __future__ import annotations

import sqlite3
from pathlib import Path

from library.ingest import ingest_directory
from library.search import search_passages

from tests.library.conftest import make_blank_pdf, make_text_pdf

GOLF = "hip joint torque golf swing inverse dynamics"


def test_ingest_search_dedupe_ocr(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    (root / "Golf").mkdir(parents=True)
    pdf = make_text_pdf(GOLF, "ankle torque follow-through")
    (root / "Golf" / "Smith - Golf Biomechanics Primer.pdf").write_bytes(pdf)
    (root / "Biomechanics" / "copy.pdf").parent.mkdir(parents=True)
    (root / "Biomechanics" / "copy.pdf").write_bytes(pdf)
    (root / "badname.pdf").write_bytes(make_text_pdf("non standard filename body"))
    (root / "tiny.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 3000)
    (root / "scan.pdf").write_bytes(make_blank_pdf())
    db = tmp_path / "library.sqlite"
    ingest_directory(root, db_path=db)
    hits = search_passages(db, "hip joint torque golf", budget_tokens=6000)
    assert hits and len({h.work_id for h in hits}) == 1
    assert any(h.author == "Smith" for h in hits)
    with sqlite3.connect(db) as conn:
        assert 1 in {r[0] for r in conn.execute("SELECT needs_ocr FROM works")}
