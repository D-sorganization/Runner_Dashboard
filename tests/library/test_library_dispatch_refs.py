from __future__ import annotations

from pathlib import Path

from library.dispatch_refs import attach_library_references, reference_query_from_task
from library.ingest import ingest_directory
from staff.plan import RunRequest

from tests.library.conftest import make_text_pdf

GOLF = "hip joint torque golf swing"


def test_reference_query_from_task() -> None:
    assert "simulation" in reference_query_from_task(RunRequest(role="r", repo="X", prompt="  simulation work  "))
    assert reference_query_from_task(RunRequest(role="r", repo="X", issue=12)) == "issue #12"


def test_attach_library_references(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "collections.yml"
    cfg.write_text("repos:\n  UpstreamDrift:\n    topics: [Golf]\ntopics:\n  Golf:\n    folders: [Golf]\n")
    monkeypatch.setenv("LIBRARY_COLLECTIONS_PATH", str(cfg))
    root = tmp_path / "lib"
    (root / "Golf").mkdir(parents=True)
    (root / "Golf" / "Smith - Golf.pdf").write_bytes(make_text_pdf(GOLF))
    db = tmp_path / "library.sqlite"
    ingest_directory(root, db_path=db)
    block, ids = attach_library_references(db, repo="UpstreamDrift", query="hip joint torque golf")
    assert ids and "Library references" in block and "Smith" in block
