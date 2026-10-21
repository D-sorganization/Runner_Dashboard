"""Cold-import contracts for backend disk-guard modules (#2042)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
MODULES = (
    "runner_disk_guard_policy",
    "disk_full_cordon",
    "disk_full_cordon_runtime",
)


def _isolated_env(home: Path) -> dict[str, str]:
    env = {name: os.environ[name] for name in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP") if name in os.environ}
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["DASHBOARD_IDENTITY_DIR"] = str(home / "identity")
    env["AUTODERIVE_FLEET_NODES"] = "0"
    env["FLEET_NODES"] = ""
    env["RUNNER_DASHBOARD_SESSION_SECRET_DIR"] = str(home / ".config" / "runner-dashboard")
    env["SESSION_SECRET"] = "synthetic-test-session-secret-not-for-auth"
    env["RUNNER_DASHBOARD_DATA_DIR"] = str(home / ".local" / "share" / "runner-dashboard")
    return env


def _run_isolated(home: Path, code: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(BACKEND), *args],
        cwd=BACKEND,
        env=_isolated_env(home),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )


@pytest.mark.parametrize("module", MODULES)
def test_backend_module_imports_without_repository_pythonpath(tmp_path: Path, module: str) -> None:
    code = """
import importlib, pathlib, sys
backend = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(backend))
assert str(backend.parent) not in sys.path
importlib.import_module(sys.argv[2])
print('import-ok')
"""
    result = _run_isolated(tmp_path, code, module)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "import-ok"


def test_imports_share_canonical_types_and_parse_explicit_synthetic_config(
    tmp_path: Path,
) -> None:
    config = tmp_path / "synthetic-disk-guard.yaml"
    config.write_text(
        """version: 1
schema: runner-disk-guard/v1
updated_at: synthetic
thresholds:
  trip_min_free_gb: 20
  readmit_min_free_gb: 30
  trip_used_percent: 90
  readmit_used_percent: 80
drain:
  timeout_seconds: 60
  finish_running_jobs: true
cordon:
  min_online_after_drain: 0
  stop_accepting_jobs_on_trip: true
cleanup:
  commands:
    - argv: ["synthetic-command-not-executed"]
      description: Synthetic parse-only fixture
alerts:
  cordoned_hosts_warning_threshold: 2
""",
        encoding="utf-8",
    )
    code = """
import pathlib, sys
backend = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(backend))
import disk_full_cordon as cordon
import disk_full_cordon_runtime as runtime
import runner_disk_guard_policy as policy
assert cordon.RunnerDiskGuardConfig is policy.RunnerDiskGuardConfig
assert runtime.RunnerDiskGuardConfig is policy.RunnerDiskGuardConfig
assert cordon.DiskGuardThresholds is policy.DiskGuardThresholds
assert runtime.load_runner_disk_guard_policy is policy.load_runner_disk_guard_policy
config = policy.load_runner_disk_guard_policy(sys.argv[2])
assert config.thresholds.trip_min_free_gb == 20
assert config.drain.timeout_seconds == 60
print('contract-ok')
"""
    result = _run_isolated(tmp_path, code, str(config))

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "contract-ok"
