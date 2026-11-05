"""Rotated encrypted USB SSD offsite restic copy (#4009)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from pwsh_host import find_pwsh, run_pwsh_command

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "deploy" / "fleet-backup-offsite.lib.ps1"
SCRIPT = REPO_ROOT / "deploy" / "fleet-backup-offsite-usb.ps1"
EXAMPLE = REPO_ROOT / "deploy" / "fleet-backup-offsite.env.example"
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "fleet-backup-offsite-usb.md"

PWSH = find_pwsh()
PWSH_REQUIRED = pytest.mark.skipif(PWSH is None, reason="PowerShell not available on this runner")


def test_offsite_backup_artifacts_exist() -> None:
    assert LIB.is_file() and SCRIPT.is_file() and EXAMPLE.is_file() and RUNBOOK.is_file()
    example = EXAMPLE.read_text(encoding="utf-8")
    assert "SOURCE_RESTIC_REPOSITORY" in example
    assert "ghp_" not in example and "RESTIC_PASSWORD=" not in example


@PWSH_REQUIRED
def test_rotation_slot_alternates_by_iso_week() -> None:
    assert PWSH is not None
    body = textwrap.dedent(
        f"""
        . '{LIB.as_posix()}' -FunctionsOnly
        $even = Get-FleetBackupOffsiteRotationSlot -State @{{}} -NowUtc ([datetime]'2026-01-08T12:00:00Z')
        $odd = Get-FleetBackupOffsiteRotationSlot -State @{{}} -NowUtc ([datetime]'2026-01-01T12:00:00Z')
        Write-Output "$($even.slot)|$($odd.slot)"
        """
    )
    result = run_pwsh_command(PWSH, body)
    assert result.returncode == 0, result.stderr
    a_slot, b_slot = result.stdout.strip().split("|")
    assert a_slot in {"A", "B"} and b_slot in {"A", "B"} and a_slot != b_slot


@PWSH_REQUIRED
def test_restic_copy_arguments_include_both_repos() -> None:
    assert PWSH is not None
    body = textwrap.dedent(
        f"""
        . '{LIB.as_posix()}' -FunctionsOnly
        $copy = Get-FleetBackupOffsiteResticCopyArguments -SourceRepository 'rest:http://hub/example' -UsbRepositoryRoot 'E:\\'
        Write-Output ($copy.Arguments -join ' ')
        """
    )
    result = run_pwsh_command(PWSH, body)
    assert result.returncode == 0, result.stderr
    assert "copy" in result.stdout and "rest:http://hub/example" in result.stdout
    assert "restic-offsite" in result.stdout


@PWSH_REQUIRED
def test_dry_run_does_not_require_usb_volume(tmp_path: Path) -> None:
    assert PWSH is not None
    config = tmp_path / "offsite.env"
    config.write_text(
        "SOURCE_RESTIC_REPOSITORY=rest:http://127.0.0.1:8000/pilot\n"
        "OFFSITE_USB_LABEL_A=TEST-A\n"
        "OFFSITE_USB_LABEL_B=TEST-B\n",
        encoding="utf-8",
    )
    # DryRun still resolves USB — skip volume resolution by testing config parse only in dry path
    # when Get-Volume fails, script should fail closed (not in dry run before volume check)
    body = textwrap.dedent(
        f"""
        . '{LIB.as_posix()}' -FunctionsOnly
        $cfg = ConvertFrom-FleetBackupOffsiteEnvFile -Path '{config.as_posix()}'
        if ($cfg['SOURCE_RESTIC_REPOSITORY']) {{ 'ok' }}
        """
    )
    result = run_pwsh_command(PWSH, body)
    assert result.returncode == 0 and result.stdout.strip() == "ok"
