"""Tests for drain-aware merge shepherd eligibility evaluator (#3932 slice 1)."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

_backend = Path(__file__).resolve().parents[2] / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import pytest  # noqa: E402
from coordination import merge_shepherd_exact_head  # noqa: E402
from coordination.merge_shepherd import (  # noqa: E402
    IneligibleReason,
    PRCandidate,
    ShepherdAction,
    evaluate_pr_shepherd_eligibility,
    get_auto_merge_flag,
    is_pr_held,
    rank_candidates_for_shepherding,
)
from coordination.merge_shepherd_exact_head import HeadReviewPass  # noqa: E402

# Fixture review passes are recorded at 2026-11-20T10:00Z; the test clock sits 24h later so
# they stay well inside the real 72h review expiry regardless of when the suite runs.
_PINNED_NOW = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[no-untyped-def]
        return _PINNED_NOW if tz is None else _PINNED_NOW.astimezone(tz)


def _armable_candidate(repo: str, pr_number: int, **kwargs: object) -> PRCandidate:
    head = str(kwargs.pop("head_sha", "abcd1234" * 5))
    return PRCandidate(
        repo,
        pr_number,
        head_sha=head,
        file_paths=kwargs.pop("file_paths", ("backend/x.py",)),
        files_complete=kwargs.pop("files_complete", True),
        review_passes=kwargs.pop(
            "review_passes",
            (
                HeadReviewPass(
                    pr=pr_number,
                    head_sha=head,
                    verdict="approve",
                    recorded_at="2026-11-20T10:00:00+00:00",
                ),
            ),
        ),
        **kwargs,
    )


@pytest.mark.unit
class TestMergeShepherdEvaluator(unittest.TestCase):
    def setUp(self) -> None:
        # Pin only the clock; the 72h DEFAULT_REVIEW_PASS_TTL_HOURS stays untouched.
        patcher = mock.patch.object(merge_shepherd_exact_head, "datetime", _FrozenDatetime)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_review_pass_older_than_72h_is_stale(self) -> None:
        """Guard: pinning the clock must not relax the real 72h review expiry."""
        self.assertEqual(merge_shepherd_exact_head.DEFAULT_REVIEW_PASS_TTL_HOURS, 72)
        head = "abcd1234" * 5
        stale = HeadReviewPass(pr=1, head_sha=head, verdict="approve", recorded_at="2026-11-18T09:59:00+00:00")
        fresh = HeadReviewPass(pr=1, head_sha=head, verdict="approve", recorded_at="2026-11-18T10:01:00+00:00")
        self.assertEqual(
            merge_shepherd_exact_head.pass_at_head((stale,), pr=1, head_sha=head),
            (False, "stale_unverified"),
        )
        self.assertEqual(
            merge_shepherd_exact_head.pass_at_head((fresh,), pr=1, head_sha=head),
            (True, "exact_head_pass"),
        )

    def test_get_auto_merge_flag(self) -> None:
        cases = [
            ("Runner_Dashboard", "--auto"),
            ("D-sorganization/Runner_Dashboard", "--auto"),
            ("Repository_Management", "--auto"),
            ("Gasification_Model", "--auto"),
            ("UpstreamDrift", "--auto --squash"),
            ("Tools", "--auto --squash"),
            ("AffineDrift", "--auto --squash"),
        ]
        for repo, expected in cases:
            with self.subTest(repo=repo):
                self.assertEqual(get_auto_merge_flag(repo), expected)

    def test_eligible_auto_arm(self) -> None:
        d_rd = evaluate_pr_shepherd_eligibility(_armable_candidate("Runner_Dashboard", 3900))
        self.assertEqual((d_rd.action, d_rd.merge_flag), (ShepherdAction.AUTO_ARM, "--auto"))

        d_ud = evaluate_pr_shepherd_eligibility(_armable_candidate("UpstreamDrift", 14000))
        self.assertEqual((d_ud.action, d_ud.merge_flag), (ShepherdAction.AUTO_ARM, "--auto --squash"))

    def test_behind_clean_pr_never_yields_update_branch(self) -> None:
        """A behind-but-clean PR must arm directly instead of churning via UPDATE_BRANCH (panel th_25026e93f6bb)."""
        decision = evaluate_pr_shepherd_eligibility(_armable_candidate("Runner_Dashboard", 3901, is_behind=True))
        self.assertEqual(decision.action, ShepherdAction.AUTO_ARM)
        self.assertEqual(decision.merge_flag, "--auto")

    def test_ineligible_without_head_sha(self) -> None:
        missing = PRCandidate("Runner_Dashboard", 4574, checks_status="SUCCESS", file_paths=("backend/x.py",))
        decision = evaluate_pr_shepherd_eligibility(missing)
        self.assertEqual(
            (decision.action, decision.reason, decision.detail),
            (ShepherdAction.SKIP, IneligibleReason.NO_EXACT_HEAD_PASS, "missing_pass_at_head"),
        )

    def test_ineligible_reasons(self) -> None:
        cases = [
            (
                PRCandidate("RD", 1, is_behind=True, mergeable="CONFLICTING"),
                IneligibleReason.CONFLICT,
            ),
            (PRCandidate("RD", 2, is_draft=True), IneligibleReason.DRAFT),
            (PRCandidate("RD", 3, state="CLOSED"), IneligibleReason.NOT_OPEN),
            (PRCandidate("RD", 4, is_queued=True), IneligibleReason.ALREADY_QUEUED),
            (
                PRCandidate("RD", 5, auto_merge_enabled=True),
                IneligibleReason.ALREADY_ARMED,
            ),
            (PRCandidate("RD", 6, bounces=2), IneligibleReason.BOUNCE_LIMIT),
            (
                PRCandidate("RD", 7, checks_status="PENDING"),
                IneligibleReason.CHECKS_PENDING,
            ),
            (
                PRCandidate("RD", 8, checks_status="FAILURE"),
                IneligibleReason.CHECKS_FAILED,
            ),
        ]
        _structural = frozenset({
            IneligibleReason.DRAFT,
            IneligibleReason.CONFLICT,
            IneligibleReason.HELD,
            IneligibleReason.BOUNCE_LIMIT,
            IneligibleReason.RETRY_SUPPRESSED,
            IneligibleReason.DRAINED,
        })
        for cand, expected_reason in cases:
            with self.subTest(cand=cand.pr_number):
                dec = evaluate_pr_shepherd_eligibility(cand)
                expected_action = (
                    ShepherdAction.DISARM if (cand.is_queued and expected_reason in _structural) else ShepherdAction.SKIP
                )
                self.assertEqual((dec.action, dec.reason), (expected_action, expected_reason))

    def test_ineligible_when_held(self) -> None:
        cases = [
            (["hold"], "fix: normal fix"),
            (["drain-hold"], "fix: normal fix"),
            (["do-not-merge"], "fix: normal fix"),
            (["blocked"], "fix: normal fix"),
            (["needs-decision"], "fix: normal fix"),
            (["needs-owner"], "fix: normal fix"),
            ([], "[HOLD] refactor dispatch"),
            ([], "chore: topology sanitize deploy paths (held)"),
        ]
        for labels, title in cases:
            with self.subTest(title=title):
                self.assertTrue(is_pr_held(labels, title))
                cand = PRCandidate("RD", 9, labels=tuple(labels), title=title)
                dec = evaluate_pr_shepherd_eligibility(cand)
                self.assertEqual(
                    (dec.action, dec.reason),
                    (ShepherdAction.SKIP, IneligibleReason.HELD),
                )

    def test_drain_gate_evaluation(self) -> None:
        blocked = PRCandidate("GM", 5700, is_drained=True, is_exempt=False)
        self.assertEqual(evaluate_pr_shepherd_eligibility(blocked).reason, IneligibleReason.DRAINED)

        exempt = _armable_candidate("GM", 5701, is_drained=True, is_exempt=True)
        self.assertEqual(evaluate_pr_shepherd_eligibility(exempt).action, ShepherdAction.AUTO_ARM)

    def test_rank_candidates(self) -> None:
        c1 = _armable_candidate("RD", 100)
        c2 = _armable_candidate("RD", 101, labels=("merge:priority",))
        c3 = _armable_candidate("RD", 102, is_behind=True)
        ranked = rank_candidates_for_shepherding([c1, c2, c3])
        self.assertEqual([d.pr_number for d in ranked], [101, 100, 102])

    def test_evaluate_wires_shepherd_arm_eligibility_when_head_sha_set(self) -> None:
        head = "abcd1234" * 5
        missing_pass = PRCandidate(
            "Runner_Dashboard",
            4573,
            checks_status="SUCCESS",
            head_sha=head,
            file_paths=("backend/x.py",),
            files_complete=True,
        )
        skip = evaluate_pr_shepherd_eligibility(missing_pass)
        self.assertEqual(
            (skip.action, skip.reason, skip.detail),
            (ShepherdAction.SKIP, IneligibleReason.NO_EXACT_HEAD_PASS, "missing_pass_at_head"),
        )
        with_pass = PRCandidate(
            "Runner_Dashboard",
            4573,
            checks_status="SUCCESS",
            head_sha=head,
            file_paths=("backend/x.py",),
            files_complete=True,
            review_passes=(
                HeadReviewPass(
                    pr=4573,
                    head_sha=head,
                    verdict="approve",
                    recorded_at="2026-11-20T10:00:00+00:00",
                ),
            ),
        )
        arm = evaluate_pr_shepherd_eligibility(with_pass)
        self.assertEqual(arm.action, ShepherdAction.AUTO_ARM)
        self.assertIn("exact_head_pass", arm.detail)


if __name__ == "__main__":
    unittest.main()
