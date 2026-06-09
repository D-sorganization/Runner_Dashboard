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


def _check_bash_working() -> bool:
    bash_path = shutil.which("bash")
    if not bash_path:
        return False
    try:
        # Verify bash can run a simple exit command without error
        res = subprocess.run([bash_path, "-c", "exit 0"], capture_output=True, text=True, timeout=2)
        return res.returncode == 0
    except Exception:
        return False


BASH = shutil.which("bash")
BASH_WORKING = _check_bash_working()
BASH_REQUIRED = pytest.mark.skipif(not BASH_WORKING, reason="bash not working or not available on this runner")


def _bash_path(path: Path) -> str:
    if os.name != "nt":
        return str(path)
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    if BASH and "Git\\usr\\bin" in BASH:
        return f"/{drive}{resolved.as_posix()[2:]}"
    return f"/mnt/{drive}{resolved.as_posix()[2:]}"


def _run(args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        [BASH, _bash_path(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        env=run_env,
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
    """Unavailable Windows interop tools must make clear a no-op.

    Self-hosted CI runners can be WSL-like and may expose Windows binaries.
    Force the existing override hooks to non-existent commands so this
    contract stays hermetic and never touches host Tailscale state.
    """
    result = _run(
        ["clear", "--port", "8321"],
        env={
            "WSL_MIRRORED_PORT_HELPER_POWERSHELL_EXE": "__missing_powershell_for_test__",
            "WSL_MIRRORED_PORT_HELPER_TAILSCALE_EXE": "__missing_tailscale_for_test__",
        },
    )
    assert result.returncode == 0, result.stderr


@BASH_REQUIRED
def test_unknown_flag_rejected() -> None:
    result = _run(["clear", "--port", "8321", "--surprise"])
    assert result.returncode == 2


def test_clear_removes_windows_portproxy_before_tailscale_binding() -> None:
    """Regression for WSL2 mirrored networking bind churn.

    A Windows ``netsh interface portproxy`` listener appears in WSL as an
    invisible ``svchost`` port holder, so the helper must clear it before
    systemd starts uvicorn.
    """
    content = SCRIPT.read_text(encoding="utf-8")
    assert 'TAILSCALE_EXE="${WSL_MIRRORED_PORT_HELPER_TAILSCALE_EXE:-' in content
    assert 'POWERSHELL_EXE="${WSL_MIRRORED_PORT_HELPER_POWERSHELL_EXE:-powershell.exe}"' in content
    assert "WSL_MIRRORED_PORT_HELPER_ASSUME_WSL:-0" in content
    assert "clear_windows_portproxy" in content
    assert "foreach (\\$addr in @('0.0.0.0', '127.0.0.1'))" in content
    assert "& netsh interface portproxy delete v4tov4 listenport=$PORT listenaddress=\\$addr" in content
    assert content.index("clear_windows_portproxy") < content.index("tailscale serve --tcp=$PORT off")
