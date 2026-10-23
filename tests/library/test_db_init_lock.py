from __future__ import annotations

import sqlite3

from library.db import init_library_db


def test_repeated_init_is_read_only_while_another_writer_holds_lock(tmp_path) -> None:
    db_path = init_library_db(tmp_path / "library.sqlite")
    writer = sqlite3.connect(db_path, timeout=0.01)
    writer.execute("BEGIN EXCLUSIVE")
    try:
        assert init_library_db(db_path) == db_path
    finally:
        writer.rollback()
        writer.close()


def test_replaced_database_gets_initialized(tmp_path) -> None:
    db_path = init_library_db(tmp_path / "library.sqlite")
    old_path = db_path.with_suffix(".old")
    db_path.rename(old_path)
    db_path.touch()

    init_library_db(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='library_sources'").fetchone()
