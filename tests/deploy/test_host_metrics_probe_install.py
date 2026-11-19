"""Contract tests for host-metrics-probe systemd wiring (#4309)."""

from __future__ import annotations

from pathlib import Path

from bash_host import as_bash_path, require_bash

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy"


def test_timer_samples_every_thirty_seconds() -> None:
    timer = (DEPLOY / "systemd" / "runner-dashboard-host-metrics-probe.timer").read_text(encoding="utf-8")
    assert "OnUnitActiveSec=30s" in timer
    assert "runner-dashboard-host-metrics-probe.service" in timer


def test_service_invokes_deployed_probe_script() -> None:
    unit = (DEPLOY / "systemd" / "runner-dashboard-host-metrics-probe.service").read_text(encoding="utf-8")
    assert "host-metrics-probe.py" in unit
    assert "RUNNER_DASHBOARD_STATE_DIR=@STATE_DIR@" in unit


def test_install_script_enables_timer_in_update_deployed_and_installer() -> None:
    update = (DEPLOY / "update-deployed.sh").read_text(encoding="utf-8")
    assert "install-host-metrics-probe.sh" in update
    assert "install_host_metrics_probe_timer" in update
    install = (DEPLOY / "install-host-metrics-probe.sh").read_text(encoding="utf-8")
    assert "runner-dashboard-host-metrics-probe.timer" in install
    assert "systemctl --user enable --now runner-dashboard-host-metrics-probe.timer" in install
    assert "sudo systemctl enable --now runner-dashboard-host-metrics-probe.timer" in install
    assert "sudo" in install.lower()


def test_deploy_check_includes_host_metrics_probe() -> None:
    deploy_check = (DEPLOY / "deploy-check.sh").read_text(encoding="utf-8")
    assert "host_metrics" in deploy_check


def test_service_unit_invokes_probe_via_python_interpreter() -> None:
    unit = (DEPLOY / "systemd" / "runner-dashboard-host-metrics-probe.service").read_text(encoding="utf-8")
    assert "ExecStart=@PYTHON_BIN@" in unit
    assert "host-metrics-probe.py" in unit


def test_windows_wsl_install_helper_exists() -> None:
    helper = DEPLOY / "windows" / "install-host-metrics-probe-wsl.ps1"
    assert helper.is_file()
    text = helper.read_text(encoding="utf-8")
    assert "install-host-metrics-probe.sh" in text
    assert "@'" in text
    assert 'deploy_dir="${1:-}"' in text
    assert '\\"${DeployDir}\\"' not in text


def test_windows_wsl_install_uses_utf8_file_not_native_stdin() -> None:
    """Windows PowerShell 5.1 breaks multi-line bash -c strings that contain quotes (#4465)."""
    helper = DEPLOY / "windows" / "install-host-metrics-probe-wsl.ps1"
    text = helper.read_text(encoding="utf-8")
    assert "[System.IO.File]::WriteAllText" in text
    assert '$bashScript.Replace("`r", "")' in text
    assert "New-Object System.Text.UTF8Encoding($false)" in text
    assert "-e bash $wslScriptPath $DeployDir" in text
    assert "$bashScript | wsl.exe" not in text
    assert "finally {" in text
    assert "Remove-Item -LiteralPath $scriptPath" in text
    assert "bash -c $bashScript" not in text


def test_install_skips_probe_copy_when_source_is_deploy_tree() -> None:
    install = (DEPLOY / "install-host-metrics-probe.sh").read_text(encoding="utf-8")
    assert 'realpath "${PROBE_SRC}"' in install
    assert 'realpath "${PROBE_DEST}"' in install


def test_windows_wsl_install_checks_native_exit_codes() -> None:
    text = (DEPLOY / "windows" / "install-host-metrics-probe-wsl.ps1").read_text(encoding="utf-8")
    assert text.count("if ($LASTEXITCODE -ne 0)") == 2
    assert 'throw "Host-metrics probe install failed' in text


def test_windows_wsl_bash_payload_handles_lf_file_and_installer_failure() -> None:
    import subprocess
    import tempfile

    bash = require_bash()
    text = (DEPLOY / "windows" / "install-host-metrics-probe-wsl.ps1").read_text(encoding="utf-8")
    payload = text.split("$bashScript = @'\n", 1)[1].split("\n'@", 1)[0]
    with tempfile.TemporaryDirectory(prefix="probe path ") as directory:
        root = Path(directory)
        installer = root / "deploy" / "install-host-metrics-probe.sh"
        installer.parent.mkdir()
        # The real installer receives the root through the environment.
        installer.write_text('test -d "$RUNNER_DASHBOARD_DEPLOY_DIR/deploy"\n', encoding="utf-8")
        script = root / "wrapper.sh"
        script.write_bytes(payload.replace("\r", "").encode("utf-8"))
        assert subprocess.run([bash, as_bash_path(script), as_bash_path(root)], check=False).returncode == 0
        installer.write_text("exit 23\n", encoding="utf-8")
        assert subprocess.run([bash, as_bash_path(script), as_bash_path(root)], check=False).returncode == 23
        # Reproduce the old Windows PowerShell native-stdin line endings.
        broken = subprocess.run(
            [bash, "-s", "--", as_bash_path(root)],
            input=payload.replace("\n", "\r\n"),
            text=True,
            capture_output=True,
            check=False,
        )
        assert broken.returncode != 0
