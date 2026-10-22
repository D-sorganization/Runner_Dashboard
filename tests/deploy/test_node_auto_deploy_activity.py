from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

from node_auto_deploy.activity import ActivityUnavailableError, local_activity_count, staff_database_path  # noqa: E402


def _database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE runs (id TEXT, status TEXT);CREATE TABLE messages (id TEXT, author_kind TEXT, delivery TEXT);"
    )
    return connection


def test_activity_count_includes_runs_and_pending_chat_replies(tmp_path: Path) -> None:
    database = tmp_path / "staff.sqlite3"
    connection = _database(database)
    connection.executemany("INSERT INTO runs VALUES (?, ?)", [("r1", "running"), ("r2", "queued"), ("r3", "succeeded")])
    connection.executemany(
        "INSERT INTO messages VALUES (?, ?, ?)",
        [
            ("m1", "role", "streaming"),
            ("m2", "system", "pending"),
            ("m3", "user", "pending"),
            ("m4", "role", "complete"),
        ],
    )
    connection.commit()
    connection.close()
    assert local_activity_count(database) == 4


def test_missing_or_unknown_activity_store_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ActivityUnavailableError, match="could not read"):
        local_activity_count(tmp_path / "missing.sqlite3")
    database = tmp_path / "unknown.sqlite3"
    sqlite3.connect(database).close()
    with pytest.raises(ActivityUnavailableError, match="schema"):
        local_activity_count(database)


def test_database_path_honors_runtime_overrides(tmp_path: Path) -> None:
    assert staff_database_path({"STAFF_RUNS_DB": str(tmp_path / "custom.db")}) == tmp_path / "custom.db"
    assert staff_database_path({"RUNNER_DASHBOARD_CONFIG_DIR": str(tmp_path)}) == tmp_path / "staff_runs.sqlite3"
