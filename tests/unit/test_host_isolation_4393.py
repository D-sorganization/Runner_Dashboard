"""The suite never reaches the host's ``gh`` or its operator machine registry (#4393)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import machine_registry
import pytest


@pytest.mark.unit
def test_gh_on_path_is_a_failing_stub() -> None:
    gh = shutil.which("gh")
    assert gh is not None, "conftest must put a gh stub on PATH"
    assert Path(gh).parent.name.startswith("gh-stub"), f"tests would run the host's gh: {gh}"
    proc = subprocess.run([gh, "api", "user"], capture_output=True, text=True, check=False, timeout=10)  # noqa: S603
    assert proc.returncode != 0
    assert "stubbed" in proc.stderr


@pytest.mark.unit
def test_machine_registry_never_resolves_the_operator_file() -> None:
    operator_file = Path("~/.config/runner-dashboard/machine_registry.yml").expanduser()
    session_config_dir = Path(os.environ["RUNNER_DASHBOARD_CONFIG_DIR"])
    assert "MACHINE_REGISTRY_PATH" not in os.environ
    assert machine_registry.LOCAL_REGISTRY_PATH != operator_file
    assert machine_registry.LOCAL_REGISTRY_PATH.parent == session_config_dir
    resolved, _source = machine_registry.resolve_registry_source(None)
    assert resolved != operator_file


@pytest.mark.unit
def test_fleet_gh_minter_never_reads_the_operator_env_file() -> None:
    env_file = Path(os.environ.get("FLEET_GH_ENV_FILE", ""))
    assert env_file.parent == Path(os.environ["RUNNER_DASHBOARD_CONFIG_DIR"])
    assert not env_file.exists()
