"""Tests for backend.agent_active_workdirs (#4440); sqlite files live under tmp_path only."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend import agent_active_workdirs as mod


def _make_db(db: Path, rows: list[tuple[str, str]]) -> None:
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE runs (status TEXT, workdir TEXT)")
    conn.executemany("INSERT INTO runs (status, workdir) VALUES (?, ?)", rows)
    conn.commit()
    conn.close()


def test_db_path_prefers_staff_runs_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "explicit.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(target))
    assert mod.staff_runs_db_path() == target


def test_db_path_uses_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("STAFF_RUNS_DB", raising=False)
    monkeypatch.setenv("RUNNER_DASHBOARD_CONFIG_DIR", str(tmp_path))
    assert mod.staff_runs_db_path() == tmp_path / "staff_runs.sqlite3"


def test_db_path_default_under_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("STAFF_RUNS_DB", raising=False)
    monkeypatch.delenv("RUNNER_DASHBOARD_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert mod.staff_runs_db_path().name == "staff_runs.sqlite3"


def test_missing_db_means_no_active_workdirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "absent.sqlite3"))
    assert mod.active_staff_workdirs() == set()


def test_only_active_statuses_with_workdir_are_returned(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = tmp_path / "runs.sqlite3"
    _make_db(
        db,
        [
            ("queued", str(tmp_path / "a")),
            ("preparing", str(tmp_path / "b")),
            ("running", str(tmp_path / "c")),
            ("done", str(tmp_path / "d")),
            ("running", ""),
        ],
    )
    monkeypatch.setenv("STAFF_RUNS_DB", str(db))
    assert mod.active_staff_workdirs() == {(tmp_path / n).resolve() for n in "abc"}


def test_unreadable_store_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = tmp_path / "bad.sqlite3"
    db.write_bytes(b"this is not a sqlite database" * 10)
    monkeypatch.setenv("STAFF_RUNS_DB", str(db))
    assert mod.active_staff_workdirs() is None


def test_db_without_runs_table_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = tmp_path / "empty.sqlite3"
    sqlite3.connect(db).close()
    monkeypatch.setenv("STAFF_RUNS_DB", str(db))
    assert mod.active_staff_workdirs() is None
