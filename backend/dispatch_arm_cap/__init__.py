"""Atomic WIP cap on healthy armed pull requests (RD-CI-005, issue #2029).

Package layout:
- ``types`` — policy and snapshot models (no side effects)
- ``health`` — pure classification of whether an armed PR counts toward the cap
- ``store`` — SQLite reservations and inventory persistence
- ``admission`` — policy load, admit/reconcile orchestration, disarm gating
"""

from __future__ import annotations

from dispatch_arm_cap.admission import (
    admit_for_arm,
    build_disarm_workflow_inputs,
    load_policy,
    reconcile_snapshots,
    record_disarm_triggered,
    should_trigger_cap_disarm,
)
from dispatch_arm_cap.health import classify_arm_health, load_required_contexts
from dispatch_arm_cap.store import ArmCapStore
from dispatch_arm_cap.types import (
    CONFLICT_DISARMED_LABEL,
    DEFAULT_POLICY_PATH,
    DEFAULT_STORE_PATH,
    AdmitContext,
    AdmitDecision,
    ArmCapPolicy,
    ArmTier,
    HealthVerdict,
    Lifecycle,
    PrArmSnapshot,
    ReservationStatus,
)

__all__ = [
    "CONFLICT_DISARMED_LABEL",
    "DEFAULT_POLICY_PATH",
    "DEFAULT_STORE_PATH",
    "AdmitContext",
    "AdmitDecision",
    "ArmCapPolicy",
    "ArmCapStore",
    "ArmTier",
    "HealthVerdict",
    "Lifecycle",
    "PrArmSnapshot",
    "ReservationStatus",
    "admit_for_arm",
    "build_disarm_workflow_inputs",
    "classify_arm_health",
    "load_policy",
    "load_required_contexts",
    "reconcile_snapshots",
    "record_disarm_triggered",
    "should_trigger_cap_disarm",
]
