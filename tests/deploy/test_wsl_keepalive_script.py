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


def _enable_marker(tmp_path: Path) -> Path:
    marker = tmp_path / "deskcomputer-runner-enabled.flag"
    marker.write_text("enabled\n", encoding="utf-8")
    return marker


def test_drain_marker_exits_before_validation_or_recovery(tmp_path: Path) -> None:
    """A deliberate host drain must prevent WSL warm-up and recovery."""
    marker = tmp_path / "deskcomputer-runner-drained.flag"
    marker.write_text("drained\n", encoding="utf-8")
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell not available")
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(SCRIPT),
            "-DrainMarker",
            str(marker),
            "-CheckIntervalSeconds",
            "1",
            "-Once",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_missing_enable_marker_exits_before_validation_or_recovery(tmp_path: Path) -> None:
    """Automatic recovery requires an explicit operator enable marker."""
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell not available")
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(SCRIPT),
            "-DrainMarker",
            str(tmp_path / "no-drain.flag"),
            "-EnableMarker",
            str(tmp_path / "no-enable.flag"),
            "-CheckIntervalSeconds",
            "1",
            "-Once",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_keepalive_and_monitor_share_default_operator_markers() -> None:
    keepalive_text = SCRIPT.read_text(encoding="utf-8")
    monitor_text = (REPO_ROOT / "deploy" / "fleet-health-monitor.ps1").read_text(encoding="utf-8")
    marker_suffixes = (
        r"runner_fleet_monitor\deskcomputer-runner-drained.flag",
        r"runner_fleet_monitor\deskcomputer-runner-enabled.flag",
    )
    for text in (keepalive_text, monitor_text):
        assert "[Environment]::GetFolderPath('UserProfile')" in text
        for marker_suffix in marker_suffixes:
            assert marker_suffix in text


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
        "EmergencyOverride",
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
    assert "dashboard_recovery_failed_no_wsl_reset" in text
    assert "Start-DashboardServiceOnly" in text


def test_dashboard_health_failure_never_escalates_to_wsl_reset() -> None:
    """Issue #1067: Dashboard health failure must remain isolated from runner services."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "dashboard_recovery_escalating_to_wsl_reset" not in text
    assert "dashboard_recovery_after_wsl_reset" not in text


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


def test_resident_task_installer_requires_interactive_principal() -> None:
    """Issue #1139: The keepalive must run under an interactive user principal.

    SYSTEM is rejected with WSL_E_LOCAL_SYSTEM_NOT_SUPPORTED and S4U lacks
    user session context to access user-scoped WSL registrations. It must also
    run unbounded (no 72h ExecutionTimeLimit kill) and start at boot/logon.
    """
    text = INSTALLER.read_text(encoding="utf-8")
    assert "New-ScheduledTaskPrincipal" in text, "must register an explicit principal"
    assert "-LogonType $LogonType" in text, "must use dynamic LogonType parameter defaulting to Interactive"
    assert "[ValidateSet('Interactive', 'InteractiveToken', 'S4U', 'SYSTEM')][string]$LogonType = 'Interactive'" in text
    assert "Test-WslPrincipalCompatible" in text, "must validate principal compatibility"
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
    """The scheduled-task installer must parse and expose the Resident command with Interactive logon."""
    script_copy = tmp_path / "wsl-keepalive.ps1"
    script_copy.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    result = _run_ps(
        f"& '{INSTALLER.as_posix()}' -DryRun "
        f"-ScriptPath '{script_copy.as_posix()}' "
        "-TaskName 'UnitTest-WSL-KeepAlive' "
        "-Distro 'WSL' "
        "-RunAsUser 'testrunner' "
        "-CheckIntervalSeconds 10 "
        "-ProbeTimeoutSeconds 3"
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["mode"] == "Resident"
    assert payload["logon_type"] == "Interactive"
    assert payload["runas_user"] == "testrunner"
    assert "-Mode Resident" in payload["arguments"]


@PWSH_REQUIRED
def test_resident_task_installer_rejects_system_principal(tmp_path: Path) -> None:
    """Issue #1139: SYSTEM principal must be rejected with fail-closed error."""
    script_copy = tmp_path / "wsl-keepalive.ps1"
    script_copy.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    result = _run_ps(
        f"& '{INSTALLER.as_posix()}' -DryRun "
        f"-ScriptPath '{script_copy.as_posix()}' "
        "-TaskName 'UnitTest-WSL-KeepAlive' "
        "-Distro 'WSL' "
        "-RunAsUser 'SYSTEM' "
    )
    assert result.returncode != 0
    assert "WSL_E_LOCAL_SYSTEM_NOT_SUPPORTED" in result.stderr


@PWSH_REQUIRED
def test_resident_task_installer_rejects_s4u_logon_type(tmp_path: Path) -> None:
    """Issue #1139: S4U logon type must be rejected as incompatible with user WSL."""
    script_copy = tmp_path / "wsl-keepalive.ps1"
    script_copy.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    result = _run_ps(
        f"& '{INSTALLER.as_posix()}' -DryRun "
        f"-ScriptPath '{script_copy.as_posix()}' "
        "-TaskName 'UnitTest-WSL-KeepAlive' "
        "-Distro 'WSL' "
        "-RunAsUser 'testrunner' "
        "-LogonType 'S4U' "
    )
    assert result.returncode != 0
    assert "S4U is unsupported" in result.stderr


@PWSH_REQUIRED
def test_wsl_principal_compatible_helper(tmp_path: Path) -> None:
    """Issue #1139: Test-WslPrincipalCompatible must distinguish SYSTEM, S4U, and Interactive."""
    driver = textwrap.dedent(
        f"""
        . '{INSTALLER.as_posix()}' -DryRun -RunAsUser 'testrunner' *> $null
        $c1 = Test-WslPrincipalCompatible -User 'SYSTEM' -LogonType 'Interactive'
        $c2 = Test-WslPrincipalCompatible -User 'NT AUTHORITY\\SYSTEM' -LogonType 'Interactive'
        $c3 = Test-WslPrincipalCompatible -User 'testuser' -LogonType 'S4U'
        $c4 = Test-WslPrincipalCompatible -User 'testuser' -LogonType 'Interactive'
        $c5 = Test-WslPrincipalCompatible -User 'testuser' -LogonType 'InteractiveToken'
        $c6 = Test-WslPrincipalCompatible -User '' -LogonType 'Interactive'

        Write-Output ('C1=' + $c1.Compatible + ';REASON=' + $c1.Reason)
        Write-Output ('C2=' + $c2.Compatible + ';REASON=' + $c2.Reason)
        Write-Output ('C3=' + $c3.Compatible + ';REASON=' + $c3.Reason)
        Write-Output ('C4=' + $c4.Compatible + ';REASON=' + $c4.Reason)
        Write-Output ('C5=' + $c5.Compatible + ';REASON=' + $c5.Reason)
        Write-Output ('C6=' + $c6.Compatible + ';REASON=' + $c6.Reason)
        """
    )
    result = _run_ps(driver)
    out = result.stdout
    assert "C1=False" in out and "WSL_E_LOCAL_SYSTEM_NOT_SUPPORTED" in out, out
    assert "C2=False" in out and "WSL_E_LOCAL_SYSTEM_NOT_SUPPORTED" in out, out
    assert "C3=False" in out and "S4U is unsupported" in out, out
    assert "C4=True" in out, out
    assert "C5=True" in out, out
    assert "C6=False" in out, out


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
            -DrainMarker '{(tmp_path / "no-drain.flag").as_posix()}' `
            -EnableMarker '{_enable_marker(tmp_path).as_posix()}' `
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
            -DrainMarker '{(tmp_path / "no-drain.flag").as_posix()}' `
            -EnableMarker '{_enable_marker(tmp_path).as_posix()}' `
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
        f"-DrainMarker '{(tmp_path / 'no-drain.flag').as_posix()}' "
        f"-EnableMarker '{_enable_marker(tmp_path).as_posix()}' "
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
        f"-DrainMarker '{(tmp_path / 'no-drain.flag').as_posix()}' "
        f"-EnableMarker '{_enable_marker(tmp_path).as_posix()}' "
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


@PWSH_REQUIRED
def test_teardown_allowed_interlock_pure_helper(tmp_path: Path) -> None:
    """Issue #1067: Test-WslTeardownAllowed must deny teardown when workers are active unless override is set."""
    driver = textwrap.dedent(
        f"""
        . '{SCRIPT.as_posix()}' -Once -Distro 'noop' `
            -CheckIntervalSeconds 10 -ProbeTimeoutSeconds 2 `
            -Mode Resident `
            -DrainMarker '{(tmp_path / "no-drain.flag").as_posix()}' `
            -EnableMarker '{_enable_marker(tmp_path).as_posix()}' `
            -LogDir '{(tmp_path / "logs").as_posix()}' *> $null
        $d1 = Test-WslTeardownAllowed -ActiveWorkers 0 -Reason 'test_clean'
        $d2 = Test-WslTeardownAllowed -ActiveWorkers 4 -Reason 'test_busy' -EmergencyOverride $false
        $d3 = Test-WslTeardownAllowed -ActiveWorkers 4 -Reason 'test_override' -EmergencyOverride $true

        Write-Output ('T1=' + $d1.allowed + ';REASON=' + $d1.decision_reason)
        Write-Output ('T2=' + $d2.allowed + ';REASON=' + $d2.decision_reason)
        Write-Output ('T3=' + $d3.allowed + ';REASON=' + $d3.decision_reason)
        """
    )
    result = _run_ps(driver)
    out = result.stdout
    assert "T1=True;REASON=no_active_workers" in out, result.stdout + result.stderr
    assert "T2=False;REASON=active_runner_workers_running (4 active)" in out, result.stdout
    assert "T3=True;REASON=emergency_override_used (4 active workers bypassed)" in out, result.stdout


@PWSH_REQUIRED
def test_watchdog_unresponsive_defers_when_workers_active(tmp_path: Path) -> None:
    """Issue #1067: Watchdog mode must defer WSL recovery when active Runner.Worker processes exist."""
    log_dir = tmp_path / "logs"
    # Create driver that dot-sources script and overrides Get-ActiveRunnerWorkers to return 3 active workers
    driver = textwrap.dedent(
        f"""
        $env:LOCALAPPDATA = '{(tmp_path / "appdata").as_posix()}'
        . '{SCRIPT.as_posix()}' -Once -Distro 'noop' -Mode Resident `
            -DrainMarker '{(tmp_path / "no-drain.flag").as_posix()}' `
            -EnableMarker '{_enable_marker(tmp_path).as_posix()}' `
            -LogDir '{log_dir.as_posix()}' *> $null
        function Get-ActiveRunnerWorkers {{ param($Distro, $WslExe, $TimeoutSeconds) return 3 }}
        $res = Invoke-OneCycle `
            -Distro 'test-distro' `
            -ProbeTimeoutSeconds 2 `
            -MaxConsecutive 5 `
            -HealthyGap 600 `
            -StatePath '{(log_dir / "wsl-keepalive-state.json").as_posix()}' `
            -LogPath '{(log_dir / "wsl-keepalive.log").as_posix()}' `
            -MaxLogBytes 5MB `
            -LogBackups 3 `
            -DashboardPort 8321 `
            -DashboardServiceName 'runner-dashboard.service' `
            -Mode 'Watchdog'
        Write-Output ($res | ConvertTo-Json -Compress)
        """
    )
    result = _run_ps(driver)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "teardown_deferred"
    assert payload["active_workers"] == 3

    # Check state file
    state_file = log_dir / "wsl-keepalive-state.json"
    assert state_file.is_file()
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["status"] == "unresponsive_teardown_deferred"
    assert state["active_workers"] == 3
    assert "active_runner_workers_running" in state["last_recovery_reason"]

    # Check log lines
    log_path = log_dir / "wsl-keepalive.log"
    assert log_path.is_file()
    events = [json.loads(line)["event"] for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert "wsl_teardown_deferred_busy_runners" in events
    assert "recovery_succeeded" not in events
    assert "wsl_teardown_initiated" not in events

    # Check audit log
    audit_path = log_dir / "wsl-teardown-audit.jsonl"
    assert audit_path.is_file()
    audit_events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(audit_events) == 1
    assert audit_events[0]["action"] == "teardown_deferred"
    assert audit_events[0]["active_workers"] == 3
    assert audit_events[0]["emergency_override"] is False
