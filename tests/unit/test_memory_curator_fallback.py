"""Tests for the plain-code curator-outage fallback (#3941, barb-memory slice 5a).

Depends on slice 1 (context_contract.py, #3937) and slice 4 (context_brief.py,
#3940). Validates: staleness detection fails closed (no heartbeat, failed last
run, unparsable/naive timestamp, timestamp older than the window all count as
"curator down"); the fallback only engages when the curator is actually
stale; it never fabricates a brief when there are no eligible entries; and
when it does engage, it serves a real, correctly-redacted, correctly-guarded
brief built only from deterministic plain code (``build_context_brief``).
"""

from __future__ import annotations

import pytest
from memory.context_brief import BriefRunTracker
from memory.context_curator_fallback import (
    DEFAULT_MAX_STALENESS_SECONDS,
    CuratorHeartbeat,
    build_fallback_brief,
    curator_is_stale,
)
from memory.models import MemoryEntry

pytestmark = pytest.mark.unit

NOW = "2026-11-03T12:00:00+00:00"


def _entry(**kwargs: object) -> MemoryEntry:
    base: dict[str, object] = {
        "id": "mem_1",
        "entry_type": "task",
        "author_principal": "agent:claude-a",
        "source_product": "codex",
        "tags": [],
        "body": {"summary": "fleet checkpoint"},
        "created_at": "2026-11-03T11:00:00+00:00",
    }
    base.update(kwargs)
    return MemoryEntry(**base)  # type: ignore[arg-type]


# --- curator_is_stale ---------------------------------------------------------


def test_curator_fresh_within_window_is_not_stale() -> None:
    heartbeat = CuratorHeartbeat(last_refreshed_at="2026-11-03T11:58:00+00:00")
    assert curator_is_stale(heartbeat, now_iso=NOW) is False


def test_curator_older_than_window_is_stale() -> None:
    heartbeat = CuratorHeartbeat(last_refreshed_at="2026-11-03T11:00:00+00:00")
    assert curator_is_stale(heartbeat, now_iso=NOW, max_staleness_seconds=600) is True


def test_no_heartbeat_ever_recorded_is_stale() -> None:
    assert curator_is_stale(CuratorHeartbeat(last_refreshed_at=None), now_iso=NOW) is True


def test_failed_last_run_is_stale_even_if_recent() -> None:
    heartbeat = CuratorHeartbeat(last_refreshed_at="2026-11-03T11:59:59+00:00", last_run_ok=False)
    assert curator_is_stale(heartbeat, now_iso=NOW) is True


def test_unparsable_timestamp_fails_closed_to_stale() -> None:
    heartbeat = CuratorHeartbeat(last_refreshed_at="not-a-timestamp")
    assert curator_is_stale(heartbeat, now_iso=NOW) is True


def test_naive_timestamp_fails_closed_to_stale() -> None:
    heartbeat = CuratorHeartbeat(last_refreshed_at="2026-11-03T11:59:00")
    assert curator_is_stale(heartbeat, now_iso=NOW) is True


def test_default_staleness_window_is_fifteen_minutes() -> None:
    assert DEFAULT_MAX_STALENESS_SECONDS == 15 * 60


# --- build_fallback_brief -----------------------------------------------------


def test_fallback_not_used_when_curator_is_fresh() -> None:
    heartbeat = CuratorHeartbeat(last_refreshed_at="2026-11-03T11:58:00+00:00")
    result = build_fallback_brief([_entry()], now_iso=NOW, heartbeat=heartbeat)
    assert result.used_fallback is False
    assert result.brief is None
    assert result.reason == "curator_fresh"


def test_fallback_serves_brief_when_curator_stale_and_entries_eligible() -> None:
    heartbeat = CuratorHeartbeat(last_refreshed_at=None)
    tracker = BriefRunTracker()
    result = build_fallback_brief([_entry()], now_iso=NOW, heartbeat=heartbeat, tracker=tracker)
    assert result.used_fallback is True
    assert result.brief is not None
    assert result.brief.entry_ids == ("mem_1",)
    assert "fleet checkpoint" in result.brief.content
    assert result.reason == "curator_stale_fallback_served"


def test_fallback_never_fabricates_brief_with_no_eligible_entries() -> None:
    heartbeat = CuratorHeartbeat(last_refreshed_at=None)
    result = build_fallback_brief([], now_iso=NOW, heartbeat=heartbeat)
    assert result.used_fallback is True
    assert result.brief is None
    assert result.reason == "no_eligible_entries"


def test_fallback_composes_privacy_guard_never_leaks_marge_entry() -> None:
    marge_entry = _entry(
        id="mem_marge",
        source_product="gmail",
        body={"summary": "marge personal data"},
    )
    heartbeat = CuratorHeartbeat(last_refreshed_at=None)
    result = build_fallback_brief([marge_entry], now_iso=NOW, heartbeat=heartbeat)
    assert result.used_fallback is True
    assert result.brief is None
    assert result.reason == "no_eligible_entries"


def test_fallback_respects_custom_staleness_window() -> None:
    heartbeat = CuratorHeartbeat(last_refreshed_at="2026-11-03T11:50:00+00:00")
    fresh_result = build_fallback_brief([_entry()], now_iso=NOW, heartbeat=heartbeat, max_staleness_seconds=3600)
    assert fresh_result.used_fallback is False
    stale_result = build_fallback_brief([_entry()], now_iso=NOW, heartbeat=heartbeat, max_staleness_seconds=60)
    assert stale_result.used_fallback is True
