"""Tests for the CI-fix red-armed PR sweep (split from ci_fix_dispatch, Issue #2766 items 2/5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.ci_fix_dispatch import CIFixLockManager, record_ci_fix_audit
from backend.ci_fix_sweep import count_stale_red_prs_without_fix_attempts, select_sweep_candidates


def test_sweep_picks_red_armed_pr_with_no_lock() -> None:
    now = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
    locks = CIFixLockManager()
    prs = [
        {
            "repo": "Runner_Dashboard",
            "number": 101,
            "is_draft": False,
            "auto_merge_armed": True,
            "red_since": (now - timedelta(minutes=45)).isoformat(),
            "labels": [],
        },
        # Too recent: under the 30-minute default threshold.
        {
            "repo": "Runner_Dashboard",
            "number": 102,
            "is_draft": False,
            "auto_merge_armed": True,
            "red_since": (now - timedelta(minutes=5)).isoformat(),
            "labels": [],
        },
        # Locked: a fix session is already active.
        {
            "repo": "Runner_Dashboard",
            "number": 103,
            "is_draft": False,
            "auto_merge_armed": True,
            "red_since": (now - timedelta(minutes=45)).isoformat(),
            "labels": [],
        },
        # Draft: never eligible.
        {
            "repo": "Runner_Dashboard",
            "number": 104,
            "is_draft": True,
            "auto_merge_armed": True,
            "red_since": (now - timedelta(minutes=45)).isoformat(),
            "labels": [],
        },
    ]
    locks.acquire("Runner_Dashboard", 103, session_id="existing")
    candidates = select_sweep_candidates(prs, now=now, lock_manager=locks)
    assert [pr["number"] for pr in candidates] == [101]


def test_sweep_gives_consolidation_prs_a_longer_grace_period() -> None:
    now = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
    locks = CIFixLockManager()
    pr = {
        "repo": "Runner_Dashboard",
        "number": 200,
        "is_draft": False,
        "auto_merge_armed": True,
        "red_since": (now - timedelta(minutes=45)).isoformat(),
        "labels": ["consolidation"],
    }
    # 45 minutes red is past the default 30-minute threshold but under the 2h consolidation one.
    assert select_sweep_candidates([pr], now=now, lock_manager=locks) == []
    pr["red_since"] = (now - timedelta(hours=3)).isoformat()
    assert select_sweep_candidates([pr], now=now, lock_manager=locks) == [pr]


def test_count_stale_red_prs_without_fix_attempts(tmp_path: Path) -> None:
    now = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
    audit_file = tmp_path / "audit.json"
    record_ci_fix_audit(
        repo="Runner_Dashboard",
        pr_number=301,
        workflow_name="CI",
        run_id=1,
        failure_type="test",
        provider="claude_code_cli",
        model="m",
        attempt_number=1,
        cost_budget=1.0,
        audit_file=audit_file,
    )
    prs = [
        # Red >24h, never attempted: counts.
        {"repo": "Runner_Dashboard", "number": 300, "red_since": (now - timedelta(hours=30)).isoformat()},
        # Red >24h, already has a fix attempt: does not count.
        {"repo": "Runner_Dashboard", "number": 301, "red_since": (now - timedelta(hours=30)).isoformat()},
        # Red, but under 24h: does not count.
        {"repo": "Runner_Dashboard", "number": 302, "red_since": (now - timedelta(hours=2)).isoformat()},
        # Not red at all: does not count.
        {"repo": "Runner_Dashboard", "number": 303, "red_since": None},
    ]
    assert count_stale_red_prs_without_fix_attempts(prs, now=now, audit_file=audit_file) == 1
