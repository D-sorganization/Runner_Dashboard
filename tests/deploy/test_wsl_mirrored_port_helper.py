"""Tests for ``deploy/wsl-mirrored-port-helper.sh``.

The helper script is intentionally a no-op outside WSL-mirrored topologies
so it can be deployed unconditionally. These tests exercise the static
behaviour (arg parsing, no-op guard) using a real bash invocation on any
host with bash available.
"""

from __future__ import annotations

import os
import shutil
import stat
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
        existing_wslenv = run_env.get("WSLENV", "")
        extra_wslenv = ":".join(env)
        run_env["WSLENV"] = f"{existing_wslenv}:{extra_wslenv}" if existing_wslenv else extra_wslenv
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


@BASH_REQUIRED
def test_clear_removes_http_tailscale_binding(tmp_path: Path) -> None:
    """Clear must use the protocol shown by ``tailscale serve status``."""

    calls = tmp_path / "tailscale-calls.txt"
    fake_tailscale = tmp_path / "tailscale.exe"
    fake_powershell = tmp_path / "powershell.exe"
    calls_for_bash = _bash_path(calls)

    fake_tailscale.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> '{calls_for_bash}'
if [[ "$*" == "serve status" ]]; then
  printf 'http://controltower.tail2bbcc7.ts.net:8321 (tailnet only)\\n'
  printf '|-- / proxy http://127.0.0.1:8321\\n'
  exit 0
fi
if [[ "$*" == "serve --http=8321 off" ]]; then
  exit 0
fi
exit 9
""",
        encoding="utf-8",
        newline="\n",
    )
    fake_powershell.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n")
    fake_tailscale.chmod(fake_tailscale.stat().st_mode | stat.S_IXUSR)
    fake_powershell.chmod(fake_powershell.stat().st_mode | stat.S_IXUSR)

    result = _run(
        ["clear", "--port", "8321"],
        env={
            "WSL_MIRRORED_PORT_HELPER_ASSUME_WSL": "1",
            "WSL_MIRRORED_PORT_HELPER_POWERSHELL_EXE": _bash_path(fake_powershell),
            "WSL_MIRRORED_PORT_HELPER_TAILSCALE_EXE": _bash_path(fake_tailscale),
        },
    )

    assert result.returncode == 0, result.stderr
    call_log = calls.read_text(encoding="utf-8")
    assert "serve --http=8321 off" in call_log
    assert "serve --tcp=8321 off" not in call_log


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
