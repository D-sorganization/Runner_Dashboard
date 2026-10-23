from __future__ import annotations

import sqlite3
from pathlib import Path

from library.db import init_library_db
from library.ingest import ingest_sources
from library.search import SearchOptions, search_passages
from library.sources import add_source, remove_source

from tests.library.conftest import make_text_pdf

GOLF = "hip joint torque golf swing rotational dynamics"


def test_multi_source_indexing_and_dedupe(tmp_path: Path) -> None:
    db = init_library_db(tmp_path / "library.sqlite")
    src1 = tmp_path / "src1"
    src2 = tmp_path / "src2"
    src1.mkdir()
    src2.mkdir()

    add_source(db, source_id="src-one", label="Source One", root_path=src1)
    add_source(db, source_id="src-two", label="Source Two", root_path=src2)

    pdf_bytes = make_text_pdf(GOLF)
    (src1 / "Smith - Biomechanics 2024.pdf").write_bytes(pdf_bytes)
    (src2 / "Duplicate - Biomechanics 2024.pdf").write_bytes(pdf_bytes)

    res = ingest_sources(db)
    assert "src-one" in res and "src-two" in res
    assert res["src-one"]["files_indexed"] == 1
    assert res["src-two"]["files_indexed"] == 1

    with sqlite3.connect(db) as conn:
        works = conn.execute("SELECT id, sha256 FROM works WHERE status='active'").fetchall()
        files = conn.execute("SELECT id, work_id, source_id, rel_path FROM files WHERE status='present'").fetchall()

    # 1 shared work row (deduplicated by sha256), 2 files rows with distinct source_ids
    assert len(works) == 1
    assert len(files) == 2
    sources_in_files = {f[2] for f in files}
    assert sources_in_files == {"src-one", "src-two"}


def test_markdown_and_text_indexing_and_search(tmp_path: Path) -> None:
    db = init_library_db(tmp_path / "library.sqlite")
    src = tmp_path / "src"
    src.mkdir()
    add_source(db, source_id="docs", label="Docs", root_path=src)

    md_content = """# Kinematics

Angular velocity in rotational sports must be measured relative to the pelvis.

## Dynamics

Torque generated at the pelvis transfers energy along the kinematic chain.
"""
    txt_content = "Plain text field notes on ground reaction force in athletic movement."

    (src / "Kinematics Guide.md").write_text(md_content, encoding="utf-8")
    (src / "Notes.txt").write_text(txt_content, encoding="utf-8")

    # A file exceeding 10MB cap should be skipped/rejected
    huge = src / "huge.txt"
    huge.write_bytes(b"A" * (10 * 1024 * 1024 + 10))

    ingest_sources(db)

    # Both markdown and text should be indexed and match FTS
    hits_md = search_passages(db, "angular velocity rotational sports", budget_tokens=2000)
    assert hits_md, "Markdown content should be indexed and searchable"
    assert hits_md[0].page_start >= 1
    assert hits_md[0].page_end >= hits_md[0].page_start

    hits_txt = search_passages(db, "ground reaction force", budget_tokens=2000)
    assert hits_txt, "Plain text content should be indexed and searchable"

    with sqlite3.connect(db) as conn:
        rejections = conn.execute("SELECT reason FROM ingest_rejections WHERE rel_path='huge.txt'").fetchall()
    assert rejections and "file_too_large" in rejections[0][0]


def test_search_hit_citation_and_cite_key(tmp_path: Path) -> None:
    db = init_library_db(tmp_path / "library.sqlite")
    src = tmp_path / "src"
    src.mkdir()
    add_source(db, source_id="papers", label="Papers", root_path=src)

    (src / "Doe - Rotational Power 2023.md").write_text(
        "# Intro\nRotational power generation dynamics.", encoding="utf-8"
    )
    ingest_sources(db)

    hits = search_passages(db, "rotational power dynamics", budget_tokens=2000)
    assert hits
    hit = hits[0]

    assert hit.source_id == "papers"
    assert len(hit.sha8) == 8
    assert "Doe" in hit.citation
    assert "Rotational Power" in hit.citation
    # Verify canonical reproducible cite_key
    assert hit.cite_key.startswith("[ref:")
    assert f":papers:w{hit.work_id}:p{hit.passage_id}#sha256={hit.sha8}:p." in hit.cite_key
    assert hit.cite_key.endswith("]")

    # Verify cite_uri: lib://<source_id>/w<work_id>/p<passage_id>@<sha8>#p<page_start>-<page_end>
    assert hit.cite_uri.startswith(f"lib://papers/w{hit.work_id}/p{hit.passage_id}@{hit.sha8}#p")


def test_removing_source_hides_passages_from_search(tmp_path: Path) -> None:
    db = init_library_db(tmp_path / "library.sqlite")
    src = tmp_path / "src"
    src.mkdir()
    add_source(db, source_id="removable", label="Removable", root_path=src)
    (src / "Author - Secret Algorithm.txt").write_text("proprietary secret algorithm mechanics", encoding="utf-8")
    ingest_sources(db)

    hits_before = search_passages(db, "proprietary secret algorithm", budget_tokens=2000)
    assert hits_before

    remove_source(db, "removable")

    hits_after = search_passages(db, "proprietary secret algorithm", budget_tokens=2000)
    assert not hits_after, "Passages from removed source should be hidden from search"


def test_source_directory_unchanged_after_ingest(tmp_path: Path) -> None:
    db = init_library_db(tmp_path / "library.sqlite")
    src = tmp_path / "src"
    src.mkdir()
    add_source(db, source_id="readonly", label="Read Only", root_path=src)

    file_path = src / "Test Doc.md"
    file_path.write_text("# Chapter 1\nContent for testing immutability.", encoding="utf-8")

    stat_before = file_path.stat()
    bytes_before = file_path.read_bytes()

    ingest_sources(db)

    stat_after = file_path.stat()
    bytes_after = file_path.read_bytes()

    assert stat_before.st_mtime == stat_after.st_mtime
    assert stat_before.st_size == stat_after.st_size
    assert bytes_before == bytes_after


def test_search_filters_source_id_and_shared(tmp_path: Path) -> None:
    db = init_library_db(tmp_path / "library.sqlite")
    src_pub = tmp_path / "pub"
    src_priv = tmp_path / "priv"
    src_pub.mkdir()
    src_priv.mkdir()

    add_source(db, source_id="public-src", label="Public", root_path=src_pub, shared=True)
    add_source(db, source_id="private-src", label="Private", root_path=src_priv, shared=False)

    (src_pub / "Shared - Joint Movement.txt").write_text("joint movement kinematics shared study", encoding="utf-8")
    (src_priv / "Private - Joint Movement.txt").write_text("joint movement kinematics private study", encoding="utf-8")

    ingest_sources(db)

    # Filter by source_id
    hits_pub = search_passages(db, "joint movement kinematics", options=SearchOptions(filter_source_id="public-src"))
    assert all(h.source_id == "public-src" for h in hits_pub)

    # Filter by shared_only
    hits_shared = search_passages(db, "joint movement kinematics", options=SearchOptions(shared_only=True))
    assert all(h.source_id == "public-src" for h in hits_shared)
