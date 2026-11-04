"""Contracts for OGLaptop WSL export into restic (#4011)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from pwsh_host import find_pwsh, run_pwsh_command

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "deploy" / "fleet-backup-wsl-export.lib.ps1"
SCRIPT = REPO_ROOT / "deploy" / "oglaptop-fleet-backup-wsl-export.ps1"
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "oglaptop-fleet-backup-wsl-export.md"

PWSH = find_pwsh()
PWSH_REQUIRED = pytest.mark.skipif(PWSH is None, reason="PowerShell not available on this runner")


def test_wsl_export_artifacts_exist() -> None:
    assert LIB.is_file() and SCRIPT.is_file() and RUNBOOK.is_file()
    text = SCRIPT.read_text(encoding="utf-8")
    assert "DryRun" in text and "wsl.exe --export" in text
    assert "Test-WslExportTarFooterComplete" in LIB.read_text(encoding="utf-8")


def test_runbook_documents_broken_wsl_deferral() -> None:
    body = RUNBOOK.read_text(encoding="utf-8")
    assert "broken WSL" in body.lower() or "broken" in body.lower()
    assert "-DryRun" in body


@PWSH_REQUIRED
def test_tar_footer_complete_detects_truncated_export(tmp_path: Path) -> None:
    assert PWSH is not None
    good = tmp_path / "good.tar"
    good.write_bytes(b"payload" + (b"\x00" * 1024))
    bad = tmp_path / "bad.tar"
    bad.write_bytes(b"payload" + (b"\x00" * 100))

    body = textwrap.dedent(
        f"""
        . '{LIB.as_posix()}' -FunctionsOnly
        if (Test-WslExportTarFooterComplete -TarPath '{good.as_posix()}') {{ 'good-ok' }} else {{ 'good-bad' }}
        if (Test-WslExportTarFooterComplete -TarPath '{bad.as_posix()}') {{ 'bad-ok' }} else {{ 'bad-bad' }}
        """
    )
    result = run_pwsh_command(PWSH, body)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].strip() == "good-ok"
    assert lines[1].strip() == "bad-bad"


@PWSH_REQUIRED
def test_dry_run_does_not_require_wsl_or_restic(tmp_path: Path) -> None:
    assert PWSH is not None
    config = tmp_path / "wsl-export.env"
    config.write_text(
        "RESTIC_REPOSITORY=rest:http://127.0.0.1:8000/oglaptop-pilot\n"
        "WSL_DISTRO_NAME=Ubuntu\n",
        encoding="utf-8",
    )
    export_dir = tmp_path / "exports"
    state = tmp_path / "state.json"
    result = run_pwsh_command(
        PWSH,
        f"& '{SCRIPT.as_posix()}' -DryRun -ConfigFile '{config.as_posix()}' "
        f"-ExportDir '{export_dir.as_posix()}' -StateFile '{state.as_posix()}'",
    )
    assert result.returncode == 0, result.stderr
    assert "DRY-RUN wsl.exe --export" in result.stdout
    assert "DRY-RUN restic" in result.stdout
    assert not state.exists()
    assert not export_dir.exists()
