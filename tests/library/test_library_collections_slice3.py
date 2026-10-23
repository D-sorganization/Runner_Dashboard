from __future__ import annotations

from pathlib import Path

from library.ingest import ingest_directory
from library.search import SearchOptions, get_work, library_stats, search_passages

from tests.library.conftest import make_text_pdf

GOLF = "hip joint torque golf swing inverse dynamics"
_CFG = "repos:\n  UpstreamDrift:\n    topics: [Golf]\ntopics:\n  Golf:\n    folders: [Golf]\n  Physics:\n    folders: [Physics]\n"


def test_unknown_topic_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LIBRARY_COLLECTIONS_PATH", str(tmp_path / "c.yml"))
    (tmp_path / "c.yml").write_text("repos: {}\ntopics:\n  Golf:\n    folders: [Golf]\n")
    with __import__("pytest").raises(ValueError, match="unknown topic"):
        search_passages(tmp_path / "library.sqlite", "golf", options=SearchOptions(topic="NotARealTopic"))


def test_topic_filter_repo_boost_cap_dup(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LIBRARY_COLLECTIONS_PATH", str(tmp_path / "collections.yml"))
    (tmp_path / "collections.yml").write_text(_CFG)
    root = tmp_path / "lib"
    (root / "Golf").mkdir(parents=True)
    (root / "Physics").mkdir(parents=True)
    (root / "Golf" / "Smith - Golf Science.pdf").write_bytes(make_text_pdf(GOLF, GOLF, "other phase"))
    (root / "Physics" / "Jones - Physics Primer.pdf").write_bytes(make_text_pdf("gravity mass energy"))
    db = tmp_path / "library.sqlite"
    ingest_directory(root, db_path=db)
    golf_hits = search_passages(db, "torque golf", options=SearchOptions(topic="Golf"))
    assert golf_hits and {h.work_id for h in golf_hits} == {golf_hits[0].work_id}
    boosted = search_passages(db, "torque", options=SearchOptions(repo="UpstreamDrift"))
    plain = search_passages(db, "torque", options=SearchOptions())
    assert boosted[0].score >= plain[0].score
    hits = search_passages(db, "hip joint torque golf", budget_tokens=6000)
    assert len([h for h in hits if h.work_id == hits[0].work_id]) <= 2
    assert len({h.text for h in hits}) == len(hits)
    work = get_work(db, hits[0].work_id)
    assert work["topics"] == ["Golf"] and library_stats(db)["works_active"] == 2
