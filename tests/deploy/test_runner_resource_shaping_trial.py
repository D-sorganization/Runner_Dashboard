"""Contract tests for opt-in per-runner resource shaping (#4312)."""

from __future__ import annotations

import configparser
import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.deploy.bash_host import BASH, as_bash_path

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy"
CONFIG = REPO / "config" / "resource-shaping-trial.json"
SHAPING_DROPIN = DEPLOY / "systemd-dropins" / "15-runner-resource-shaping.conf"
MEMORY_HIGH_DROPIN = DEPLOY / "systemd-dropins" / "15-runner-resource-shaping-memory-high.conf"
SLICE_UNIT = DEPLOY / "systemd-dropins" / "runners.slice"
CONFIGURE = DEPLOY / "configure-runner-resource-shaping.sh"
WSL_EXAMPLE = REPO / "config" / "wslconfig-resource-shaping-trial.example"
RUNBOOK = REPO / "docs" / "runbooks" / "resource-shaping-trial.md"


def _parse_systemd_unit(path: Path) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(strict=False)
    cp.read_string(path.read_text(encoding="utf-8"))
    return cp


def test_config_default_off() -> None:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert data.get("enabled") is False


def test_systemd_dropin_has_agreed_weights_not_hard_caps() -> None:
    assert SHAPING_DROPIN.is_file()
    text = SHAPING_DROPIN.read_text(encoding="utf-8")
    assert "CPUWeight=20" in text
    assert "IOWeight=20" in text
    assert "Nice=10" in text
    assert "Slice=runners.slice" in text
    assert not any(line.strip().startswith("CPUQuota=") for line in text.splitlines())
    assert not any(line.strip().startswith("MemoryMax=") for line in text.splitlines())


def test_memory_high_dropin_is_separate_optional_phase() -> None:
    assert MEMORY_HIGH_DROPIN.is_file()
    text = MEMORY_HIGH_DROPIN.read_text(encoding="utf-8")
    assert "MemoryHigh=" in text
    assert not any(line.strip().startswith("MemoryMax=") for line in text.splitlines())


def test_runners_slice_unit_exists() -> None:
    assert SLICE_UNIT.is_file()
    cp = _parse_systemd_unit(SLICE_UNIT)
    assert cp.has_section("Slice") or cp.has_section("Unit")


def test_wslconfig_example_documents_optional_trial() -> None:
    assert WSL_EXAMPLE.is_file()
    text = WSL_EXAMPLE.read_text(encoding="utf-8")
    config = _parse_systemd_unit(WSL_EXAMPLE)
    assert config.get("experimental", "autoMemoryReclaim") == "gradual"
    assert "autoMemoryReclaim" in text
    assert "memory=" in text.lower() or "memory =" in text.lower()


def test_runbook_mentions_go_no_go_harness() -> None:
    assert RUNBOOK.is_file()
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "go/no-go" in text.lower() or "go / no-go" in text.lower()
    assert "configure-runner-resource-shaping" in text


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_configure_script_syntax() -> None:
    assert CONFIGURE.is_file()
    out = subprocess.run(
        [BASH, "-n", as_bash_path(CONFIGURE)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO),
    )
    assert out.returncode == 0, out.stderr


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_configure_script_noops_when_disabled(tmp_path: Path) -> None:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg["enabled"] = False
    cfg_path = tmp_path / "resource-shaping-trial.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    runner_unit = tmp_path / "actions.runner.test.host-runner.service.d"
    runner_unit.mkdir(parents=True)
    script = f"""
set -euo pipefail
export RESOURCE_SHAPING_TRIAL_CONFIG={as_bash_path(cfg_path)}
export SYSTEMD_UNIT_DROPIN_ROOT={as_bash_path(tmp_path)}
export SYSTEMD_SLICE_ROOT={as_bash_path(tmp_path)}
source {as_bash_path(CONFIGURE)}
main --dry-run
"""
    out = subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO),
    )
    assert out.returncode == 0, out.stderr + out.stdout
    assert "disabled" in (out.stdout + out.stderr).lower()


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_configure_uses_settings_and_removes_disabled_memory_phase(tmp_path: Path) -> None:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg["enabled"] = True
    cfg["systemd"].update(
        cpu_weight=35, io_weight=45, nice=5, slice_name="trial.slice", memory_high_enabled=True, memory_high="2G"
    )
    cfg_path = tmp_path / "trial.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    bin_dir = tmp_path / "trial-bin"
    bin_dir.mkdir()
    systemctl = bin_dir / "systemctl"
    systemctl.write_text(
        '#!/bin/sh\nif [ "$1" = list-unit-files ]; then\n  echo "actions.runner.test.service enabled"\nfi\n'
    )
    systemctl.chmod(0o755)
    env = dict(
        os.environ,
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        RESOURCE_SHAPING_TRIAL_CONFIG=as_bash_path(cfg_path),
        SYSTEMD_UNIT_DROPIN_ROOT=as_bash_path(tmp_path / "units"),
        SYSTEMD_SLICE_ROOT=as_bash_path(tmp_path / "slices"),
    )
    unit_dir = tmp_path / "units" / "actions.runner.test.d"
    memory_file = unit_dir / MEMORY_HIGH_DROPIN.name

    def run() -> None:
        result = subprocess.run([BASH, as_bash_path(CONFIGURE)], env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr

    result = subprocess.run([BASH, as_bash_path(CONFIGURE), "--dry-run"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not unit_dir.exists()
    run()
    text = (unit_dir / SHAPING_DROPIN.name).read_text()
    for setting in ("CPUWeight=35", "IOWeight=45", "Nice=5", "Slice=trial.slice"):
        assert setting in text
    assert "MemoryHigh=2G" in memory_file.read_text()
    cfg["systemd"]["memory_high_enabled"] = False
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    run()
    assert not memory_file.exists()
    cfg["enabled"] = False
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    run()
    assert not (unit_dir / SHAPING_DROPIN.name).exists()
