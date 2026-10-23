from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from library.db import init_library_db
from library.submissions import create_submission, list_submissions

_PAYLOAD = {
    "doi": "10.1000/xyz",
    "title": "Golf Biomechanics",
    "authors": ["Smith, J."],
    "reason": "cited in UpstreamDrift#42",
    "requesting_ref": "UpstreamDrift#42",
}


def test_create_and_list_pending(tmp_path: Path) -> None:
    db = init_library_db(tmp_path / "library.sqlite")
    row = create_submission(db, principal_id="agent-1", payload=_PAYLOAD)
    assert row["status"] == "pending"
    items = list_submissions(db)
    assert len(items) == 1 and items[0]["payload"]["doi"] == _PAYLOAD["doi"]


def test_duplicate_doi_against_work(tmp_path: Path) -> None:
    db = init_library_db(tmp_path / "library.sqlite")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO works (sha256, title, format, status, doi) VALUES ('a', 'Existing', 'pdf', 'active', ?)",
            (_PAYLOAD["doi"],),
        )
        conn.commit()
    row = create_submission(db, principal_id="agent-1", payload=_PAYLOAD)
    assert row["status"] == "duplicate"


def test_duplicate_pending_submission(tmp_path: Path) -> None:
    db = init_library_db(tmp_path / "library.sqlite")
    create_submission(db, principal_id="agent-1", payload=_PAYLOAD)
    row = create_submission(db, principal_id="agent-2", payload={**_PAYLOAD, "reason": "also needed"})
    assert row["status"] == "duplicate"
