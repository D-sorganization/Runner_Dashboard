from __future__ import annotations

import hashlib
from pathlib import Path

from library.db import init_library_db
from library.retrieval_ledger import record_retrievals
from library.verify import verify_content


def _indexed(tmp_path: Path) -> tuple[Path, str]:
    db = init_library_db(tmp_path / "library.sqlite")
    source = tmp_path / "source" / "paper.md"
    source.parent.mkdir()
    source.write_text("Rotational power improves turbine efficiency.", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO works(id,sha256,author,title,format) VALUES(1,?,?,?,?)",
            (digest, "Doe", "Rotational Power", "md"),
        )
        conn.execute(
            "INSERT INTO files(id,work_id,source_id,rel_path,mtime,size,sha256,source_root) "
            "VALUES(1,1,'shared','paper.md',0,1,?,?)",
            (digest, str(source.parent)),
        )
        conn.execute(
            "INSERT INTO passages(id,work_id,page_start,page_end,section,text,token_estimate) "
            "VALUES(19,1,7,8,'Efficiency','Rotational power improves turbine efficiency.',6)"
        )
    return db, digest


def test_verify_resolves_canonical_and_lib_citations_by_hash_after_move(tmp_path: Path) -> None:
    db, digest = _indexed(tmp_path)
    (tmp_path / "source").rename(tmp_path / "moved")
    content = (
        f"Rotational power improves turbine efficiency. "
        f"[ref:desk:shared:w1:p19#sha256={digest[:8]}:p.7-8] "
        f"lib://shared/w1/p19@{digest[:8]}#p7-8"
    )

    result = verify_content(db, content)

    assert [item["verified"] for item in result["citations"]] == [True, True]
    assert all(item["work"]["title"] == "Rotational Power" for item in result["citations"])


def test_verify_rejects_hash_page_and_claim_mismatches(tmp_path: Path) -> None:
    db, digest = _indexed(tmp_path)
    content = "Dinosaurs live on Mars. [ref:desk:shared:w1:p19#sha256=00000000:p.2-4]"

    result = verify_content(db, content)

    assert result["citations"][0]["status"] == "sha_mismatch"
    content = f"Dinosaurs live on Mars. [ref:desk:shared:w1:p19#sha256={digest[:8]}:p.2-4]"
    assert verify_content(db, content)["citations"][0]["status"] == "page_range_mismatch"
    content = f"Dinosaurs live on Mars. [ref:desk:shared:w1:p19#sha256={digest[:8]}:p.7-8]"
    assert verify_content(db, content)["citations"][0]["status"] == "misappropriated_reference"
    content = f"This function is not safe. [ref:desk:shared:w1:p19#sha256={digest[:8]}:p.7-8]"
    assert verify_content(db, content)["citations"][0]["status"] == "misappropriated_reference"
    claim = "Rotational power does not improve turbine efficiency."
    content = f"{claim} [ref:desk:shared:w1:p19#sha256={digest[:8]}:p.7-8]"
    assert verify_content(db, content)["citations"][0]["status"] == "misappropriated_reference"


def test_verify_reports_broken_relative_links(tmp_path: Path) -> None:
    db, _ = _indexed(tmp_path)
    result = verify_content(db, "See [missing](docs/absent.md#overview).", repo_root=tmp_path)
    assert result["links"] == [{"url": "docs/absent.md#overview", "verified": False, "status": "broken_link"}]


def test_verify_checks_markdown_heading_anchors_and_legacy_markers(tmp_path: Path) -> None:
    db, _ = _indexed(tmp_path)
    target = tmp_path / "guide.md"
    target.write_text("# Getting Started\n", encoding="utf-8")

    result = verify_content(
        db,
        "[valid](guide.md#getting-started) [invalid](guide.md#missing) (work_id=1)",
        repo_root=tmp_path,
    )

    assert [link["verified"] for link in result["links"]] == [True, False]
    assert result["citations"][0]["status"] == "legacy_unverifiable"


def test_verify_requires_run_ledger_membership(tmp_path: Path) -> None:
    db, digest = _indexed(tmp_path)
    cite = f"[ref:desk:shared:w1:p19#sha256={digest[:8]}:p.7-8]"

    result = verify_content(db, cite, run_id="run-1")

    assert result["citations"][0]["status"] == "unretrieved_reference"
    record_retrievals(
        db,
        run_id="run-1",
        references=[
            {
                "node": "desk",
                "source_id": "shared",
                "work_id": 1,
                "passage_id": 19,
                "file_sha256": digest,
                "page_start": 7,
                "page_end": 8,
            }
        ],
    )
    assert verify_content(db, cite, run_id="run-1")["citations"][0]["verified"] is True


def test_verify_requires_exact_node_in_run_ledger(tmp_path: Path) -> None:
    db, digest = _indexed(tmp_path)
    record_retrievals(
        db,
        run_id="run-1",
        references=[
            {
                "node": "desk",
                "source_id": "shared",
                "work_id": 1,
                "passage_id": 19,
                "file_sha256": digest,
                "page_start": 7,
                "page_end": 8,
            }
        ],
    )
    cite = f"[ref:other-node:shared:w1:p19#sha256={digest[:8]}:p.7-8]"

    result = verify_content(db, f"Rotational power improves turbine efficiency. {cite}", run_id="run-1")

    assert result["citations"][0]["status"] == "node_mismatch"
    assert result["citations"][0]["verified"] is False


def test_valid_reference_cannot_hide_a_malformed_reference(tmp_path: Path) -> None:
    db, digest = _indexed(tmp_path)
    cite = f"[ref:desk:shared:w1:p19#sha256={digest[:8]}:p.7-8]"
    invalid = "[ref:desk:shared:w1:p19#sha256=typo:p.7-8]"

    result = verify_content(db, f"Rotational power improves turbine efficiency. {cite}\n{invalid}")

    assert result["verified"] is False
    assert [item["verified"] for item in result["citations"]] == [True, False]
    assert result["citations"][1]["status"] == "invalid_citation"
