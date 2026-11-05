"""Tests for rebuildable context brief (#3940, barb-memory slice 4).

Validates ~2,000-token cap, redaction on every rebuild, advisory-only status
until 5 passing runs, priority sorting, conflict resolution, and privacy checks.
"""

from __future__ import annotations

import pytest
from memory.context_brief import (
    DEFAULT_MAX_BRIEF_TOKENS,
    MIN_PASSING_RUNS,
    BriefRunTracker,
    ContextBrief,
    build_context_brief,
    estimate_tokens,
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


# --- estimate_tokens ---------------------------------------------------------


def test_estimate_tokens_empty_and_text() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("test") == 1
    assert estimate_tokens("a" * 40) == 10


# --- BriefRunTracker ---------------------------------------------------------


def test_brief_run_tracker_advisory_until_5_passes() -> None:
    tracker = BriefRunTracker()
    assert tracker.passing_runs == 0
    assert tracker.is_advisory is True

    for i in range(1, MIN_PASSING_RUNS):
        tracker.record_pass(run_id=f"run_{i}", timestamp=NOW)
        assert tracker.passing_runs == i
        assert tracker.is_advisory is True

    # 5th pass qualifies beyond advisory
    tracker.record_pass(run_id="run_5", timestamp=NOW)
    assert tracker.passing_runs == 5
    assert tracker.is_advisory is False

    # Failure does not increment passing_runs
    tracker.record_failure(run_id="run_6", timestamp=NOW, reason="test failure")
    assert tracker.passing_runs == 5
    assert tracker.total_runs == 6
    assert tracker.is_advisory is False


def test_brief_run_tracker_reset() -> None:
    tracker = BriefRunTracker()
    for _ in range(5):
        tracker.record_pass()
    assert tracker.is_advisory is False
    tracker.reset()
    assert tracker.passing_runs == 0
    assert tracker.is_advisory is True


# --- build_context_brief ------------------------------------------------------


def test_build_context_brief_empty_entries() -> None:
    brief = build_context_brief([], now_iso=NOW)
    assert isinstance(brief, ContextBrief)
    assert brief.entry_ids == ()
    assert brief.is_advisory is True
    assert brief.passing_runs == 0
    assert brief.token_count <= DEFAULT_MAX_BRIEF_TOKENS
    assert "Barb Shared Context Brief" in brief.content
    assert "ADVISORY" in brief.content


def test_build_context_brief_prioritizes_higher_authority() -> None:
    task = _entry(id="mem_task", entry_type="task", body={"summary": "routine task"})
    decision = _entry(id="mem_dec", entry_type="decision", body={"summary": "cap policy"})
    lesson = _entry(id="mem_les", entry_type="lesson", body={"summary": "timeout rule"})

    brief = build_context_brief([task, decision, lesson], now_iso=NOW)
    assert brief.entry_ids == ("mem_dec", "mem_les", "mem_task")
    # Decisions section appears before Lessons, which appears before Tasks
    dec_pos = brief.content.index("## Decisions")
    les_pos = brief.content.index("## Lessons")
    task_pos = brief.content.index("## Active Tasks")
    assert dec_pos < les_pos < task_pos


def test_build_context_brief_resolves_conflicts_via_guard_context() -> None:
    # Conflicting decision and lesson with the same topic
    lesson = _entry(
        id="mem_les",
        entry_type="lesson",
        repo="RD",
        body={"topic": "arming cap", "summary": "cap stance old"},
        created_at="2026-11-03T09:00:00+00:00",
    )
    decision = _entry(
        id="mem_dec",
        entry_type="decision",
        repo="RD",
        body={"topic": "arming cap", "summary": "cap stance new"},
        created_at="2026-11-03T10:00:00+00:00",
    )
    brief = build_context_brief([lesson, decision], now_iso=NOW)
    # Decision wins; suppressed lesson does not appear in brief
    assert "mem_dec" in brief.entry_ids
    assert "mem_les" not in brief.entry_ids
    assert brief.suppressed_count == 1


def test_build_context_brief_drops_expired_entries() -> None:
    expired = _entry(id="mem_exp", expires_at="2026-11-01T00:00:00+00:00")
    fresh = _entry(id="mem_fresh", created_at=NOW)
    brief = build_context_brief([expired, fresh], now_iso=NOW)
    assert brief.entry_ids == ("mem_fresh",)


def test_build_context_brief_caps_tokens_at_max_budget() -> None:
    entries = [
        _entry(id=f"mem_{i}", entry_type="task", body={"summary": f"Task number {i} " + "details " * 15})
        for i in range(40)
    ]
    brief = build_context_brief(entries, now_iso=NOW, max_tokens=150)
    assert brief.token_count <= 150
    assert len(brief.entry_ids) < len(entries)


def test_build_context_brief_redaction_on_every_rebuild() -> None:
    tok = "ghs_" + "B" * 30
    entry_with_token = _entry(
        id="mem_token",
        entry_type="lesson",
        body={"summary": f"Use auth {tok} on host"},
    )
    brief = build_context_brief([entry_with_token], now_iso=NOW)
    assert tok not in brief.content
    assert "[REDACTED_GITHUB_TOKEN]" in brief.content


def test_build_context_brief_with_passing_tracker() -> None:
    tracker = BriefRunTracker()
    for _ in range(5):
        tracker.record_pass()

    brief = build_context_brief([_entry(id="mem_1")], now_iso=NOW, tracker=tracker)
    assert brief.is_advisory is False
    assert brief.passing_runs == 5
    assert "ACTIVE" in brief.content


def test_build_context_brief_caps_digest_summaries_at_400_chars() -> None:
    long_summary = "X" * 600
    entry = _entry(id="mem_long", entry_type="session_digest", body={"summary": long_summary})
    brief = build_context_brief([entry], now_iso=NOW)
    # The summary in the brief must be capped at 400 chars with ellipsis
    assert "X" * 401 not in brief.content
    assert "..." in brief.content


def test_build_context_brief_filters_marge_exclusion() -> None:
    marge_entry = _entry(id="mem_marge", tags=["+marge"], body={"summary": "excluded"})
    clean_entry = _entry(id="mem_clean", source_product="codex", body={"summary": "allowed"})
    brief = build_context_brief([marge_entry, clean_entry], now_iso=NOW)
    assert "mem_marge" not in brief.entry_ids
    assert "mem_clean" in brief.entry_ids
