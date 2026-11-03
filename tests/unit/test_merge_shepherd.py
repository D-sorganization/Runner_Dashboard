"""Tests for drain-aware merge shepherd eligibility evaluator (#3932 slice 1)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_backend = Path(__file__).resolve().parents[2] / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import pytest  # noqa: E402
from coordination.merge_shepherd import (  # noqa: E402
    IneligibleReason,
    PRCandidate,
    ShepherdAction,
    evaluate_pr_shepherd_eligibility,
    get_auto_merge_flag,
    is_pr_held,
    rank_candidates_for_shepherding,
)


@pytest.mark.unit
class TestMergeShepherdEvaluator(unittest.TestCase):
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
        rd = PRCandidate("Runner_Dashboard", 3900)
        d_rd = evaluate_pr_shepherd_eligibility(rd)
        self.assertEqual((d_rd.action, d_rd.merge_flag), (ShepherdAction.AUTO_ARM, "--auto"))

        ud = PRCandidate("UpstreamDrift", 14000)
        d_ud = evaluate_pr_shepherd_eligibility(ud)
        self.assertEqual((d_ud.action, d_ud.merge_flag), (ShepherdAction.AUTO_ARM, "--auto --squash"))

    def test_eligible_update_branch_when_behind(self) -> None:
        cand = PRCandidate("Runner_Dashboard", 3901, is_behind=True)
        decision = evaluate_pr_shepherd_eligibility(cand)
        self.assertEqual(decision.action, ShepherdAction.UPDATE_BRANCH)
        self.assertIsNone(decision.merge_flag)

    def test_ineligible_reasons(self) -> None:
        cases = [
            (PRCandidate("RD", 1, is_behind=True, mergeable="CONFLICTING"), IneligibleReason.CONFLICT),
            (PRCandidate("RD", 2, is_draft=True), IneligibleReason.DRAFT),
            (PRCandidate("RD", 3, state="CLOSED"), IneligibleReason.NOT_OPEN),
            (PRCandidate("RD", 4, is_queued=True), IneligibleReason.ALREADY_QUEUED),
            (PRCandidate("RD", 5, auto_merge_enabled=True), IneligibleReason.ALREADY_ARMED),
            (PRCandidate("RD", 6, bounces=2), IneligibleReason.BOUNCE_LIMIT),
            (PRCandidate("RD", 7, checks_status="PENDING"), IneligibleReason.CHECKS_PENDING),
            (PRCandidate("RD", 8, checks_status="FAILURE"), IneligibleReason.CHECKS_FAILED),
        ]
        for cand, expected_reason in cases:
            with self.subTest(cand=cand.pr_number):
                dec = evaluate_pr_shepherd_eligibility(cand)
                self.assertEqual((dec.action, dec.reason), (ShepherdAction.SKIP, expected_reason))

    def test_ineligible_when_held(self) -> None:
        cases = [
            (["hold"], "fix: normal fix"),
            (["drain-hold"], "fix: normal fix"),
            (["do-not-merge"], "fix: normal fix"),
            (["blocked"], "fix: normal fix"),
            (["needs-decision"], "fix: normal fix"),
            ([], "[HOLD] refactor dispatch"),
            ([], "chore: topology sanitize deploy paths (held)"),
        ]
        for labels, title in cases:
            with self.subTest(title=title):
                self.assertTrue(is_pr_held(labels, title))
                cand = PRCandidate("RD", 9, labels=tuple(labels), title=title)
                dec = evaluate_pr_shepherd_eligibility(cand)
                self.assertEqual((dec.action, dec.reason), (ShepherdAction.SKIP, IneligibleReason.HELD))

    def test_drain_gate_evaluation(self) -> None:
        blocked = PRCandidate("GM", 5700, is_drained=True, is_exempt=False)
        self.assertEqual(evaluate_pr_shepherd_eligibility(blocked).reason, IneligibleReason.DRAINED)

        exempt = PRCandidate("GM", 5701, is_drained=True, is_exempt=True)
        self.assertEqual(evaluate_pr_shepherd_eligibility(exempt).action, ShepherdAction.AUTO_ARM)

    def test_rank_candidates(self) -> None:
        c1 = PRCandidate("RD", 100)
        c2 = PRCandidate("RD", 101, labels=("merge:priority",))
        c3 = PRCandidate("RD", 102, is_behind=True)
        ranked = rank_candidates_for_shepherding([c1, c2, c3])
        self.assertEqual([d.pr_number for d in ranked], [101, 100, 102])


if __name__ == "__main__":
    unittest.main()
