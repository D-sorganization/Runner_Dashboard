"""PASS carry-forward across main-only merges and the 1-retry fix cap (#4563)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from coordination.merge_shepherd import (
    IneligibleReason,
    PRCandidate,
    ShepherdAction,
    evaluate_pr_shepherd_eligibility,
)
from coordination.merge_shepherd_carry_forward import (
    MAX_FIX_PASSES,
    diff_fingerprint,
    failed_fix_passes_from_reviews,
    fix_pass_decision,
    pass_with_carry_forward,
)
from coordination.merge_shepherd_exact_head import HeadReviewPass
from coordination.merge_shepherd_pass import node_to_candidate

OLD = "a" * 40
NEW = "b" * 40
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def _pass(sha: str, *, at: str = "2026-11-19T10:00:00+00:00") -> HeadReviewPass:
    return HeadReviewPass(pr=7, head_sha=sha, verdict="approve", recorded_at=at, reviewer_model="m")


def test_fingerprint_is_order_independent_and_content_sensitive() -> None:
    a = diff_fingerprint([("x.py", "100644", "1"), ("y.py", "100644", "2")])
    assert a == diff_fingerprint([("y.py", "100644", "2"), ("x.py", "100644", "1")])
    assert a != diff_fingerprint([("x.py", "100644", "1"), ("y.py", "100644", "3")])
    assert a != diff_fingerprint([("x.py", "100644", "1")])


def test_fingerprint_of_empty_diff_is_empty_string() -> None:
    assert diff_fingerprint([]) == ""


def test_exact_head_pass_still_wins() -> None:
    ok, why = pass_with_carry_forward(
        [_pass(NEW)], pr=7, head_sha=NEW, head_fingerprint="f", pass_fingerprints={}, now=NOW
    )
    assert (ok, why) == (True, "exact_head_pass")


def test_carries_forward_when_diff_identical() -> None:
    fp = diff_fingerprint([("x.py", "100644", "1")])
    ok, why = pass_with_carry_forward(
        [_pass(OLD)], pr=7, head_sha=NEW, head_fingerprint=fp, pass_fingerprints={OLD: fp}, now=NOW
    )
    assert (ok, why) == (True, "carried_forward_pass")


def test_real_code_change_requires_fresh_review() -> None:
    old_fp = diff_fingerprint([("x.py", "100644", "1")])
    new_fp = diff_fingerprint([("x.py", "100644", "2")])
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


def test_fingerprint_changes_when_only_git_mode_changes() -> None:
    plain = diff_fingerprint([("run.sh", "100644", "abc")])
    executable = diff_fingerprint([("run.sh", "100755", "abc")])
    assert plain != executable


def test_mode_only_change_requires_fresh_review() -> None:
    old_fp = diff_fingerprint([("run.sh", "100644", "abc")])
    new_fp = diff_fingerprint([("run.sh", "100755", "abc")])
    ok, why = pass_with_carry_forward(
        [_pass(OLD)], pr=7, head_sha=NEW, head_fingerprint=new_fp, pass_fingerprints={OLD: old_fp}, now=NOW
    )
    assert (ok, why) == (False, "missing_pass_at_head")


def _verdict(sha: str, verdict: str, *, at: str, pr: int = 7) -> HeadReviewPass:
    return HeadReviewPass(pr=pr, head_sha=sha, verdict=verdict, recorded_at=at)


def test_first_rejection_is_not_a_failed_fix() -> None:
    count = failed_fix_passes_from_reviews(
        [_verdict(OLD, "changes", at="2026-11-19T10:00:00+00:00")],
        pr=7,
    )
    assert count == 0
    assert fix_pass_decision(failed_fix_passes=count) == "fix"


def test_second_rejected_head_is_one_failed_fix() -> None:
    count = failed_fix_passes_from_reviews(
        [
            _verdict(OLD, "changes", at="2026-11-19T10:00:00+00:00"),
            _verdict(NEW, "changes", at="2026-11-19T12:00:00+00:00"),
        ],
        pr=7,
    )
    assert count == 1
    assert fix_pass_decision(failed_fix_passes=count) == "escalate"


def test_approve_closes_the_fix_cycle() -> None:
    third = "c" * 40
    count = failed_fix_passes_from_reviews(
        [
            _verdict(OLD, "changes", at="2026-11-19T10:00:00+00:00"),
            _verdict(NEW, "approve", at="2026-11-19T11:00:00+00:00"),
            _verdict(third, "changes", at="2026-11-19T12:00:00+00:00"),
        ],
        pr=7,
    )
    assert count == 0


def test_later_verdict_on_the_same_head_wins() -> None:
    count = failed_fix_passes_from_reviews(
        [
            _verdict(OLD, "changes", at="2026-11-19T10:00:00+00:00"),
            _verdict(OLD, "approve", at="2026-11-19T11:00:00+00:00"),
        ],
        pr=7,
    )
    assert count == 0


def test_other_pr_rejections_do_not_count() -> None:
    count = failed_fix_passes_from_reviews(
        [
            _verdict(OLD, "changes", at="2026-11-19T10:00:00+00:00", pr=8),
            _verdict(NEW, "changes", at="2026-11-19T12:00:00+00:00", pr=8),
        ],
        pr=7,
    )
    assert count == 0


def test_shepherd_escalates_after_one_failed_fix_without_a_fresh_pass() -> None:
    head = "d" * 40
    decision = evaluate_pr_shepherd_eligibility(
        PRCandidate(
            "Runner_Dashboard",
            7,
            checks_status="SUCCESS",
            head_sha=head,
            file_paths=("backend/x.py",),
            files_complete=True,
            failed_fix_passes=1,
        )
    )
    assert (decision.action, decision.reason) == (ShepherdAction.SKIP, IneligibleReason.FIX_PASS_CAP)


def test_fresh_exact_head_pass_still_arms_after_an_earlier_failed_fix() -> None:
    head = "e" * 40
    decision = evaluate_pr_shepherd_eligibility(
        PRCandidate(
            "Runner_Dashboard",
            7,
            checks_status="SUCCESS",
            head_sha=head,
            file_paths=("backend/x.py",),
            files_complete=True,
            failed_fix_passes=1,
            review_passes=(_verdict(head, "approve", at=datetime.now(UTC).isoformat()),),
        )
    )
    assert decision.action == ShepherdAction.AUTO_ARM


def test_node_to_candidate_counts_failed_fix_passes() -> None:
    node = {
        "number": 7,
        "title": "pr 7",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "reviewDecision": "",
        "headRefOid": NEW,
        "additions": 1,
        "deletions": 0,
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
        "labels": {"nodes": []},
        "files": {"pageInfo": {"hasNextPage": False}, "nodes": [{"path": "backend/x.py"}]},
    }
    candidate = node_to_candidate(
        "D-sorganization/Runner_Dashboard",
        node,
        is_drained=False,
        head_sha=NEW,
        review_passes=[
            _verdict(OLD, "changes", at="2026-11-19T10:00:00+00:00"),
            _verdict(NEW, "changes", at="2026-11-19T12:00:00+00:00"),
        ],
    )
    assert candidate.failed_fix_passes == 1


def test_carry_forward_is_not_wired_into_arm_eligibility() -> None:
    """ADR 0010: arming needs fresh exact-head acceptance; carry-forward stays advisory."""
    import inspect

    from coordination.merge_shepherd_exact_head import shepherd_arm_eligibility

    params = inspect.signature(shepherd_arm_eligibility).parameters
    assert "head_fingerprint" not in params
    assert "pass_fingerprints" not in params
