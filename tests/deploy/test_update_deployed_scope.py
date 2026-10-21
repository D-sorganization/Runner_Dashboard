"""Executable regression coverage for system and user deployment scopes (#2049)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UPDATE = ROOT / "deploy" / "update-deployed.sh"


def _run_update(
    tmp_path: Path,
    *args: str,
    health: bool = True,
    user_unit: bool = True,
    user_manager: bool = True,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "commands.log"
    for name, body in {
        "systemctl": (
            'printf "systemctl %s\\n" "$*" >> "$COMMAND_LOG"\n'
            'if [[ "$*" == *"LoadState"* && "${FAKE_USER_MANAGER_AVAILABLE}" != "true" ]]; then echo "failed to connect to bus" >&2; exit 1; fi\n'
            'if [[ "$*" == *"LoadState"* ]]; then printf "%s\\n" "${FAKE_USER_UNIT_STATE}"; fi\n'
            'exit 0\n'
        ),
        "sudo": 'printf "sudo %s\\n" "$*" >> "$COMMAND_LOG"\nexec "$@"\n',
        "curl": f'printf "curl %s\\n" "$*" >> "$COMMAND_LOG"\n' + ("exit 0\n" if health else "exit 1\n"),
        "sleep": 'exit 0\n',
        "rsync": (
            'printf "rsync %s\\n" "$*" >> "$COMMAND_LOG"\n'
            'source_path="${@: -2:1}"; target_path="${@: -1}"\n'
            'mkdir -p "$target_path"; cp -a "${source_path%/}/." "$target_path/"\n'
        ),
        "uv": 'printf "uv %s\\n" "$*" >> "$COMMAND_LOG"\nexit 0\n',
        "npm": 'printf "npm %s\\n" "$*" >> "$COMMAND_LOG"\nexit 0\n',
    }.items():
        command = bin_dir / name
        command.write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8")
        command.chmod(0o755)

    deploy = tmp_path / "deployed"
    deploy.mkdir()
    (deploy / "old.txt").write_text("old", encoding="utf-8")
    repo = tmp_path / "repo"
    for directory in ("backend", "frontend", "deploy"):
        (repo / directory).mkdir(parents=True, exist_ok=True)
    (repo / "uv.lock").write_text("", encoding="utf-8")
    (repo / "backend" / "server.py").write_text("", encoding="utf-8")
    (repo / "backend" / "runner_autoscaler.py").write_text("", encoding="utf-8")
    for filename in ("refresh-token.sh", "wsl-mirrored-port-helper.sh"):
        (repo / "deploy" / filename).write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / "local_apps.json").write_text("[]", encoding="utf-8")
    (repo / "VERSION").write_text("0.0.0", encoding="utf-8")
    (repo / "frontend" / "index.html").write_text("", encoding="utf-8")
    (repo / "dist").mkdir()

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(UPDATE, scripts / "update-deployed.sh")
    shutil.copy2(ROOT / "deploy" / "lib.sh", scripts / "lib.sh")
    shutil.copy2(ROOT / "deploy" / "rollback-deployed.sh", scripts / "rollback-deployed.sh")
    (scripts / "systemd-dropins").mkdir()
    (scripts / "systemd-dropins" / "40-cursor-sandbox.conf").write_text("", encoding="utf-8")
    for helper in ("migrate-runner-units.sh", "refresh-maintenance-assets.sh", "write-deployment-metadata.sh"):
        helper_path = scripts / helper
        helper_path.write_text(
            '#!/usr/bin/env bash\nprintf "helper %s\\n" "' + helper + '" >> "$COMMAND_LOG"\n',
            encoding="utf-8",
        )
        helper_path.chmod(0o755)
    (scripts / "install-dashboard-artifact.sh").write_text(
        '#!/usr/bin/env bash\nprintf "install %s\\n" "$*" >> "$COMMAND_LOG"\n'
        'while [[ $# -gt 0 ]]; do case "$1" in --deploy-dir) deploy_dir="$2"; shift 2 ;; *) shift ;; esac; done\n'
        'printf new > "$deploy_dir/version.txt"\n',
        encoding="utf-8",
    )
    (scripts / "install-dashboard-artifact.sh").chmod(0o755)

    env = os.environ.copy()
    env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    env.update(
        PATH=f"{bin_dir}:{env['PATH']}",
        COMMAND_LOG=str(log),
        DEPLOY_DIR=str(deploy),
        DASHBOARD_HOME=str(tmp_path),
        DEPLOY_HEALTH_DELAYS="0",
        FAKE_USER_UNIT_STATE="loaded" if user_unit else "not-found",
        FAKE_USER_MANAGER_AVAILABLE="true" if user_manager else "false",
    )
    runtime_dir = tmp_path / "user-runtime"
    runtime_dir.mkdir()
    env["XDG_RUNTIME_DIR"] = str(runtime_dir)
    result = subprocess.run(
        ["bash", str(scripts / "update-deployed.sh"), "--repo", str(repo), "--deploy-dir", str(deploy), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    commands = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, commands


def test_invalid_scope_fails_before_backup_or_commands(tmp_path: Path) -> None:
    result, commands = _run_update(tmp_path, "--scope", "host")

    assert result.returncode != 0
    assert "unsupported deployment scope" in result.stderr.lower()
    assert commands == []
    assert not list(tmp_path.glob("deployed.bak.*"))


def test_user_scope_uses_user_manager_and_skips_host_maintenance(tmp_path: Path) -> None:
    result, commands = _run_update(tmp_path, "--scope", "user")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "systemctl --user restart runner-dashboard" in commands
    assert "systemctl --user is-active --quiet runner-dashboard" in commands
    assert not any(line.startswith("sudo ") for line in commands)
    assert not any(token in " ".join(commands) for token in ("migrate-runner-units", "refresh-maintenance-assets"))
    assert any(line.startswith("systemctl --user show runner-dashboard.service") for line in commands)
    assert not any(line.startswith("sudo systemctl show") for line in commands)


def test_default_scope_preserves_system_service_and_maintenance(tmp_path: Path) -> None:
    result, commands = _run_update(tmp_path)

    assert result.returncode == 0, result.stderr + result.stdout
    assert "sudo systemctl restart runner-dashboard" in commands
    assert any(line == "helper refresh-maintenance-assets.sh" for line in commands)
    assert any(line.startswith("systemctl show runner-dashboard.service") for line in commands)
    assert not any(line.startswith("sudo systemctl show") for line in commands)


def test_user_scope_rolls_back_with_user_service_manager(tmp_path: Path) -> None:
    result, commands = _run_update(tmp_path, "--scope", "user", health=False)

    assert result.returncode != 0
    assert any(line.startswith("systemctl --user restart runner-dashboard") for line in commands)
    assert any(line.startswith("systemctl --user is-active --quiet runner-dashboard") for line in commands)
    assert not any(line.startswith("sudo ") for line in commands)
    assert sum(line == "systemctl --user restart runner-dashboard" for line in commands) >= 2
    assert any(line.startswith("rsync -a --delete ") and ".bak." in line for line in commands)
    assert (tmp_path / "deployed" / "old.txt").exists()


def test_user_scope_preserves_backup_manifest_before_update(tmp_path: Path) -> None:
    result, _commands = _run_update(tmp_path, "--scope", "user")

    assert result.returncode == 0, result.stderr + result.stdout
    backups = [path for path in tmp_path.glob("deployed.bak.*") if path.is_dir()]
    assert len(backups) == 1
    assert (backups[0] / "old.txt").read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob("deployed.bak.*.manifest.sha256"))


def test_user_artifact_scope_forwards_checksum_and_rolls_back_in_user_scope(tmp_path: Path) -> None:
    artifact = tmp_path / "dashboard.tar.gz"
    artifact.write_bytes(b"fixture artifact")
    checksum = "a" * 64

    result, commands = _run_update(
        tmp_path,
        "--scope",
        "user",
        "--artifact",
        str(artifact),
        "--checksum",
        checksum,
        health=False,
    )

    assert result.returncode != 0
    assert any(f"--checksum {checksum}" in line for line in commands)
    assert any(line.startswith("systemctl --user restart runner-dashboard") for line in commands)
    assert not any(line.startswith("sudo ") for line in commands)
    assert not any("migrate-runner-units" in line or "refresh-maintenance-assets" in line for line in commands)
    assert not any(line.startswith("uv ") or line.startswith("npm ") for line in commands)
    install_index = next(index for index, line in enumerate(commands) if line.startswith("install "))
    rollback_index = next(
        index for index, line in enumerate(commands) if line.startswith("rsync -a --delete ") and ".bak." in line
    )
    assert install_index < rollback_index
    assert (tmp_path / "deployed" / "version.txt").read_text(encoding="utf-8") == "new"
    assert (tmp_path / "deployed" / "old.txt").exists()


def test_user_scope_accepts_xdg_only_user_manager_session(tmp_path: Path) -> None:
    result, commands = _run_update(tmp_path, "--scope", "user")

    assert result.returncode == 0, result.stderr + result.stdout
    assert any(line.startswith("systemctl --user show runner-dashboard.service") for line in commands)


def test_user_scope_requires_installed_unit_before_backup(tmp_path: Path) -> None:
    result, commands = _run_update(tmp_path, "--scope", "user", user_unit=False)

    assert result.returncode != 0
    assert "not installed" in result.stderr
    assert commands == ["systemctl --user show runner-dashboard.service -p LoadState --value"]
    assert not [path for path in tmp_path.glob("deployed.bak.*") if path.is_dir()]


def test_user_scope_manager_connection_failure_precedes_backup(tmp_path: Path) -> None:
    result, commands = _run_update(tmp_path, "--scope", "user", user_manager=False)

    assert result.returncode != 0
    assert "cannot reach the user systemd manager" in result.stderr.lower()
    assert "XDG_RUNTIME_DIR=" in result.stderr
    assert commands == ["systemctl --user show runner-dashboard.service -p LoadState --value"]
    assert not [path for path in tmp_path.glob("deployed.bak.*") if path.is_dir()]
