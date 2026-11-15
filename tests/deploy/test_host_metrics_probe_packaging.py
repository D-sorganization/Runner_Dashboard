"""Packaging and deploy-path contracts for host-metrics-probe (#4443)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy"


def test_host_metrics_probe_is_executable_in_git_index() -> None:
  out = subprocess.run(
      ["git", "ls-files", "-s", "deploy/host-metrics-probe.py"],
      cwd=REPO,
      capture_output=True,
      text=True,
      check=True,
  ).stdout.strip()
  assert out, "deploy/host-metrics-probe.py must be tracked"
  mode = out.split()[0]
  assert mode == "100755", f"expected git mode 100755, got {mode}"


def test_update_deployed_copies_host_metrics_probe_into_deploy_dir() -> None:
  src = (DEPLOY / "update-deployed.sh").read_text(encoding="utf-8")
  assert 'cp "$REPO/deploy/host-metrics-probe.py"' in src
  assert 'chmod 0755 "$DEPLOY_DIR/deploy/host-metrics-probe.py"' in src


def test_package_dashboard_artifact_chmods_host_metrics_probe() -> None:
  src = (DEPLOY / "package-dashboard-artifact.sh").read_text(encoding="utf-8")
  assert "host-metrics-probe.py" in src


def test_install_dashboard_artifact_chmods_host_metrics_probe() -> None:
  src = (DEPLOY / "install-dashboard-artifact.sh").read_text(encoding="utf-8")
  assert "host-metrics-probe.py" in src


def test_setup_sh_installs_host_metrics_probe_timer() -> None:
  setup = (DEPLOY / "setup.sh").read_text(encoding="utf-8")
  helper = (DEPLOY / "setup-host-metrics-probe.sh").read_text(encoding="utf-8")
  assert "setup_install_host_metrics_probe" in setup
  assert "install-host-metrics-probe.sh" in helper


def test_installer_does_not_require_execute_bit_before_install() -> None:
  install = (DEPLOY / "install-host-metrics-probe.sh").read_text(encoding="utf-8")
  assert "[[ -x \"${DEPLOY_DIR}/deploy/host-metrics-probe.py\" ]]" not in install
  assert "install -m 0755" in install and "host-metrics-probe.py" in install