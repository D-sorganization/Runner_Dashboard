"""Restic prune/forget gate tests (#4020)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy" / "fleet-backup"))

import restic_prune_gate as gate  # noqa: E402


def _record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "version": 1,
        "machine": "node-b",
        "halted": False,
        "halt_reason": None,
        "events": [],
    }
    base.update(overrides)
    return base


def _verified_drill(at: str = "2026-11-01T00:00:00Z") -> dict[str, Any]:
    return {"kind": gate.KIND_RESTORE_DRILL, "verified": True, "at": at}


def test_weekly_read_data_subset_does_not_count_toward_prune_gate() -> None:
    record = gate.parse_record(
        _record(
            events=[
                _verified_drill("2026-11-01T00:00:00Z"),
                _verified_drill("2026-11-02T00:00:00Z"),
                {"kind": gate.KIND_READ_DATA_SUBSET, "verified": True, "at": "2026-11-03T00:00:00Z"},
            ]
        )
    )
    assert gate.verified_restore_drill_count(record) == 2
    gate.precondition_restic_prune_or_forget(record)  # does not raise


def test_prune_gate_requires_two_verified_restore_drills() -> None:
    one = gate.parse_record(_record(events=[_verified_drill()]))
    with pytest.raises(gate.PreconditionViolation, match="restore drill"):
        gate.precondition_restic_prune_or_forget(one)

    two = gate.parse_record(
        _record(events=[_verified_drill("2026-11-01T00:00:00Z"), _verified_drill("2026-11-02T00:00:00Z")])
    )
    gate.precondition_restic_prune_or_forget(two)


def test_unverified_restore_drill_does_not_count() -> None:
    record = gate.parse_record(
        _record(
            events=[
                _verified_drill(),
                {"kind": gate.KIND_RESTORE_DRILL, "verified": False, "at": "2026-11-02T00:00:00Z"},
            ]
        )
    )
    assert gate.verified_restore_drill_count(record) == 1
    with pytest.raises(gate.PreconditionViolation):
        gate.precondition_restic_prune_or_forget(record)


def test_prune_and_forget_both_require_the_gate() -> None:
    record = gate.parse_record(_record(events=[_verified_drill()]))
    for argv in (["restic", "forget", "latest", "--keep-last", "1"], ["restic", "prune"]):
        assert gate.restic_mutation_requires_gate(argv)
        with pytest.raises(gate.PreconditionViolation):
            gate.run_gated_restic(record, argv, runner=lambda _a: 0)


def test_failed_restore_drill_alerts_hub_halts_and_does_not_retry() -> None:
    alerts: list[str] = []
    attempts = {"n": 0}

    def flaky() -> bool:
        attempts["n"] += 1
        return False

    record = gate.parse_record(_record())

    with pytest.raises(gate.DrillFailedError):
        gate.run_restore_drill_once(
            record,
            verify=flaky,
            alert=lambda msg: alerts.append(msg),
            persist=lambda _r: None,
        )

    assert attempts["n"] == 1, "failed drills must not be retried silently"
    assert len(alerts) == 1
    assert "node-b" in alerts[0].lower() or "restore drill" in alerts[0].lower()

