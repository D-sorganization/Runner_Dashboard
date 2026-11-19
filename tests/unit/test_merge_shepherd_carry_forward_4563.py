"""PASS carry-forward across main-only merges and the 1-retry fix cap (#4563)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from coordination.merge_shepherd_carry_forward import (
    MAX_FIX_PASSES,
    diff_fingerprint,
    fix_pass_decision,
    pass_with_carry_forward,
)
from coordination.merge_shepherd_exact_head import HeadReviewPass

OLD = "a" * 40
NEW = "b" * 40
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def _pass(sha: str, *, at: str = "2026-11-19T10:00:00+00:00") -> HeadReviewPass:
    return HeadReviewPass(pr=7, head_sha=sha, verdict="approve", recorded_at=at, reviewer_model="m")


def test_fingerprint_is_order_independent_and_content_sensitive() -> None:
    a = diff_fingerprint([("x.py", "1"), ("y.py", "2")])
    assert a == diff_fingerprint([("y.py", "2"), ("x.py", "1")])
    assert a != diff_fingerprint([("x.py", "1"), ("y.py", "3")])
    assert a != diff_fingerprint([("x.py", "1")])


def test_fingerprint_of_empty_diff_is_empty_string() -> None:
    assert diff_fingerprint([]) == ""


def test_exact_head_pass_still_wins() -> None:
    ok, why = pass_with_carry_forward(
        [_pass(NEW)], pr=7, head_sha=NEW, head_fingerprint="f", pass_fingerprints={}, now=NOW
    )
    assert (ok, why) == (True, "exact_head_pass")


def test_carries_forward_when_diff_identical() -> None:
    fp = diff_fingerprint([("x.py", "1")])
    ok, why = pass_with_carry_forward(
        [_pass(OLD)], pr=7, head_sha=NEW, head_fingerprint=fp, pass_fingerprints={OLD: fp}, now=NOW
    )
    assert (ok, why) == (True, "carried_forward_pass")


def test_real_code_change_requires_fresh_review() -> None:
    old_fp = diff_fingerprint([("x.py", "1")])
    new_fp = diff_fingerprint([("x.py", "2")])
    ok, why = pass_with_carry_forward(
        [_pass(OLD)], pr=7, head_sha=NEW, head_fingerprint=new_fp, pass_fingerprints={OLD: old_fp}, now=NOW
    )
    assert (ok, why) == (False, "missing_pass_at_head")


def test_empty_fingerprint_never_carries() -> None:
    ok, _ = pass_with_carry_forward(
        [_pass(OLD)], pr=7, head_sha=NEW, head_fingerprint="", pass_fingerprints={OLD: ""}, now=NOW
    )
    assert ok is False


def test_unknown_pass_fingerprint_does_not_carry() -> None:
    ok, _ = pass_with_carry_forward(
        [_pass(OLD)], pr=7, head_sha=NEW, head_fingerprint="f", pass_fingerprints={}, now=NOW
    )
    assert ok is False


def test_stale_pass_does_not_carry() -> None:
    ok, why = pass_with_carry_forward(
        [_pass(OLD, at="2026-11-01T00:00:00+00:00")],
        pr=7,
        head_sha=NEW,
        head_fingerprint="f",
        pass_fingerprints={OLD: "f"},
        now=NOW,
    )
    assert (ok, why) == (False, "missing_pass_at_head")


def test_other_pr_pass_does_not_carry() -> None:
    other = HeadReviewPass(pr=8, head_sha=OLD, verdict="approve", recorded_at="2026-11-19T10:00:00+00:00")
    ok, _ = pass_with_carry_forward(
        [other], pr=7, head_sha=NEW, head_fingerprint="f", pass_fingerprints={OLD: "f"}, now=NOW
    )
    assert ok is False


def test_fix_cap_allows_one_pass_then_escalates() -> None:
    assert MAX_FIX_PASSES == 1
    assert fix_pass_decision(failed_fix_passes=0) == "fix"
    assert fix_pass_decision(failed_fix_passes=1) == "escalate"
    assert fix_pass_decision(failed_fix_passes=5) == "escalate"


def test_fix_cap_rejects_negative_counter() -> None:
    with pytest.raises(ValueError, match="failed_fix_passes"):
        fix_pass_decision(failed_fix_passes=-1)
