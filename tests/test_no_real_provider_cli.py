"""Regression coverage for the suite-wide real-provider process guard."""

import subprocess

import pytest


@pytest.mark.unit
@pytest.mark.parametrize("shell", [False, True])
def test_autouse_guard_rejects_provider_cli_execution(shell: bool) -> None:
    with pytest.raises(AssertionError, match="must not execute a real provider CLI: codex"):
        command = "codex exec sentinel" if shell else ["codex", "exec", "sentinel"]
        subprocess.run(command, shell=shell, check=False)
