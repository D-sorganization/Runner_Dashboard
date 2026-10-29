"""Registry go-public migration and empty-registry observability (#3304)."""

from __future__ import annotations

import stat
import sys
from pathlib import Path
from unittest.mock import patch

import machine_registry as mr
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "deploy"))


def test_migrate_legacy_registry_copies_deploy_file_to_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    deploy_backend = tmp_path / "dashboard" / "backend"
    deploy_backend.mkdir(parents=True)
    legacy = deploy_backend / "machine_registry.yml"
    legacy.write_text("version: 1\nmachines:\n  - name: hub-a\n", encoding="utf-8")

    local = tmp_path / "config" / "machine_registry.yml"
    monkeypatch.setattr(mr, "LOCAL_REGISTRY_PATH", local)
    monkeypatch.delenv("MACHINE_REGISTRY_PATH", raising=False)

    outcome = mr.migrate_legacy_registry_to_local_config(deploy_backend_dir=deploy_backend)

    assert outcome == "migrated"
    assert local.read_text(encoding="utf-8") == legacy.read_text(encoding="utf-8")
    assert stat.S_IMODE(local.stat().st_mode) == 0o600


def test_migrate_legacy_registry_does_not_overwrite_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    deploy_backend = tmp_path / "dashboard" / "backend"
    deploy_backend.mkdir(parents=True)
    (deploy_backend / "machine_registry.yml").write_text("version: 1\nmachines:\n  - name: new\n", encoding="utf-8")

    local = tmp_path / "config" / "machine_registry.yml"
    local.parent.mkdir(parents=True)
    local.write_text("version: 1\nmachines:\n  - name: kept\n", encoding="utf-8")

    monkeypatch.setattr(mr, "LOCAL_REGISTRY_PATH", local)
    monkeypatch.delenv("MACHINE_REGISTRY_PATH", raising=False)

    outcome = mr.migrate_legacy_registry_to_local_config(deploy_backend_dir=deploy_backend)

    assert outcome == "skipped_local_exists"
    assert "kept" in local.read_text(encoding="utf-8")


def test_load_empty_registry_sets_warning_and_metric_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    missing = tmp_path / "missing.yml"
    monkeypatch.setattr(mr, "LOCAL_REGISTRY_PATH", missing)
    monkeypatch.setattr(mr, "DEPLOYED_REGISTRY_PATH", missing)
    monkeypatch.delenv("MACHINE_REGISTRY_PATH", raising=False)

    metric_calls: list[bool] = []

    def _capture(empty: bool) -> None:
        metric_calls.append(empty)

    with patch("prometheus_metrics.set_machine_registry_empty", side_effect=_capture):
        result = mr.load_machine_registry()

    assert result["machines"] == []
    assert mr.machine_registry_is_empty() is True
    assert any("MACHINE REGISTRY EMPTY" in record.message for record in caplog.records)
    assert metric_calls == [True]


def test_copy_mutable_state_migrates_registry_before_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    live = tmp_path / "dashboard"
    live.mkdir()
    (live / "backend").mkdir()
    (live / "backend" / "machine_registry.yml").write_text(
        "version: 1\nmachines:\n  - name: hub-a\n",
        encoding="utf-8",
    )

    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "backend").mkdir()

    local = tmp_path / "local" / "machine_registry.yml"
    monkeypatch.setattr(mr, "LOCAL_REGISTRY_PATH", local)
    monkeypatch.delenv("MACHINE_REGISTRY_PATH", raising=False)

    from node_auto_deploy.transaction import copy_mutable_state  # noqa: PLC0415

    copy_mutable_state(live, candidate)

    assert local.is_file()
    assert "hub-a" in local.read_text(encoding="utf-8")
