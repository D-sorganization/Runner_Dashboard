"""Contracts for OGLaptop fleet backup pilot scripts (#4015)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from pwsh_host import find_pwsh, run_pwsh_command

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "deploy" / "fleet-backup-pilot.lib.ps1"
SCRIPT = REPO_ROOT / "deploy" / "oglaptop-fleet-backup.ps1"

PWSH = find_pwsh()
PWSH_REQUIRED = pytest.mark.skipif(PWSH is None, reason="PowerShell not available on this runner")


def test_scripts_exist_and_declare_vss_restic() -> None:
    assert LIB.is_file() and SCRIPT.is_file()
    main_text = SCRIPT.read_text(encoding="utf-8")
    lib_text = LIB.read_text(encoding="utf-8")
    assert "InstallTask" in main_text and "RESTIC_REPOSITORY" in main_text
    assert "use-fs-snapshot" in lib_text.lower()


@PWSH_REQUIRED
def test_lib_bandwidth_probe_gate() -> None:
    assert PWSH is not None
    body = textwrap.dedent(
        f"""
        . '{LIB.as_posix()}' -FunctionsOnly
        if (Test-FleetBackupPilotNeedsBandwidthProbe -State @{{}}) {{ 'yes' }} else {{ 'no' }}
        """
    )
    result = run_pwsh_command(PWSH, body)
    assert result.returncode == 0 and result.stdout.strip() == "yes"


@PWSH_REQUIRED
def test_dry_run_backup_cycle(tmp_path: Path) -> None:
    assert PWSH is not None
    config = tmp_path / "pilot.env"
    config.write_text(
        "RESTIC_REPOSITORY=rest:http://127.0.0.1:8000/oglaptop-pilot\n"
        "BACKUP_PATHS=C:\\\\data\n",
        encoding="utf-8",
    )
    state = tmp_path / "state.json"
    result = run_pwsh_command(
        PWSH,
        f"& '{SCRIPT.as_posix()}' -DryRun -ConfigFile '{config.as_posix()}' "
        f"-StateFile '{state.as_posix()}' -LogDir '{tmp_path.as_posix()}'",
    )
    assert result.returncode == 0, result.stderr
    assert "DRY-RUN restic" in result.stdout
    assert not state.exists()
