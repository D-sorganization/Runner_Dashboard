"""Contract tests for opt-in per-runner resource shaping (#4312)."""

from __future__ import annotations

import configparser
import json
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
