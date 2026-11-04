"""Restic prune/forget gate tests (#4020)."""

from __future__ import annotations

import json
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
        "machine": "oglaptop",
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
    assert "oglaptop" in alerts[0].lower() or "restore drill" in alerts[0].lower()


def test_record_read_data_subset_appends_without_satisfying_gate() -> None:
    path = Path("/tmp/unused")
    saved: list[dict[str, Any]] = []

    def persist(rec: gate.MachineRecord) -> None:
        saved.append(rec.to_dict())

    record = gate.parse_record(_record())
    updated = gate.record_read_data_subset_check(
        record,
        verified=True,
        persist=persist,
    )
    assert gate.verified_restore_drill_count(updated) == 0
    assert saved[-1]["events"][-1]["kind"] == gate.KIND_READ_DATA_SUBSET
    with pytest.raises(gate.PreconditionViolation):
        gate.precondition_restic_prune_or_forget(updated)


def test_hub_alert_posts_json_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, bytes]] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok":true}'

    def opener(req, timeout):  # noqa: ANN001
        captured.append((req.full_url, req.data or b""))
        return _Resp()

    gate.post_hub_drill_failure_alert(
        hub_origin="https://hub-a.example",
        machine="oglaptop",
        detail="restore drill verification failed",
        token="fleet-token",
        opener=opener,
    )
    assert captured
    url, body = captured[0]
    assert url.endswith("/api/fleet/backup/drill-failure")
    payload = json.loads(body.decode())
    assert payload["machine"] == "oglaptop"
