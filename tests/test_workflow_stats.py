"""Tests for backend/workflow_stats.py — issue #386."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import workflow_stats as ws

# ---------------------------------------------------------------------------
# _parse_ts
# ---------------------------------------------------------------------------


def test_parse_ts_none() -> None:
    assert ws._parse_ts(None) is None


def test_parse_ts_empty() -> None:
    assert ws._parse_ts("") is None


def test_parse_ts_iso_z() -> None:
    dt = ws._parse_ts("2026-04-01T12:00:00Z")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 4


def test_parse_ts_iso_offset() -> None:
    dt = ws._parse_ts("2026-04-01T12:00:00+00:00")
    assert dt is not None


# ---------------------------------------------------------------------------
# _compute_durations
# ---------------------------------------------------------------------------


def _run(created: str, started: str | None, updated: str | None, conclusion: str | None = "success") -> dict:
    return {
        "created_at": created,
        "run_started_at": started,
        "updated_at": updated,
        "conclusion": conclusion,
    }


def test_compute_durations_full_run() -> None:
    run = _run("2026-04-01T10:00:00Z", "2026-04-01T10:01:00Z", "2026-04-01T10:11:00Z")
    queued, duration = ws._compute_durations(run)
    assert queued == pytest.approx(60.0)
    assert duration == pytest.approx(600.0)


def test_compute_durations_no_start_time() -> None:
    run = _run("2026-04-01T10:00:00Z", None, "2026-04-01T10:11:00Z")
    queued, duration = ws._compute_durations(run)
    assert queued is None
    assert duration is None


def test_compute_durations_no_conclusion() -> None:
    run = _run("2026-04-01T10:00:00Z", "2026-04-01T10:01:00Z", "2026-04-01T10:11:00Z", conclusion=None)
    queued, duration = ws._compute_durations(run)
    assert queued == pytest.approx(60.0)
    assert duration is None  # no conclusion → no duration recorded


def test_compute_durations_no_updated() -> None:
    run = _run("2026-04-01T10:00:00Z", "2026-04-01T10:01:00Z", None)
    _, duration = ws._compute_durations(run)
    assert duration is None


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------


def test_init_db_creates_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_file = tmp_path / "test_stats.db"
    monkeypatch.setenv("STATS_DB_PATH", str(db_file))
    result = ws.init_db(db_file)
    assert result == db_file
    assert db_file.exists()


def test_init_db_creates_tables(tmp_path: Path) -> None:
    db_file = tmp_path / "stats.db"
    ws.init_db(db_file)
    with sqlite3.connect(str(db_file)) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "workflow_runs" in tables
    assert "collection_state" in tables


def test_init_db_idempotent(tmp_path: Path) -> None:
    """Calling init_db twice on the same path must not raise."""
    db_file = tmp_path / "stats.db"
    ws.init_db(db_file)
    ws.init_db(db_file)  # should not raise


# ---------------------------------------------------------------------------
# get_summary — requires an initialized DB
# ---------------------------------------------------------------------------


def test_get_summary_empty_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """get_summary returns a dict with 'rows' key; rows is empty for an empty DB."""
    db_file = tmp_path / "stats.db"
    monkeypatch.setenv("STATS_DB_PATH", str(db_file))
    ws.init_db(db_file)
    result = ws.get_summary()
    assert isinstance(result, dict)
    assert "rows" in result
    assert result["rows"] == []


def test_get_summary_returns_rows_with_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_file = tmp_path / "stats.db"
    monkeypatch.setenv("STATS_DB_PATH", str(db_file))
    ws.init_db(db_file)
    # Insert a row manually
    with sqlite3.connect(str(db_file)) as conn:
        conn.execute(
            """
            INSERT INTO workflow_runs
            (run_id, repo, workflow_name, workflow_id, head_branch, event,
             status, conclusion, created_at, run_started_at, updated_at,
             queued_seconds, duration_seconds, runner_label, inserted_at)
            VALUES (1, 'my-repo', 'CI', 100, 'main', 'push',
                    'completed', 'success', '2026-04-01T10:00:00Z',
                    '2026-04-01T10:01:00Z', '2026-04-01T10:11:00Z',
                    60.0, 600.0, 'd-sorg-fleet', '2026-04-01T10:11:00Z')
            """
        )
    result = ws.get_summary(days=9999)  # large window so the row is included
    assert isinstance(result, dict)
    rows = result["rows"]
    assert len(rows) >= 1
    row = rows[0]
    assert "repo" in row
    assert "p50_duration" in row or "count" in row


def test_get_summary_group_by_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """group_by='repo' must be accepted and return rows."""
    db_file = tmp_path / "stats.db"
    monkeypatch.setenv("STATS_DB_PATH", str(db_file))
    ws.init_db(db_file)
    result = ws.get_summary(group_by="repo")
    assert isinstance(result, dict)
    assert result["group_by"] == "repo"


# ---------------------------------------------------------------------------
# get_timeseries — empty DB
# ---------------------------------------------------------------------------


def test_get_timeseries_empty_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """get_timeseries returns a dict with 'series' key; series is empty for empty DB."""
    db_file = tmp_path / "stats.db"
    monkeypatch.setenv("STATS_DB_PATH", str(db_file))
    ws.init_db(db_file)
    result = ws.get_timeseries(repo="any-repo", workflow_name="CI")
    assert isinstance(result, dict)
    assert "series" in result
    assert result["series"] == []
