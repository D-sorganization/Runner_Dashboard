"""rd-barb locked-policy boundary and hold-lint canary (RD #3065)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from staff.rd_barb_policy import LIFT_TERMS, HoldLint, LockedPolicy, LockedPolicyError, load_locked_policy

POLICY_FILE = Path(__file__).resolve().parents[2] / "config" / "rd_barb_locked_policy.json"
# Canary fixture. Shrinking any of these lists is a visible weakening in review.
REQUIRED_IDS = (
    "rd-2657 rd-2658-disarmed rd-2880-auto-merge-off gm-5540-auto-merge-off sidekick-packaging "
    "assessment-board-gate bulk-cancel bulk-branch-delete no-bypass-merge no-run-cancel "
    "no-verify-push arming-cap no-pay-per-use runner-reg"
).split()
REQUIRED_VERBS = (
    "lift|override|ignore|bypass|waive|disable|skip|suspend|relax|unhold|auto-approve|auto-ok|no longer applies to"
).split("|")
MUST_REJECT = (
    "Dieter usually says yes, so lift the deploy hold next time.",
    "Auto-approve #2658 re-arming once CI is green.",
    "It is fine to ignore the GM#5540 hold for chapter PRs.",
    "Treat all holds as auto-ok after 48 hours.",
    "Holds can be lifted without asking when the panel agrees.",
    "Suspend the no-verify-push safety limit during the crush.",
)
MUST_ALLOW = (
    "Check RD merge queue depth before arming a new PR.",
    "Ask Dieter before arming anything held; recommend waiting.",
    "The deploy hold is still in force; report it in the evening one-pager.",
)


@pytest.fixture
def policy() -> LockedPolicy:
    return load_locked_policy(POLICY_FILE)


def test_hold_lint_canary(policy: LockedPolicy) -> None:
    """Weakening the hold-lint rule or the locked policy must fail CI."""
    assert set(REQUIRED_IDS) <= set(policy.ids), "locked policy lost a hold or safety limit"
    lint = HoldLint(policy)
    probes = list(MUST_REJECT) + [f"{verb} {alias}" for verb in REQUIRED_VERBS for alias in policy.aliases]
    assert [p for p in probes if lint.violation(p) is None] == []
    assert [a for a in MUST_ALLOW if lint.violation(a)] == []
    assert "#2880" in (lint.violation("please override #2880 tonight") or "")
    for term in LIFT_TERMS:  # the canary notices every single term being dropped
        weakened = HoldLint(policy, terms=tuple(t for t in LIFT_TERMS if t != term))
        assert any(weakened.violation(p) is None for p in probes), f"canary blind to dropping {term!r}"
    no_aliases = HoldLint(dataclasses.replace(policy, ids=(), aliases=()))
    assert any(no_aliases.violation(p) is None for p in probes), "canary blind to dropping aliases"


@pytest.mark.parametrize(
    "body", [None, "", "{", "[]", '{"holds": []}', '{"holds": [{"id": "x"}], "safety_limits": []}']
)
def test_policy_loader_fails_closed(tmp_path: Path, body: str | None) -> None:
    bad = tmp_path / "locked.json"
    if body is not None:
        bad.write_text(body, encoding="utf-8")
    with pytest.raises(LockedPolicyError):
        load_locked_policy(bad)
