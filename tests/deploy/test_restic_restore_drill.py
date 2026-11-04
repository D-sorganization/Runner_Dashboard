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
        "machine": "oglaptop",
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
    record = mr.load_record(record_path)
    mr.run_restore_drill_once(record, verify=lambda: True, alert=lambda _m: None, persist=lambda r: mr.save_record(record_path, r))
    loaded = mr.load_record(record_path)
    assert mr.verified_restore_drill_count(loaded) == 1
    assert loaded.events[0].kind == mr.KIND_RESTORE_DRILL


def test_two_separate_drills_accumulate_on_record(tmp_path: Path) -> None:
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
    run_once()
    loaded = mr.load_record(record_path)
    assert mr.verified_restore_drill_count(loaded) == 2


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


def test_cli_run_invokes_restic_and_verifies_probe(tmp_path: Path) -> None:
    record_path = tmp_path / "record.json"
    record_path.write_text(json.dumps(_record()), encoding="utf-8")
    restore_root = tmp_path / "out"
    restore_root.mkdir()
    probe_rel = "data/probe.txt"
    probe_file = restore_root / probe_rel
    probe_file.parent.mkdir(parents=True)
    probe_file.write_text("drill-payload", encoding="utf-8")
    manifest_path = tmp_path / "probe.json"
    manifest_path.write_text(
        json.dumps({"files": [{"path": probe_rel, "sha256": drill.sha256_file(probe_file)}]}),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_runner(argv: list[str]) -> int:
        calls.append(list(argv))
        return 0

    rc = drill.run_drill(
        record_path=record_path,
        machine="oglaptop",
        snapshot="deadbeef",
        restore_root=restore_root,
        probe_manifest=manifest_path,
        restic_binary="restic",
        runner=fake_runner,
        alert=lambda _m: None,
    )
    assert rc == 0
    assert calls and calls[0][0] == "restic"
    assert mr.verified_restore_drill_count(mr.load_record(record_path)) == 1
