"""Resolve source/work/page locators against actual indexed records."""

import sqlite3
from pathlib import Path

import pytest
from library.db import init_library_db
from library.search import AmbiguousPassageError
from library.sources import add_source

pytestmark = pytest.mark.unit


@pytest.fixture
def indexed(tmp_path: Path) -> Path:
    root = tmp_path / "books"
    root.mkdir()
    db = init_library_db(tmp_path / "library.sqlite")
    add_source(db, source_id="private", label="Private", root_path=root, shared=False)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO works(id,sha256,title,format,status) VALUES(9,?,'Book','txt','active')", ("a" * 64,))
        conn.execute(
            "INSERT INTO files(work_id,source_id,rel_path,mtime,size,sha256,status,source_root) "
            "VALUES(9,'private','book.txt',0,12,?,'present',?)",
            ("b" * 64, str(root)),
        )
        conn.execute(
            "INSERT INTO passages(id,work_id,page_start,page_end,text,token_estimate) "
            "VALUES(14,9,3,4,'Energy transfer contracts',4)"
        )
    return db


def test_locator_returns_exact_cited_passage(indexed: Path) -> None:
    from library.fetch_locator import fetch_locator

    hit = fetch_locator(indexed, source_id="private", work_id=9, page=4)
    assert (hit.source_id, hit.work_id, hit.passage_id, hit.page_start, hit.page_end) == ("private", 9, 14, 3, 4)
    assert hit.file_sha256 == "b" * 64 and hit.work_sha256 == "a" * 64
    assert hit.text == "Energy transfer contracts"


def test_peer_cannot_discover_private_locator(indexed: Path) -> None:
    from library.fetch_locator import fetch_locator

    with pytest.raises(LookupError):
        fetch_locator(indexed, source_id="private", work_id=9, page=3, shared_only=True)
    with sqlite3.connect(indexed) as conn:
        conn.execute("UPDATE library_sources SET shared=1 WHERE id='private'")
    assert fetch_locator(indexed, source_id="private", work_id=9, page=3, shared_only=True).passage_id == 14


def test_identical_file_copies_do_not_make_a_locator_ambiguous(indexed: Path) -> None:
    from library.fetch_locator import fetch_locator

    with sqlite3.connect(indexed) as conn:
        conn.execute(
            "INSERT INTO files(work_id,source_id,rel_path,mtime,size,sha256,status,source_root) "
            "SELECT work_id,source_id,'copy.txt',mtime,size,sha256,status,source_root FROM files"
        )
    hit = fetch_locator(indexed, source_id="private", work_id=9, page=3)
    assert hit.passage_id == 14 and hit.file_path is None


def test_missing_digest_cannot_generate_a_reference(indexed: Path) -> None:
    from library.fetch_locator import fetch_locator

    with sqlite3.connect(indexed) as conn:
        conn.execute("UPDATE files SET sha256=''")
    with pytest.raises(LookupError, match="digest"):
        fetch_locator(indexed, source_id="private", work_id=9, page=3)


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE library_sources SET status='paused'",
        "UPDATE works SET status='removed'",
        "UPDATE files SET status='removed'",
        "DELETE FROM passages",
    ],
)
def test_inactive_or_missing_records_fail_closed(indexed: Path, mutation: str) -> None:
    from library.fetch_locator import fetch_locator

    with sqlite3.connect(indexed) as conn:
        conn.execute(mutation)
    with pytest.raises(LookupError):
        fetch_locator(indexed, source_id="private", work_id=9, page=3)


@pytest.mark.parametrize(
    "mutation",
    [
        "INSERT INTO passages(id,work_id,page_start,page_end,text,token_estimate) VALUES(15,9,3,4,'Other chunk',3)",
        "INSERT INTO files(work_id,source_id,rel_path,mtime,size,sha256,status,source_root) "
        "SELECT work_id,source_id,'another.txt',mtime,size,replace(sha256,'b','c'),status,source_root FROM files",
    ],
)
def test_ambiguous_page_or_file_does_not_guess(indexed: Path, mutation: str) -> None:
    from library.fetch_locator import fetch_locator

    with sqlite3.connect(indexed) as conn:
        conn.execute(mutation)
    with pytest.raises(AmbiguousPassageError):
        fetch_locator(indexed, source_id="private", work_id=9, page=3)


@pytest.mark.parametrize(
    ("source", "work", "page"),
    [("../private", 9, 3), ("private", 0, 3), ("private", True, 3), ("private", 9, 0), ("private", 9, True)],
)
def test_invalid_locators_do_not_create_a_database(tmp_path: Path, source: str, work: int, page: int) -> None:
    from library.fetch_locator import fetch_locator

    missing = tmp_path / "missing.sqlite"
    with pytest.raises(ValueError):
        fetch_locator(missing, source_id=source, work_id=work, page=page)
    assert not missing.exists()
