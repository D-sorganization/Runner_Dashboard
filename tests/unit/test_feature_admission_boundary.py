"""RM#2278: trusted feature admission boundary (source-only contract)."""

from __future__ import annotations

import pytest
from feature_admission_boundary import (
    DashboardWorkKind,
    ReservationDecision,
    caller_controlled_hints_are_non_authoritative,
    classify_dashboard_work,
    enforce_reservation_boundary,
    library_quota_applies,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "dispatch_kind,expected",
    [
        ("implementation", DashboardWorkKind.NEW_FEATURE),
        ("design", DashboardWorkKind.NEW_FEATURE),
        ("pr", DashboardWorkKind.NEW_FEATURE),
        ("ci_fix:lint", DashboardWorkKind.OPERATIONAL),
        ("ci_fix:test", DashboardWorkKind.OPERATIONAL),
        ("conflict", DashboardWorkKind.OPERATIONAL),
        ("continue", DashboardWorkKind.CONTINUATION),
        ("needs_input_resume", DashboardWorkKind.CONTINUATION),
    ],
)
def test_classify_dashboard_work_from_dispatch_kind_only(dispatch_kind: str, expected: DashboardWorkKind) -> None:
    assert classify_dashboard_work(dispatch_kind=dispatch_kind) == expected


@pytest.mark.parametrize(
    "dispatch_kind,caller_task_kind,caller_force",
    [
        ("ci_fix:lint", "new_feature", True),
        ("implementation", "adhoc", True),
        ("continue", "implementation", False),
    ],
)
def test_caller_task_kind_and_force_never_change_classification(
    dispatch_kind: str, caller_task_kind: str, caller_force: bool
) -> None:
    base = classify_dashboard_work(dispatch_kind=dispatch_kind)
    hinted = classify_dashboard_work(
        dispatch_kind=dispatch_kind,
        caller_task_kind=caller_task_kind,
        caller_force=caller_force,
    )
    assert hinted == base
    assert caller_controlled_hints_are_non_authoritative(
        dispatch_kind=dispatch_kind,
        caller_task_kind=caller_task_kind,
        caller_force=caller_force,
    )


def test_enforce_reservation_fail_closed_without_freshness_evidence() -> None:
    decision = enforce_reservation_boundary(
        dispatch_kind="implementation",
        caller_task_kind="new_feature",
        caller_force=True,
        freshness_evidence=None,
        activation_enabled=False,
    )
    assert isinstance(decision, ReservationDecision)
    assert decision.allowed is False
    assert decision.reason == "reservation_freshness_evidence_absent"


def test_enforce_reservation_fail_closed_even_with_force_hint() -> None:
    decision = enforce_reservation_boundary(
        dispatch_kind="implementation",
        caller_task_kind="adhoc",
        caller_force=True,
        freshness_evidence={"issued_at": "2026-11-09T00:00:00Z", "receipt_id": "r1"},
        activation_enabled=False,
    )
    assert decision.allowed is False
    assert decision.work_kind == DashboardWorkKind.NEW_FEATURE
    assert decision.reason == "feature_admission_boundary_not_activated"


def test_library_quota_applies_to_new_feature_not_operational() -> None:
    assert library_quota_applies(DashboardWorkKind.NEW_FEATURE) is True
    assert library_quota_applies(DashboardWorkKind.OPERATIONAL) is False
    assert library_quota_applies(DashboardWorkKind.CONTINUATION) is False
