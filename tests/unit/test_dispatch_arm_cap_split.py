"""Package split for armed-PR WIP cap stays under the 500-line cap (#2029)."""

from __future__ import annotations

from pathlib import Path

import dispatch_arm_cap
from dispatch_arm_cap import (
    AdmitContext,
    ArmCapStore,
    HealthVerdict,
    PrArmSnapshot,
    admit_for_arm,
    classify_arm_health,
)
from dispatch_arm_cap import admission, health, store, types


def test_public_import_surface_unchanged() -> None:
    assert dispatch_arm_cap.AdmitContext is AdmitContext
    assert dispatch_arm_cap.ArmCapStore is ArmCapStore
    assert dispatch_arm_cap.admit_for_arm is admit_for_arm
    assert dispatch_arm_cap.CONFLICT_DISARMED_LABEL == types.CONFLICT_DISARMED_LABEL


def test_split_modules_stay_under_line_cap() -> None:
    root = Path(types.__file__).resolve().parent
    for path in sorted(root.glob("*.py")):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= 500, f"{path.name} has {lines} lines"


def test_health_module_is_pure_classification() -> None:
    snap = PrArmSnapshot(
        repo="o/r",
        number=1,
        armed=True,
        draft=False,
        mergeable_state="clean",
        labels=(),
        in_merge_queue=False,
        merge_queue_state="",
        check_runs=(),
    )
    verdict = classify_arm_health(snap, ())
    assert isinstance(verdict, HealthVerdict)
    assert verdict.healthy is True
    assert health.classify_arm_health is classify_arm_health


def test_admission_does_not_own_sqlite_schema() -> None:
    text = Path(admission.__file__).read_text(encoding="utf-8")
    assert "CREATE TABLE" not in text
    assert store.ArmCapStore is ArmCapStore
