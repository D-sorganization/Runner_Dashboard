"""Fleet backup restore drill runner (#4013)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy" / "fleet-backup"))

import machine_record as mr  # noqa: E402
import restic_restore_drill as drill  # noqa: E402


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


def test_verify_restored_probe_requires_manifest_hashes(tmp_path: Path) -> None:
    root = tmp_path / "restore"
    root.mkdir()
    probe = root / "probe.txt"
    probe.write_text("ok", encoding="utf-8")
    manifest = {"files": [{"path": "probe.txt", "sha256": drill.sha256_file(probe)}]}
    assert drill.verify_restored_probe(manifest, root)
    probe.write_text("changed", encoding="utf-8")
    assert not drill.verify_restored_probe(manifest, root)


def test_run_restore_drill_once_records_verified_event(tmp_path: Path) -> None:
    record_path = tmp_path / "record.json"
    record_path.write_text(json.dumps(_record()), encoding="utf-8")

    def run_once() -> None:
        record = mr.load_record(record_path)
        mr.run_restore_drill_once(
            record,
            verify=lambda: True,
            alert=lambda _m: None,
            persist=lambda r: mr.save_record(record_path, r),
        )

    run_once()
    assert mr.verified_restore_drill_count(mr.load_record(record_path)) == 1
    run_once()
    assert mr.verified_restore_drill_count(mr.load_record(record_path)) == 2


def test_failed_drill_halts_record_and_does_not_retry_silently(tmp_path: Path) -> None:
    record_path = tmp_path / "record.json"
    record_path.write_text(json.dumps(_record()), encoding="utf-8")
    attempts = {"n": 0}
    alerts: list[str] = []

    def flaky() -> bool:
        attempts["n"] += 1
        return False

    record = mr.load_record(record_path)
    with pytest.raises(mr.DrillFailedError):
        mr.run_restore_drill_once(
            record,
            verify=flaky,
            alert=alerts.append,
            persist=lambda r: mr.save_record(record_path, r),
        )
    assert attempts["n"] == 1
    assert len(alerts) == 1
    loaded = mr.load_record(record_path)
    assert loaded.halted is True
    assert mr.verified_restore_drill_count(loaded) == 0
