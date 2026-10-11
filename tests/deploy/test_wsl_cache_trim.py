"""Contract tests for the fleet memory policy assets (RM #2071)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from bash_host import find_bash

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPO_ROOT / "deploy"
SCRIPT = DEPLOY / "wsl-cache-trim"
BASH = find_bash()


def test_units_exist_with_15_minute_cadence() -> None:
    service = (DEPLOY / "wsl-cache-trim.service").read_text(encoding="utf-8")
    timer = (DEPLOY / "wsl-cache-trim.timer").read_text(encoding="utf-8")
    assert "Type=oneshot" in service
    assert "ExecStart=/usr/local/sbin/wsl-cache-trim" in service
    assert "OnUnitActiveSec=15min" in timer
    assert "WantedBy=timers.target" in timer


def test_installer_installs_and_enables_timer() -> None:
    text = (DEPLOY / "install-runner-maintenance.sh").read_text(encoding="utf-8")
    assert '"${SCRIPT_DIR}/wsl-cache-trim" /usr/local/sbin/wsl-cache-trim' in text
    assert "wsl-cache-trim.service" in text
    enable = [ln for ln in text.splitlines() if "systemctl enable --now" in ln]
    assert enable and all("wsl-cache-trim.timer" in ln for ln in enable)


def test_script_default_threshold_and_lf_endings() -> None:
    raw = SCRIPT.read_bytes()
    assert b"\r" not in raw
    assert b"THRESH=${THRESH:-30}" in raw


def test_windows_task_installer_guards_on_service_presence() -> None:
    text = (DEPLOY / "windows" / "install-vendor-leak-restart-task.ps1").read_text(encoding="utf-8")
    assert "DellClientManagementService" in text
    assert "Get-Service -Name $ServiceName" in text
    assert "Fleet-Dell-ServiceShell-Restart" in text
    assert text.index("Get-Service -Name $ServiceName") < text.index("Register-ScheduledTask")
    assert "-DaysInterval" in text and "'4:30am'" in text


def _meminfo(tmp_path: Path, cached_kb: int) -> Path:
    path = tmp_path / "meminfo"
    path.write_text(f"MemTotal:       1000000 kB\nCached:         {cached_kb} kB\n")
    return path


def _run(tmp_path: Path, cached_kb: int, **env: str) -> str:
    drop = tmp_path / "drop_caches"
    proc = subprocess.run(
        [BASH, SCRIPT.as_posix()],
        env={
            "PATH": __import__("os").environ["PATH"],
            "MEMINFO": _meminfo(tmp_path, cached_kb).as_posix(),
            "DROP_CACHES": drop.as_posix(),
            "COMPACT_MEMORY": (tmp_path / "compact").as_posix(),
            **env,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.returncode == 0
    return drop.read_text().strip() if drop.exists() else ""


@pytest.mark.skipif(BASH is None, reason="POSIX bash required")
def test_drops_cache_only_at_or_above_threshold(tmp_path: Path) -> None:
    assert _run(tmp_path, 299_999) == ""
    assert _run(tmp_path, 300_000) == "1"


@pytest.mark.skipif(BASH is None, reason="POSIX bash required")
def test_threshold_env_override(tmp_path: Path) -> None:
    assert _run(tmp_path, 400_000, THRESH="50") == ""
    assert _run(tmp_path, 500_000, THRESH="50") == "1"
