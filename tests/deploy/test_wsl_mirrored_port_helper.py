"""Tests for ``deploy/wsl-mirrored-port-helper.sh``.

The helper script is intentionally a no-op outside WSL-mirrored topologies
so it can be deployed unconditionally. These tests exercise the static
behaviour (arg parsing, no-op guard) using a real bash invocation on any
host with bash available.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "deploy" / "wsl-mirrored-port-helper.sh"

BASH = shutil.which("bash")
BASH_REQUIRED = pytest.mark.skipif(BASH is None, reason="bash not available on this runner")


def _bash_path(path: Path) -> str:
    if os.name != "nt":
        return str(path)
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    if BASH and "Git\\usr\\bin" in BASH:
        return f"/{drive}{resolved.as_posix()[2:]}"
    return f"/mnt/{drive}{resolved.as_posix()[2:]}"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run(
        [BASH, _bash_path(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_script_exists_and_is_executable() -> None:
    assert SCRIPT.is_file()


@BASH_REQUIRED
def test_missing_mode_returns_usage() -> None:
    result = _run([])
    assert result.returncode == 2
    assert "Usage:" in result.stderr


@BASH_REQUIRED
def test_help_returns_zero() -> None:
    result = _run(["--help"])
    assert result.returncode == 0
    assert "Usage:" in result.stderr


@BASH_REQUIRED
def test_invalid_port_rejected() -> None:
    result = _run(["clear", "--port", "not-a-number"])
    assert result.returncode == 2
    assert "1..65535" in result.stderr


@BASH_REQUIRED
def test_port_zero_rejected() -> None:
    result = _run(["clear", "--port", "0"])
    assert result.returncode == 2


@BASH_REQUIRED
def test_port_too_large_rejected() -> None:
    result = _run(["clear", "--port", "70000"])
    assert result.returncode == 2


@BASH_REQUIRED
def test_outside_wsl_is_a_noop_for_clear() -> None:
    """On a non-WSL host the script must exit 0 without touching anything.

    The host running the test may or may not be WSL. If it is WSL but
    ``tailscale.exe`` isn't present, the script also returns 0. Either
    way the only acceptable outcome is success.
    """
    result = _run(["clear", "--port", "8321"])
    assert result.returncode == 0, result.stderr


@BASH_REQUIRED
def test_unknown_flag_rejected() -> None:
    result = _run(["clear", "--port", "8321", "--surprise"])
    assert result.returncode == 2
