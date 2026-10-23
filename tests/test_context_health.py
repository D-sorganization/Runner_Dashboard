"""Tests for RD #2238: Health checks for library pool, lessons and context pack."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from context_health import (
    ContextHealthResult,
    check_all_context_health,
    check_context_pack_health,
    check_library_health,
    check_memory_health,
    check_pool_health,
    clear_context_health_cache,
    reset_transition_tracker,
)
from fleet_events import get_event_store

_SCHEMA_SOURCES = (
    "CREATE TABLE library_sources "
    "(id TEXT PRIMARY KEY, label TEXT, root_path TEXT, shared INT, last_ingest_at TEXT, status TEXT)"
)
_SCHEMA_MEMORY = (
    "CREATE TABLE memory_entries "
    "(id TEXT PRIMARY KEY, entry_type TEXT, author_principal TEXT, source_product TEXT, "
    "tags_json TEXT, body_json TEXT, created_at TEXT, expires_at TEXT, supersedes_id TEXT, superseded_by TEXT)"
)


@pytest.fixture(autouse=True)
def _reset_health_state() -> None:
    clear_context_health_cache()
    reset_transition_tracker()


def test_library_health_not_installed(tmp_path: Path) -> None:
    res = check_library_health(db_path=tmp_path / "missing.sqlite")
    assert res.name == "library" and res.status == "not_installed"


def test_library_health_ok_and_degraded(tmp_path: Path) -> None:
    db_path = tmp_path / "library.sqlite"
    source_dir = tmp_path / "docs"
    source_dir.mkdir()

    with sqlite3.connect(db_path) as conn:
        conn.execute(_SCHEMA_SOURCES)
        conn.execute(
            "INSERT INTO library_sources VALUES ('d1', 'Docs', ?, 0, ?, 'active')",
            (str(source_dir), datetime.now(UTC).isoformat()),
        )

    # ok
    assert check_library_health(db_path=db_path).status == "ok"

    # missing folder -> degraded
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE library_sources SET root_path=? WHERE id='d1'", (str(tmp_path / "removed"),))
    assert check_library_health(db_path=db_path).status == "degraded"

    # marked unavailable -> degraded
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE library_sources SET root_path=?, status='unavailable' WHERE id='d1'", (str(source_dir),))
    assert check_library_health(db_path=db_path).status == "degraded"

    # stale ingest -> degraded
    stale = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE library_sources SET status='active', last_ingest_at=? WHERE id='d1'", (stale,))
    assert check_library_health(db_path=db_path, max_ingest_age_seconds=7 * 86400).status == "degraded"


def test_pool_and_memory_health_not_installed(tmp_path: Path) -> None:
    assert check_pool_health().status == "not_installed"
    assert check_memory_health(db_path=tmp_path / "missing.sqlite").status == "not_installed"


def test_memory_health_ok_and_degraded(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(_SCHEMA_MEMORY)
    assert check_memory_health(db_path=db_path).status == "ok"

    # expired lesson
    past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO memory_entries VALUES ('l1', 'lesson', 'a', 'c', '[\"active\"]', '{}', ?, ?, NULL, NULL)",
            (past, past),
        )
    assert check_memory_health(db_path=db_path).status == "degraded"

    # quarantined lesson
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM memory_entries")
        conn.execute(
            "INSERT INTO memory_entries VALUES "
            "('l2', 'lesson', 'a', 'c', '[\"quarantine\"]', '{}', ?, NULL, NULL, NULL)",
            (past,),
        )
    assert check_memory_health(db_path=db_path).status == "degraded"


def test_context_pack_health() -> None:
    with patch("staff.standards_registry.load_fleet_standards") as mock_load:
        mock_load.return_value = MagicMock(
            source_path="<builtin>", digest="abc", name="fleet-standards", version="1.0.0"
        )
        assert check_context_pack_health().status == "degraded"

        mock_load.return_value = MagicMock(
            source_path="/std/FLEET.md", digest="abc", name="fleet-standards", version="1.1.0"
        )
        assert check_context_pack_health().status == "ok"


def test_context_health_caching_and_read_only(tmp_path: Path) -> None:
    db_path = tmp_path / "library.sqlite"
    source_dir = tmp_path / "docs"
    source_dir.mkdir()
    with sqlite3.connect(db_path) as conn:
        conn.execute(_SCHEMA_SOURCES)
        conn.execute(
            "INSERT INTO library_sources VALUES ('d1', 'Docs', ?, 0, ?, 'active')",
            (str(source_dir), datetime.now(UTC).isoformat()),
        )

    call_count = 0
    orig = sqlite3.connect

    def spy(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        nonlocal call_count
        call_count += 1
        return orig(*args, **kwargs)

    with patch("sqlite3.connect", side_effect=spy):
        res1 = check_all_context_health(library_db=db_path)
        recorded = call_count
        res2 = check_all_context_health(library_db=db_path)
        assert call_count == recorded and res1 == res2


def test_event_emission_on_transition(tmp_path: Path) -> None:
    db_path = tmp_path / "library.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(_SCHEMA_SOURCES)
        conn.execute("INSERT INTO library_sources VALUES ('d1', 'Docs', '/missing', 0, NULL, 'active')")

    store = get_event_store()
    with patch(
        "context_health.check_context_pack_health", return_value=ContextHealthResult("context_pack", "ok", "ok", "")
    ):
        initial = len([e for e in store.recent(100) if "library" in e.title])
        res1 = check_all_context_health(library_db=db_path)
        assert res1["library"]["status"] == "degraded"
        events = [e for e in store.recent(100) if "library" in e.title]
        assert len(events) == initial + 1 and events[-1].kind == "context_health"

        clear_context_health_cache()
        assert check_all_context_health(library_db=db_path)["library"]["status"] == "degraded"
        assert len([e for e in store.recent(100) if "library" in e.title]) == initial + 1


@pytest.mark.asyncio
async def test_diagnostics_summary_and_context_endpoint() -> None:
    from routers.diagnostics import get_context_health, get_diagnostics_summary

    ctx = await get_context_health()
    assert isinstance(ctx, dict) and "library" in ctx and "context_pack" in ctx

    summary = await get_diagnostics_summary()
    assert "context" in summary and "library" in summary["context"]
