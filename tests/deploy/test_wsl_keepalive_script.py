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
INSTALLER = REPO_ROOT / "deploy" / "install-wsl-keepalive-task.ps1"


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


def test_resident_task_installer_exists() -> None:
    assert INSTALLER.is_file(), f"resident keepalive installer missing: {INSTALLER}"


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
        "DashboardPort",
        "DashboardServiceName",
        "Mode",
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


def test_script_validates_dashboard_recovery_parameters() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "DashboardPort must be in 1..65535" in text
    assert "DashboardServiceName must be a non-empty string" in text
    assert "dashboard_unhealthy_detected" in text
    assert "dashboard_recovery_escalating_to_wsl_reset" in text
    assert "dashboard_recovery_failed_no_wsl_reset" in text
    assert "Start-DashboardServiceOnly" in text


def test_script_has_resident_mode_without_wsl_reset() -> None:
    """Split-disk runner pools need a keep-warm mode that never resets WSL."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "[ValidateSet('Watchdog', 'Resident')]" in text
    assert "Mode must be Watchdog or Resident" in text
    assert "unresponsive_no_wsl_reset" in text


def test_script_pins_distro_with_persistent_session() -> None:
    """The idle fleet stays online only if a host-side session keeps the distro
    resident. Periodic probes attach/detach and let the distro idle-terminate
    between cycles; a persistent ``wsl --exec sleep infinity`` session does not.
    Regression guard: the watchdog loop must maintain that pin.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    assert "function Set-DistroPin" in text, "distro pin helper missing"
    assert "Set-DistroPin -Distro" in text, "main loop must call Set-DistroPin every cycle"
    assert "'/bin/sleep', 'infinity'" in text, "pin must hold a persistent sleep-infinity session"
    assert "distro_pin_started" in text, "pin (re)starts should be logged"


def test_script_defaults_to_resident_mode() -> None:
    """A runner-fleet host must default to the never-shutdown mode.

    ``Watchdog`` recovery calls ``wsl --shutdown``, which kills every distro
    and the WSL2 VM -- taking every runner and in-flight CI job with it, and
    corrupting the ext4 root on a hard kill mid-write (root-caused on OGLaptop
    2026-05-29: 415 shutdowns, 48 e2fsck errors). The canonical installer
    already passes ``-Mode Resident`` explicitly, so this default only governs
    a bare run or a legacy task that omits ``-Mode`` -- exactly the paths that
    should fail safe rather than reboot the host.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    assert "[string]$Mode = 'Resident'" in text, "Mode must default to Resident"


def test_probe_does_not_gate_on_process_exit_code() -> None:
    """Regression guard for the fleet-wide false-unresponsive bug.

    ``Start-Process -PassThru`` leaves ``ExitCode`` null when the watchdog
    runs non-interactively (scheduled task), so gating on ``$p.ExitCode -ne
    0`` declared every healthy distro unresponsive. The probe must instead
    decide success from clean exit + non-empty stdout via Test-ProbeSuccess.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    assert "function Test-ProbeSuccess" in text, "pure success helper missing"
    assert "Test-ProbeSuccess -Exited" in text, "Test-Responsive must delegate to Test-ProbeSuccess"
    assert "$p.ExitCode -ne 0" not in text, "probe must not gate on process exit code"


def test_resident_task_installer_registers_no_reset_mode() -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    assert "Register-ScheduledTask" in text
    assert "'-Mode', 'Resident'" in text
    assert "wsl --shutdown" not in text
    assert "RunnerDashboard-WSL-Resident-KeepAlive" in text
    assert "Start-ScheduledTask" in text


def test_resident_task_installer_runs_when_logged_off() -> None:
    """The keepalive holds the host-side handle that keeps the WSL VM resident.

    It MUST survive logoff (S4U principal, run whether logged on or not),
    otherwise the VM is torn down on logoff and both split-disk distros
    cold-boot together on next logon, racing WSL's ~10s boot timeout into a
    crash loop. It must also run unbounded (no 72h ExecutionTimeLimit kill) and
    start at boot, not only at logon.
    """
    text = INSTALLER.read_text(encoding="utf-8")
    assert "New-ScheduledTaskPrincipal" in text, "must register an explicit principal"
    assert "-LogonType S4U" in text, "must use S4U so it runs whether logged on or not"
    assert "-RunLevel Highest" in text
    assert "-Principal $principal" in text, "principal must be passed to Register-ScheduledTask"
    assert "[TimeSpan]::Zero" in text, "ExecutionTimeLimit must be unbounded (no 72h kill)"
    assert "New-ScheduledTaskTrigger -AtStartup" in text, "must start at boot, not only logon"


# ---------------------------------------------------------------------------
# Behavioural checks (require pwsh)
# ---------------------------------------------------------------------------


@PWSH_REQUIRED
def test_script_parses_cleanly(tmp_path: Path) -> None:
    """A syntax error would surface as a parser error here."""
    log_dir = tmp_path / "logs"
    result = _run_ps(
        f". '{SCRIPT}' -Once -Distro 'no-such-distro' "
        f"-CheckIntervalSeconds 10 -ProbeTimeoutSeconds 2 "
        f"-Mode Resident "
        f"-LogDir '{log_dir.as_posix()}'"
    )
    # We don't care about the actual outcome of the probe (it will fail
    # because no such distro exists). We only require that the parser
    # accepted the script and the parameter validation did not throw.
    # A parser error would put "ParserError" in stderr.
    assert "ParserError" not in result.stderr, result.stderr


@PWSH_REQUIRED
def test_resident_task_installer_dry_run_parses_cleanly(tmp_path: Path) -> None:
    """The scheduled-task installer must parse and expose the Resident command."""
    script_copy = tmp_path / "wsl-keepalive.ps1"
    script_copy.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    result = _run_ps(
        f"& '{INSTALLER.as_posix()}' -DryRun "
        f"-ScriptPath '{script_copy.as_posix()}' "
        "-TaskName 'UnitTest-WSL-KeepAlive' "
        "-Distro 'WSL' "
        "-CheckIntervalSeconds 10 "
        "-ProbeTimeoutSeconds 3"
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["mode"] == "Resident"
    assert "-Mode Resident" in payload["arguments"]


@PWSH_REQUIRED
def test_backoff_helper_is_pure_and_capped(tmp_path: Path) -> None:
    """``Get-BackoffSeconds`` must double per recovery and cap at 1800s."""
    # Dot-source the script via -Command so its functions become available,
    # then drive Get-BackoffSeconds for a range of inputs.
    samples_script = textwrap.dedent(
        f"""
        . '{SCRIPT.as_posix()}' -Once -Distro 'noop' `
            -CheckIntervalSeconds 10 -ProbeTimeoutSeconds 2 `
            -Mode Resident `
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
def test_probe_success_helper_ignores_exit_code(tmp_path: Path) -> None:
    """Test-ProbeSuccess must judge on exit + sentinel token, never exit code.

    R1 is the decisive regression case: a probe that exited and echoed the
    sentinel is healthy even though no exit code is supplied at all -- exactly
    the scheduled-task scenario where ExitCode came back $null and the old
    gate spuriously failed. R2 covers the other half of the bug: wsl.exe
    writes "no distribution" errors to STDOUT, so a dead distro yields
    non-empty output that must NOT be read as healthy.
    """
    token = "uid="
    healthy_stdout = "uid=1000(runner) gid=1000(runner) groups=1000(runner)"
    dead_distro_stdout = "There is no distribution with the supplied name."
    driver = textwrap.dedent(
        f"""
        . '{SCRIPT.as_posix()}' -Once -Distro 'noop' `
            -CheckIntervalSeconds 10 -ProbeTimeoutSeconds 2 `
            -Mode Resident `
            -LogDir '{(tmp_path / "logs").as_posix()}' *> $null
        $tok = '{token}'
        $ok = '{healthy_stdout}'
        $dead = '{dead_distro_stdout}'
        Write-Output ('R1=' + (Test-ProbeSuccess -Exited $true -StdoutContent $ok -ExpectedToken $tok))
        Write-Output ('R2=' + (Test-ProbeSuccess -Exited $true -StdoutContent $dead -ExpectedToken $tok))
        Write-Output ('R3=' + (Test-ProbeSuccess -Exited $true -StdoutContent $null -ExpectedToken $tok))
        Write-Output ('R4=' + (Test-ProbeSuccess -Exited $false -StdoutContent $ok -ExpectedToken $tok))
        """
    )
    result = _run_ps(driver)
    out = result.stdout
    assert "R1=True" in out, result.stdout + result.stderr
    assert "R2=False" in out, result.stdout
    assert "R3=False" in out, result.stdout
    assert "R4=False" in out, result.stdout


@PWSH_REQUIRED
def test_state_file_is_written_and_parsable(tmp_path: Path) -> None:
    """``-Once`` against a fake distro must still leave a JSON state file."""
    log_dir = tmp_path / "logs"
    result = _run_ps(
        f". '{SCRIPT.as_posix()}' -Once "
        f"-Distro 'definitely-not-installed-{tmp_path.name}' "
        f"-CheckIntervalSeconds 10 -ProbeTimeoutSeconds 2 "
        f"-Mode Resident "
        f"-LogDir '{log_dir.as_posix()}'"
    )
    assert result.returncode == 0, result.stderr
    state_path = log_dir / "wsl-keepalive-state.json"
    assert state_path.is_file(), result.stdout + result.stderr
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["distro"].startswith("definitely-not-installed-")
    assert state["status"] in {"failed", "recovered", "unresponsive"}, state
    assert state["mode"] == "Resident"
    assert isinstance(state["consecutive"], int)


@PWSH_REQUIRED
def test_log_jsonl_event_is_written(tmp_path: Path) -> None:
    """Each cycle must append at least one JSON-lines event."""
    log_dir = tmp_path / "logs"
    _run_ps(
        f". '{SCRIPT.as_posix()}' -Once "
        f"-Distro 'definitely-not-installed-{tmp_path.name}' "
        f"-CheckIntervalSeconds 10 -ProbeTimeoutSeconds 2 "
        f"-Mode Resident "
        f"-LogDir '{log_dir.as_posix()}'"
    )
    log_path = log_dir / "wsl-keepalive.log"
    assert log_path.is_file()
    lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines, "no log lines emitted"
    parsed = [json.loads(line) for line in lines]
    events = {row["event"] for row in parsed}
    # Resident mode must not reset WSL for a non-existent distro.
    assert "unresponsive_no_wsl_reset" in events, parsed
    assert "recovery_failed" not in events, parsed
    assert "recovery_succeeded" not in events, parsed
