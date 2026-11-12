"""Trusted feature admission and reservation boundaries (RM#2278, source-only).

Codifies SPEC dispatch semantics and the sibling-repos admission split between
new-feature work, operational repair, and in-flight continuation. Runtime quota
enforcement and reservation commits require a separate freshness-evidence
contract (activation PR); this module never enables enforcement by default.

Caller-controlled ``task_kind`` (Quick Dispatch / workflow inputs) and ``force``
hints are recorded for audit only and must never drive classification or quota.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from dispatch_effort import normalize_dispatch_kind

_ISSUED_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


class DashboardWorkKind(StrEnum):
    """Authoritative dashboard work classes for rollout admission."""

    NEW_FEATURE = "new_feature"
    OPERATIONAL = "operational"
    CONTINUATION = "continuation"


_NEW_FEATURE_KINDS = frozenset({"implementation", "design", "pr", "expand_epic_children"})
_OPERATIONAL_PREFIXES = ("ci_fix:", "conflict")
_CONTINUATION_KINDS = frozenset({"continue", "needs_input_resume", "continuation"})


@dataclass(frozen=True, slots=True)
class ReservationDecision:
    """Contract result for a feature reservation evaluation (fail-closed default)."""

    allowed: bool
    work_kind: DashboardWorkKind
    reason: str
    caller_task_kind: str | None = None
    caller_force: bool | None = None


def classify_dashboard_work(
    *,
    dispatch_kind: str,
    caller_task_kind: str | None = None,
    caller_force: bool | None = None,
) -> DashboardWorkKind:
    """Classify work using only the trusted ``dispatch_kind`` channel.

    ``caller_task_kind`` and ``caller_force`` are ignored for classification (RM#2278).
    They may be supplied for downstream audit fields.
    """
    _ = caller_task_kind, caller_force
    kind = normalize_dispatch_kind(dispatch_kind)
    if kind in _CONTINUATION_KINDS:
        return DashboardWorkKind.CONTINUATION
    if any(kind.startswith(prefix) for prefix in _OPERATIONAL_PREFIXES):
        return DashboardWorkKind.OPERATIONAL
    if kind in _NEW_FEATURE_KINDS:
        return DashboardWorkKind.NEW_FEATURE
    return DashboardWorkKind.OPERATIONAL


def caller_controlled_hints_are_non_authoritative(
    *,
    dispatch_kind: str,
    caller_task_kind: str | None,
    caller_force: bool | None,
) -> bool:
    """Return True when hints do not alter authoritative classification."""
    return classify_dashboard_work(dispatch_kind=dispatch_kind) == classify_dashboard_work(
        dispatch_kind=dispatch_kind,
        caller_task_kind=caller_task_kind,
        caller_force=caller_force,
    )


def _freshness_evidence_present(payload: Mapping[str, object] | None) -> bool:
    if payload is None:
        return False
    issued_at = payload.get("issued_at")
    receipt_id = payload.get("receipt_id")
    return (
        isinstance(issued_at, str)
        and bool(_ISSUED_AT.match(issued_at.strip()))
        and isinstance(receipt_id, str)
        and bool(receipt_id.strip())
    )


def library_quota_applies(work_kind: DashboardWorkKind) -> bool:
    """New-feature rollout counts against reservation quota; repair/continuation do not."""
    return work_kind is DashboardWorkKind.NEW_FEATURE


def enforce_reservation_boundary(
    *,
    dispatch_kind: str,
    caller_task_kind: str | None = None,
    caller_force: bool | None = None,
    freshness_evidence: Mapping[str, object] | None = None,
    activation_enabled: bool = False,
) -> ReservationDecision:
    """Evaluate reservation under the source-only contract (never allow when unactivated)."""
    work_kind = classify_dashboard_work(
        dispatch_kind=dispatch_kind,
        caller_task_kind=caller_task_kind,
        caller_force=caller_force,
    )
    if not _freshness_evidence_present(freshness_evidence):
        return ReservationDecision(
            allowed=False,
            work_kind=work_kind,
            reason="reservation_freshness_evidence_absent",
            caller_task_kind=caller_task_kind,
            caller_force=caller_force,
        )
    if not activation_enabled:
        return ReservationDecision(
            allowed=False,
            work_kind=work_kind,
            reason="feature_admission_boundary_not_activated",
            caller_task_kind=caller_task_kind,
            caller_force=caller_force,
        )
    return ReservationDecision(
        allowed=False,
        work_kind=work_kind,
        reason="feature_admission_boundary_not_activated",
        caller_task_kind=caller_task_kind,
        caller_force=caller_force,
    )
