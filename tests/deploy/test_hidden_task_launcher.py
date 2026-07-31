"""Cross-platform tests for the zero-window scheduled-task launcher.

``deploy/run-hidden.vbs`` + ``deploy/install-hidden-task-launcher.ps1`` fix
the fleet-host console-popup nuisance: any scheduled task registered with an
InteractiveToken principal and a console executable (powershell.exe,
bash.exe, cmd.exe) opens a visible console window in the user's session each
time it fires and steals foreground focus. ``-WindowStyle Hidden`` is not
sufficient — the console host window is created before PowerShell parses its
arguments, so a focus-stealing flash remains. Launching through
``wscript.exe`` (a GUI-subsystem host) with ``WshShell.Run(cmd, 0, True)``
never creates a console window at all.

Test layout mirrors ``test_wsl_keepalive_script.py``:

1. **Static checks** (run everywhere): both artifacts exist and contain the
   load-bearing contract markers.
2. **Behavioural checks** (require ``pwsh``): drive the installer's pure
   helpers (``ConvertTo-WrappedAction`` / ``ConvertFrom-WrappedAction`` /
   ``Test-WrappedAction``) via ``-FunctionsOnly`` dot-sourcing.
3. **Windows-only behavioural checks**: execute the VBS via ``cscript`` and
   assert exit-code propagation and precondition rejections. (Window
   invisibility itself is verified operationally on the live task — it is
   not automatable from a headless test.)

The scheduled-task read/write path (Get-/Set-ScheduledTask) is deliberately
NOT exercised here: CI runners are Linux and have no Task Scheduler. The
installer isolates it behind its pure helpers plus a postcondition re-read.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VBS = REPO_ROOT / "deploy" / "run-hidden.vbs"
INSTALLER = REPO_ROOT / "deploy" / "install-hidden-task-launcher.ps1"

IS_WINDOWS = sys.platform == "win32"
WINDOWS_REQUIRED = pytest.mark.skipif(not IS_WINDOWS, reason="cscript.exe only exists on Windows")


def _find_pwsh() -> str | None:
    for candidate in ("pwsh", "powershell"):
        if shutil.which(candidate):
            return candidate
    return None


PWSH = _find_pwsh()
PWSH_REQUIRED = pytest.mark.skipif(PWSH is None, reason="PowerShell (pwsh/powershell) not available on this runner")


def _run_ps(script_body: str) -> subprocess.CompletedProcess[str]:
    assert PWSH is not None
    return subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", script_body],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_vbs(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the launcher under cscript (same engine as wscript, console host)."""
    return subprocess.run(
        ["cscript.exe", "//B", "//Nologo", str(VBS), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _dot_source_prefix() -> str:
    """Dot-source the installer exposing only its pure helpers."""
    return f". '{INSTALLER.as_posix()}' -FunctionsOnly; "


# ---------------------------------------------------------------------------
# Static checks — run-hidden.vbs
# ---------------------------------------------------------------------------


def test_vbs_exists() -> None:
    assert VBS.is_file(), f"launcher missing: {VBS}"


def test_vbs_runs_hidden_and_waits() -> None:
    """The whole point: SW_HIDE (0) and synchronous wait (True).

    Waiting matters for two reasons: the task's MultipleInstancesPolicy
    (IgnoreNew) only debounces overlapping cycles if the wscript process
    lives as long as the child, and the child's exit code can only be
    propagated after it exits.
    """
    text = VBS.read_text(encoding="utf-8")
    assert ".Run(command, 0, True)" in text, "must run with window style 0 (hidden) and wait"


def test_vbs_propagates_child_exit_code() -> None:
    text = VBS.read_text(encoding="utf-8")
    assert "WScript.Quit(exitCode)" in text, "child exit code must become the launcher exit code"


def test_vbs_rejects_missing_command() -> None:
    """Precondition: at least one argument (the command)."""
    text = VBS.read_text(encoding="utf-8")
    assert "WScript.Quit(87)" in text, "argument-contract violations must exit 87 (ERROR_INVALID_PARAMETER)"


def test_vbs_rejects_embedded_double_quotes() -> None:
    """Precondition: embedded double quotes are not re-quotable losslessly.

    WScript strips quoting during argument parsing; an argument that still
    contains a literal double quote cannot be rebuilt unambiguously, so the
    launcher must refuse rather than silently corrupt the command line.
    """
    text = VBS.read_text(encoding="utf-8")
    assert "InStr(arg, Chr(34))" in text, "must detect embedded double quotes and refuse"


# ---------------------------------------------------------------------------
# Static checks — install-hidden-task-launcher.ps1
# ---------------------------------------------------------------------------


def test_installer_exists() -> None:
    assert INSTALLER.is_file(), f"installer missing: {INSTALLER}"


def test_installer_declares_contract_parameters() -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    for param in ("TaskName", "VbsPath", "Revert", "DryRun", "FunctionsOnly"):
        assert f"${param}" in text, f"parameter ${param} not declared"


def test_installer_uses_strict_mode() -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    assert "Set-StrictMode -Version Latest" in text
    assert "$ErrorActionPreference = 'Stop'" in text


def test_installer_launches_via_wscript_batch_mode() -> None:
    """//B suppresses script-host error dialogs; //Nologo keeps stdout clean."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert "wscript.exe" in text
    assert "//B" in text
    assert "//Nologo" in text


def test_installer_verifies_postcondition_after_write() -> None:
    """DbC: after Set-ScheduledTask the installer must re-read the task and
    verify the action actually matches what it intended to write."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert "postcondition" in text.lower()


def test_installer_guards_schtasks_fallback_length() -> None:
    """The non-elevated fallback path (schtasks /Change /TR) silently
    truncates beyond 261 characters; the installer must refuse instead."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert "261" in text, "schtasks /TR 261-char limit must be guarded"


def test_installer_threads_task_path_through_write_and_reread() -> None:
    """Regression guard (found live on ControlTower's ``matlab-runner``):
    ``Get-ScheduledTask -TaskName`` finds a task in any folder, but
    ``Set-ScheduledTask`` without ``-TaskPath`` looks only at the root and
    fails with 0x80070002. The discovered TaskPath must be passed to the
    write, the postcondition re-read, and the schtasks fallback (as the
    ``\\folder\\name`` form), and a name matching multiple tasks must be
    rejected up front."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert text.count("-TaskPath $taskPath") >= 2, "write and re-read must both pin the discovered TaskPath"
    assert "$taskPath + $TaskName" in text, "schtasks fallback must address the task by full path"
    assert "matches multiple tasks" in text, "ambiguous task names must be rejected"


# ---------------------------------------------------------------------------
# Behavioural checks — pure helpers (require pwsh)
# ---------------------------------------------------------------------------


@PWSH_REQUIRED
def test_helpers_wrap_simple_powershell_action(tmp_path: Path) -> None:
    vbs = tmp_path / "run-hidden.vbs"
    vbs.write_text("' stub", encoding="utf-8")
    driver = _dot_source_prefix() + textwrap.dedent(
        f"""
        $w = ConvertTo-WrappedAction -Execute 'powershell.exe' `
            -Arguments '-NoProfile -File "C:\\ops\\monitor.ps1"' `
            -VbsPath '{vbs}'
        Write-Output ("EXEC=" + $w.Execute)
        Write-Output ("ARGS=" + $w.Arguments)
        """
    )
    result = _run_ps(driver)
    assert result.returncode == 0, result.stderr
    assert "EXEC=" in result.stdout
    exec_line = next(line for line in result.stdout.splitlines() if line.startswith("EXEC="))
    args_line = next(line for line in result.stdout.splitlines() if line.startswith("ARGS="))
    assert exec_line.removeprefix("EXEC=").lower().endswith("wscript.exe")
    assert args_line.removeprefix("ARGS=").startswith('//B //Nologo "')
    assert 'powershell.exe -NoProfile -File "C:\\ops\\monitor.ps1"' in args_line


@PWSH_REQUIRED
def test_helpers_quote_executable_containing_spaces(tmp_path: Path) -> None:
    vbs = tmp_path / "run-hidden.vbs"
    vbs.write_text("' stub", encoding="utf-8")
    driver = _dot_source_prefix() + textwrap.dedent(
        f"""
        $w = ConvertTo-WrappedAction -Execute 'C:\\Program Files\\Git\\bin\\bash.exe' `
            -Arguments '-lc "/c/ops/backup.sh"' `
            -VbsPath '{vbs}'
        Write-Output ("ARGS=" + $w.Arguments)
        """
    )
    result = _run_ps(driver)
    assert result.returncode == 0, result.stderr
    assert '"C:\\Program Files\\Git\\bin\\bash.exe"' in result.stdout


@PWSH_REQUIRED
def test_helpers_round_trip_wrap_then_revert(tmp_path: Path) -> None:
    """ConvertFrom(ConvertTo(x)) must reproduce x verbatim — the revert path
    depends on it."""
    vbs = tmp_path / "run-hidden.vbs"
    vbs.write_text("' stub", encoding="utf-8")
    cases = [
        ("powershell.exe", '-NoProfile -ExecutionPolicy Bypass -File "C:\\ops\\fleet-health-monitor.ps1"'),
        ("C:\\Program Files\\Git\\bin\\bash.exe", '-lc "/c/ops/run-forgejo-backup.sh"'),
        ("cmd.exe", "/c echo done"),
    ]
    for execute, arguments in cases:
        driver = _dot_source_prefix() + textwrap.dedent(
            f"""
            $w = ConvertTo-WrappedAction -Execute '{execute}' -Arguments '{arguments}' -VbsPath '{vbs}'
            $r = ConvertFrom-WrappedAction -Execute $w.Execute -Arguments $w.Arguments -VbsPath '{vbs}'
            Write-Output ("EXEC=" + $r.Execute)
            Write-Output ("ARGS=" + $r.Arguments)
            """
        )
        result = _run_ps(driver)
        assert result.returncode == 0, f"{execute}: {result.stderr}"
        assert f"EXEC={execute}" in result.stdout, result.stdout
        assert f"ARGS={arguments}" in result.stdout, result.stdout


@PWSH_REQUIRED
def test_helpers_detect_wrapped_action(tmp_path: Path) -> None:
    vbs = tmp_path / "run-hidden.vbs"
    vbs.write_text("' stub", encoding="utf-8")
    driver = _dot_source_prefix() + textwrap.dedent(
        f"""
        $w = ConvertTo-WrappedAction -Execute 'powershell.exe' -Arguments '-File x.ps1' -VbsPath '{vbs}'
        $before = Test-WrappedAction -Execute 'powershell.exe' -Arguments '-File x.ps1' -VbsPath '{vbs}'
        Write-Output ("BEFORE=" + $before)
        Write-Output ("AFTER=" + (Test-WrappedAction -Execute $w.Execute -Arguments $w.Arguments -VbsPath '{vbs}'))
        """
    )
    result = _run_ps(driver)
    assert result.returncode == 0, result.stderr
    assert "BEFORE=False" in result.stdout
    assert "AFTER=True" in result.stdout


@PWSH_REQUIRED
def test_helpers_refuse_double_wrap(tmp_path: Path) -> None:
    """Precondition: wrapping an already-wrapped action must throw, not nest."""
    vbs = tmp_path / "run-hidden.vbs"
    vbs.write_text("' stub", encoding="utf-8")
    driver = _dot_source_prefix() + textwrap.dedent(
        f"""
        $w = ConvertTo-WrappedAction -Execute 'powershell.exe' -Arguments '-File x.ps1' -VbsPath '{vbs}'
        try {{
            ConvertTo-WrappedAction -Execute $w.Execute -Arguments $w.Arguments -VbsPath '{vbs}' | Out-Null
            Write-Output 'NOTHROW'
        }} catch {{
            Write-Output 'THREW'
        }}
        """
    )
    result = _run_ps(driver)
    assert result.returncode == 0, result.stderr
    assert "THREW" in result.stdout


@PWSH_REQUIRED
def test_helpers_refuse_revert_of_unwrapped_action(tmp_path: Path) -> None:
    vbs = tmp_path / "run-hidden.vbs"
    vbs.write_text("' stub", encoding="utf-8")
    driver = _dot_source_prefix() + textwrap.dedent(
        f"""
        try {{
            ConvertFrom-WrappedAction -Execute 'powershell.exe' -Arguments '-File x.ps1' -VbsPath '{vbs}' | Out-Null
            Write-Output 'NOTHROW'
        }} catch {{
            Write-Output 'THREW'
        }}
        """
    )
    result = _run_ps(driver)
    assert result.returncode == 0, result.stderr
    assert "THREW" in result.stdout


# ---------------------------------------------------------------------------
# Behavioural checks — the VBS itself (Windows only)
# ---------------------------------------------------------------------------


@WINDOWS_REQUIRED
def test_vbs_propagates_exit_code_live() -> None:
    result = _run_vbs("cmd.exe", "/c", "exit 3")
    assert result.returncode == 3, f"expected 3, got {result.returncode}: {result.stderr}"


@WINDOWS_REQUIRED
def test_vbs_zero_exit_code_live() -> None:
    result = _run_vbs("cmd.exe", "/c", "exit 0")
    assert result.returncode == 0


@WINDOWS_REQUIRED
def test_vbs_no_arguments_rejected_live() -> None:
    result = _run_vbs()
    assert result.returncode == 87


@WINDOWS_REQUIRED
def test_vbs_requotes_spaced_argument_live(tmp_path: Path) -> None:
    """An argument containing spaces must survive WScript's quote-stripping
    via the launcher's re-quoting: a batch file in a spaced directory only
    runs (and its exit code only propagates) if the rebuilt command line
    quoted the path again.

    Note there is deliberately no live embedded-double-quote test: WScript's
    parser strips all quoting before the script sees its arguments, so a
    literal ``"`` cannot be injected at runtime. The ``InStr(arg, Chr(34))``
    guard is defense-in-depth and is asserted statically above.
    """
    spaced_dir = tmp_path / "a b"
    spaced_dir.mkdir()
    script = spaced_dir / "probe.cmd"
    script.write_text("@exit 11\r\n", encoding="ascii")
    result = _run_vbs(str(script))
    assert result.returncode == 11, f"expected 11, got {result.returncode}: {result.stderr}"
