"""Connection lifetime contracts for the extracted statistics database boundary."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from workflow_stats_db import connect_stats_db


@pytest.mark.parametrize("raise_inside", [False, True])
def test_connection_is_closed_on_normal_and_exceptional_exit(tmp_path: Path, raise_inside: bool) -> None:
    connection = None
    try:
        with connect_stats_db(tmp_path / "stats.db") as connection:
            assert connection.execute("SELECT COUNT(*) FROM workflow_jobs").fetchone()[0] == 0
            if raise_inside:
                raise ValueError("synthetic consumer failure")
    except ValueError:
        assert raise_inside
    assert connection is not None
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
