"""Tests for backend/runner_lease_pilot.py — issue #3878 slice 2 (staging pilot evidence)."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import runner_lease_pilot as pilot
import yaml


@pytest.fixture(autouse=True)
def _unguard_config_path():
    """Let leases persist under tmp_path (same fixture as tests/test_runner_lease_migration.py)."""
    with (
        patch("runner_lease.validate_config_path"),
        patch("security.validate_config_path", side_effect=lambda p, *a, **k: p),
    ):
        yield


def _write_leases(config_dir: Path, leases: list[dict]) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "leases.yml"
    path.write_text(yaml.safe_dump({"leases": leases}), encoding="utf-8")
    return path


def _lease(runner_id: str, principal_id: str = "alice", ttl: float = 3600.0, **extra: object) -> dict:
    now = time.time()
    return {
        "principal_id": principal_id,
        "runner_id": runner_id,
        "acquired_at": now,
        "expires_at": now + ttl,
        **extra,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_pilot_drains_cuts_over_and_rolls_back_on_a_copy(tmp_path: Path) -> None:
    source = _write_leases(
        tmp_path / "node",
        [
            _lease("runner-1", task_id="t-1", metadata={"repo": "Runner_Dashboard"}),
            _lease("runner-2", principal_id="bob"),
        ],
    )

    report = pilot.run_pilot(source.parent, tmp_path / "work")

    assert report.source_record_count == 2
    assert report.agrees_before_drain is False
    assert report.drained_record_count == 2
    assert report.agrees_after_drain is True
    assert report.field_mismatches == ()
    assert report.cutover_ok is True
    assert report.rollback_ok is True
    assert report.sqlite_journal_mode == "wal"
    assert report.passed is True


def test_pilot_never_modifies_the_source_leases_file(tmp_path: Path) -> None:
    # An expired lease would be pruned (rewritten) by LeaseManager if it ran on the source.
    source = _write_leases(tmp_path / "node", [_lease("runner-1"), _lease("runner-old", ttl=-60.0)])
    before = _sha256(source)

    report = pilot.run_pilot(source.parent, tmp_path / "work")

    assert _sha256(source) == before
    assert report.source_unchanged is True
    assert report.source_record_count == 1


def test_pilot_with_no_leases_file_passes_with_zero_records(tmp_path: Path) -> None:
    (tmp_path / "node").mkdir()

    report = pilot.run_pilot(tmp_path / "node", tmp_path / "work")

    assert report.source_record_count == 0
    assert report.passed is True
    assert not (tmp_path / "node" / "leases.yml").exists()


def test_pilot_reports_field_level_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _write_leases(tmp_path / "node", [_lease("runner-1", task_id="t-1")])
    original_write_all = pilot.SqliteLeaseStore.write_all

    def lossy_write_all(self, records):  # type: ignore[no-untyped-def]
        original_write_all(self, [r.model_copy(update={"task_id": None}) for r in records])

    monkeypatch.setattr(pilot.SqliteLeaseStore, "write_all", lossy_write_all)

    report = pilot.run_pilot(source.parent, tmp_path / "work")

    assert report.agrees_after_drain is True  # runner ids match...
    assert report.field_mismatches == ("runner-1: task_id",)  # ...but a field was lost
    assert report.passed is False


def test_pilot_refuses_the_source_config_dir_as_work_dir(tmp_path: Path) -> None:
    source = _write_leases(tmp_path / "node", [_lease("runner-1")])

    with pytest.raises(ValueError, match="work_dir"):
        pilot.run_pilot(source.parent, source.parent)


def test_pilot_accepts_a_subdirectory_of_the_config_dir(tmp_path: Path) -> None:
    # On a node the copy must sit under the allowed config root, e.g. ~/.config/runner-dashboard/lease-pilot.
    source = _write_leases(tmp_path / "node", [_lease("runner-1")])
    before = _sha256(source)

    report = pilot.run_pilot(source.parent, source.parent / "lease-pilot")

    assert report.passed is True
    assert _sha256(source) == before


def test_pilot_fails_when_the_copy_silently_loads_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # LeaseManager.load_leases logs and yields [] on any load error; that must not pass as "0 leases".
    source = _write_leases(tmp_path / "node", [_lease("runner-1"), _lease("runner-2")])
    monkeypatch.setattr(pilot.LeaseManager, "get_active_leases", lambda self, principal_id=None: [])

    report = pilot.run_pilot(source.parent, tmp_path / "work")

    assert report.source_record_count == 2
    assert report.copy_record_count == 0
    assert report.passed is False


def test_pilot_raises_when_the_copy_is_outside_the_allowed_config_roots(tmp_path: Path) -> None:
    source = _write_leases(tmp_path / "node", [_lease("runner-1")])

    with (
        patch("runner_lease.validate_config_path", side_effect=ValueError("Config path escapes allowed roots")),
        pytest.raises(ValueError, match="allowed roots"),
    ):
        pilot.run_pilot(source.parent, tmp_path / "work")


def test_report_as_dict_is_json_ready(tmp_path: Path) -> None:
    source = _write_leases(tmp_path / "node", [_lease("runner-1")])

    data = pilot.run_pilot(source.parent, tmp_path / "work").as_dict()

    assert data["passed"] is True
    assert data["field_mismatches"] == []
    assert "principal_id" not in str(data)  # counts and verdicts only; no lease contents


def test_cli_prints_the_report_and_exits_zero_on_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import importlib.util
    import json

    script = Path(__file__).parents[1] / "scripts" / "runner_lease_pilot.py"
    spec = importlib.util.spec_from_file_location("runner_lease_pilot_cli", script)
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    source = _write_leases(tmp_path / "node", [_lease("runner-1")])

    code = cli.main(["--config-dir", str(source.parent)])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True
    assert (source.parent / "lease-pilot" / "pilot-leases.sqlite3").exists()
