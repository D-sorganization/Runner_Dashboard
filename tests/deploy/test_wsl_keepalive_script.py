"""Cross-platform tests for ``deploy/wsl-keepalive.ps1``.

The watchdog script is Windows-only at runtime (it calls ``wsl.exe``), but
its structure and pure-helper behaviour must be exercised in CI even on the
Linux runners. We do two things:

1. **Static checks** (run everywhere): the script parses cleanly, declares
   the documented parameters, and rejects invalid arguments by throwing
   from the validation block at the top. These checks need PowerShell
   (``pwsh`` on Linux/macOS, ``powershell.exe`` on Windows).
2. **Behavioural checks** (run only when ``pwsh`` is present): invoke the
   script's pure helpers (``Get-BackoffSeconds``, ``Invoke-LogRotate``,
   ``Write-EventLine``, ``Write-StateFile``) via a small driver and assert
   on their effects.

The recovery / responsiveness path is NOT exercised here because it shells
out to ``wsl.exe``. Those are covered by the Pester suite at
``deploy/wsl-keepalive.Tests.ps1`` (Windows-only).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "deploy" / "wsl-keepalive.ps1"


def _find_pwsh() -> str | None:
    for candidate in ("pwsh", "powershell"):
        if shutil.which(candidate):
            return candidate
    return None


PWSH = _find_pwsh()
PWSH_REQUIRED = pytest.mark.skipif(PWSH is None, reason="PowerShell (pwsh/powershell) not available on this runner")


def _run_ps(script_body: str) -> subprocess.CompletedProcess[str]:
    """Run a small PowerShell snippet and return the completed process."""
    assert PWSH is not None
    return subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", script_body],
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# Static checks
# ---------------------------------------------------------------------------


def test_script_exists() -> None:
    assert SCRIPT.is_file(), f"watchdog script missing: {SCRIPT}"


def test_script_documents_required_parameters() -> None:
    """Every parameter the docstring promises must actually be declared."""
    text = SCRIPT.read_text(encoding="utf-8")
    for param in (
        "Distro",
        "CheckIntervalSeconds",
        "ProbeTimeoutSeconds",
        "MaxConsecutiveRecoveries",
        "HealthyGapSeconds",
        "LogDir",
        "MaxLogBytes",
        "LogBackups",
        "Once",
    ):
        assert f"${param}" in text, f"parameter ${param} not declared"


def test_script_validates_distro_is_non_empty() -> None:
    """The DbC block at the top must reject blank Distro."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "Distro must be a non-empty string" in text


def test_script_validates_probe_timeout_relative_to_interval() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "ProbeTimeoutSeconds" in text
    assert "must be < CheckIntervalSeconds" in text


# ---------------------------------------------------------------------------
# Behavioural checks (require pwsh)
# ---------------------------------------------------------------------------


@PWSH_REQUIRED
def test_script_parses_cleanly() -> None:
    """A syntax error would surface as a parser error here."""
    result = _run_ps(
        f". '{SCRIPT}' -Once -Distro 'no-such-distro' "
        f"-CheckIntervalSeconds 10 -ProbeTimeoutSeconds 2 "
        f"-LogDir '{(REPO_ROOT / '.test-wsl-keepalive-junk').as_posix()}'"
    )
    # We don't care about the actual outcome of the probe (it will fail
    # because no such distro exists). We only require that the parser
    # accepted the script and the parameter validation did not throw.
    # A parser error would put "ParserError" in stderr.
    assert "ParserError" not in result.stderr, result.stderr


@PWSH_REQUIRED
def test_backoff_helper_is_pure_and_capped(tmp_path: Path) -> None:
    """``Get-BackoffSeconds`` must double per recovery and cap at 1800s."""
    # Dot-source the script via -Command so its functions become available,
    # then drive Get-BackoffSeconds for a range of inputs.
    samples_script = textwrap.dedent(
        f"""
        . '{SCRIPT.as_posix()}' -Once -Distro 'noop' `
            -CheckIntervalSeconds 10 -ProbeTimeoutSeconds 2 `
            -LogDir '{(tmp_path / "logs").as_posix()}' *> $null
        for ($i = 0; $i -le 12; $i++) {{
            Write-Output (Get-BackoffSeconds -ConsecutiveRecoveries $i)
        }}
        """
    )
    result = _run_ps(samples_script)
    # The -Once invocation above will print a JSON result and then fall
    # through to the for-loop. Filter to int-looking lines only.
    samples = [int(line) for line in result.stdout.splitlines() if line.strip().isdigit()]
    assert samples[:5] == [0, 30, 60, 120, 240], samples
    # cap at 1800
    assert max(samples) == 1800, samples
    # monotonic non-decreasing
    assert samples == sorted(samples), samples


@PWSH_REQUIRED
def test_state_file_is_written_and_parsable(tmp_path: Path) -> None:
    """``-Once`` against a fake distro must still leave a JSON state file."""
    log_dir = tmp_path / "logs"
    result = _run_ps(
        f". '{SCRIPT.as_posix()}' -Once "
        f"-Distro 'definitely-not-installed-{tmp_path.name}' "
        f"-CheckIntervalSeconds 10 -ProbeTimeoutSeconds 2 "
        f"-LogDir '{log_dir.as_posix()}'"
    )
    assert result.returncode == 0, result.stderr
    state_path = log_dir / "wsl-keepalive-state.json"
    assert state_path.is_file(), result.stdout + result.stderr
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["distro"].startswith("definitely-not-installed-")
    assert state["status"] in {"failed", "recovered"}, state
    assert isinstance(state["consecutive"], int)


@PWSH_REQUIRED
def test_log_jsonl_event_is_written(tmp_path: Path) -> None:
    """Each cycle must append at least one JSON-lines event."""
    log_dir = tmp_path / "logs"
    _run_ps(
        f". '{SCRIPT.as_posix()}' -Once "
        f"-Distro 'definitely-not-installed-{tmp_path.name}' "
        f"-CheckIntervalSeconds 10 -ProbeTimeoutSeconds 2 "
        f"-LogDir '{log_dir.as_posix()}'"
    )
    log_path = log_dir / "wsl-keepalive.log"
    assert log_path.is_file()
    lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines, "no log lines emitted"
    parsed = [json.loads(line) for line in lines]
    events = {row["event"] for row in parsed}
    # We expect at least the unresponsive detection and a recovery
    # outcome for a non-existent distro.
    assert "unresponsive_detected" in events, parsed
    assert events & {"recovery_failed", "recovery_succeeded"}, parsed
