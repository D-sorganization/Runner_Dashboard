"""Contracts for shared PowerShell subprocess helpers (deploy tests)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pwsh_host import PWSH_FILE_TIMEOUT_SECONDS, ensure_pwsh_warmed_up, find_pwsh, run_pwsh_file


def test_pwsh_file_timeout_covers_hosted_runner_cold_start() -> None:
    """Merge-group CI flakes at 10s; warm-up + 60s cap absorbs pwsh JIT on ubuntu-latest."""
    assert PWSH_FILE_TIMEOUT_SECONDS >= 30


@pytest.mark.skipif(find_pwsh() is None, reason="PowerShell not available")
def test_run_pwsh_file_executes_trivial_script(tmp_path: Path) -> None:
    pwsh = find_pwsh()
    assert pwsh is not None
    script = tmp_path / "noop.ps1"
    script.write_text("exit 0\n", encoding="utf-8")
    ensure_pwsh_warmed_up(pwsh)
    result = run_pwsh_file(pwsh, str(script))
    assert result.returncode == 0
