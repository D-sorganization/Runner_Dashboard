"""Contracts for ``deploy/fleet-health-monitor.ps1``.

The 2026-07-30 fleet outage went unnoticed for weeks because the deployed
DeskComputer health monitor only watched the ControlTower-SSD pool: the
DeskComputer runners sat offline until GitHub's 14-day auto-purge deleted
their registrations, and nothing alarmed. This canonical monitor closes the
blind spot with per-pool online floors, a DeskComputer unit self-heal, and
an explicit registration-purge alarm.

Static checks run everywhere; behavioural checks drive the pure helpers via
``-FunctionsOnly`` dot-sourcing (the live cycle needs SSH/WSL/dashboard and
is exercised operationally, not here).
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "deploy" / "fleet-health-monitor.ps1"


def _find_pwsh() -> str | None:
    for candidate in ("pwsh", "powershell"):
        if shutil.which(candidate):
            return candidate
    return None


PWSH = _find_pwsh()
PWSH_REQUIRED = pytest.mark.skipif(PWSH is None, reason="PowerShell not available on this runner")


def _run_ps(script_body: str) -> subprocess.CompletedProcess[str]:
    assert PWSH is not None
    return subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", script_body],
        capture_output=True,
        text=True,
        check=False,
    )


def _prefix() -> str:
    return f". '{SCRIPT.as_posix()}' -FunctionsOnly; "


# ---------------------------------------------------------------------------
# Static checks
# ---------------------------------------------------------------------------


def test_script_exists() -> None:
    assert SCRIPT.is_file(), f"missing: {SCRIPT}"


def test_script_declares_contract_parameters() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    for param in (
        "DashboardUrl",
        "CtSsh",
        "WmiHandleKillThreshold",
        "PoolFloors",
        "DeskWslDistro",
        "DrainMarker",
        "EnableMarker",
        "FunctionsOnly",
    ):
        assert f"${param}" in text, f"parameter ${param} not declared"


def test_script_exits_before_side_effects_when_drain_marker_exists(tmp_path: Path) -> None:
    """An operator drain must win over every keepalive and recovery action."""
    marker = tmp_path / "deskcomputer-runner-drained.flag"
    marker.write_text("drained\n", encoding="utf-8")
    assert PWSH is not None
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(SCRIPT),
            "-DrainMarker",
            str(marker),
            "-DashboardUrl",
            "invalid://must-not-be-contacted",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_script_exits_before_side_effects_when_enable_marker_is_absent(tmp_path: Path) -> None:
    """Marker cleanup alone must not re-enable automatic fleet recovery."""
    assert PWSH is not None
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(SCRIPT),
            "-DrainMarker",
            str(tmp_path / "no-drain.flag"),
            "-EnableMarker",
            str(tmp_path / "no-enable.flag"),
            "-DashboardUrl",
            "invalid://must-not-be-contacted",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_functions_only_remains_available_while_drained(tmp_path: Path) -> None:
    marker = tmp_path / "deskcomputer-runner-drained.flag"
    marker.write_text("drained\n", encoding="utf-8")
    driver = (
        f". '{SCRIPT.as_posix()}' -FunctionsOnly -DrainMarker '{marker.as_posix()}'; "
        "Write-Output ('HELPER=' + (Test-PurgeSuspected -PoolOnline 0 -LocalUnitsActive 1))"
    )
    result = _run_ps(driver)
    assert result.returncode == 0, result.stderr
    assert "HELPER=True" in result.stdout


def test_script_watches_all_pools_not_just_ct_ssd() -> None:
    """Regression guard for the 2026-07-30 blind spot."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "d-sorg-local-Desktop-" in text
    assert "d-sorg-local-ControlTower-SSD-" in text
    assert "d-sorg-local-Oglaptop-" in text


def test_script_alarms_on_suspected_registration_purge() -> None:
    """Zero pool members online while local units run = the purge signature
    (GitHub deletes registrations after ~14 days offline). The monitor must
    say so loudly and point at the recovery runbook."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "Test-PurgeSuspected" in text
    assert "runner-registration-purge-recovery" in text, "must point at the recovery runbook"


def test_script_self_heals_desk_units_without_wsl_reset() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "systemctl" in text and "start" in text
    assert "runner-scheduler.service" in text
    assert "for ($n = 1; $n -le $DeskRunnerTotal; $n++)" not in text
    assert "wsl --shutdown" not in text, "monitor must never tear down WSL"


def test_script_explicitly_quarantines_nvme_keepalive() -> None:
    """Issue #1071 / #1078 quarantine guard: ControlTower-NVMe keepalive must be explicitly quarantined."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "ControlTower-NVMe-KeepAlive" in text
    assert "$quarantined" in text


def test_ct_ssh_enforces_a_hard_timeout() -> None:
    """Regression guard: on 2026-07-31 a cycle hung >100 min inside the
    ControlTower SSH call (the $TimeoutSec parameter existed but was never
    enforced) and MultipleInstances=IgnoreNew silently rejected every later
    firing. The SSH child must be waited on with a deadline and killed on
    expiry."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "WaitForExit(" in text, "ssh must be awaited with a deadline"
    assert ".Kill()" in text, "timed-out ssh must be killed"
    assert "ct_ssh_timeout" in text or "timed out" in text


def test_script_guards_controltower_host_disk_space() -> None:
    """A WSL2 vhdx that runs out of host disk mid-write corrupts the distro
    (null-byte files, corrupt package DB) — the probable origin of the #1071
    NVMe corruption, which on 2026-07-31 came within minutes of repeating on
    the LIVE ControlTower-SSD pool (F: hit 2.05 GB free and falling). The
    monitor must alarm on host free space, not just runner counts."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "$DiskFloorsGb" in text, "disk floors must be a declared parameter"
    assert "Test-DiskBelowFloor" in text
    assert "DISKFREE=" in text, "the ControlTower probe must report free space"
    assert "vhdx corruption risk" in text.lower()


def test_cycle_logs_a_heartbeat_line() -> None:
    """A cycle that dies before its first section must still leave a trace,
    otherwise a stall is indistinguishable from healthy silence."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "cycle start" in text


def test_floor_breaches_are_verified_against_github() -> None:
    """The dashboard feed can serve stale/false zeros (observed 2026-07-31:
    10 online reported vs 31 actual). A floor breach computed from dashboard
    data must be re-counted straight from the GitHub API before any warning
    or self-heal action; if verification is unavailable, the cycle takes no
    action (fail-safe)."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "Get-GitHubPoolCounts" in text
    assert "ConvertFrom-GitHubRunnerJson" in text
    assert "no action" in text.lower()


def test_desktop_floor_comes_from_scheduler_state() -> None:
    """The governed scheduler, not the monitor's installed-runner count, owns
    desired Desktop capacity. A fixed floor causes a two-runner daytime target
    to oscillate back to all installed services every five minutes."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "Get-DeskScheduledCapacity" in text
    assert "$effectiveFloors['Desktop'] = $deskDesired" in text


# ---------------------------------------------------------------------------
# Behavioural checks — pure helpers
# ---------------------------------------------------------------------------


@PWSH_REQUIRED
def test_pool_counts_classify_by_prefix() -> None:
    driver = _prefix() + textwrap.dedent(
        """
        $runners = @(
            @{ name = 'd-sorg-local-Desktop-1'; status = 'online' },
            @{ name = 'd-sorg-local-Desktop-2'; status = 'offline' },
            @{ name = 'd-sorg-local-ControlTower-SSD-1'; status = 'online' },
            @{ name = 'd-sorg-local-ControlTower-nvme-6'; status = 'offline' },
            @{ name = 'd-sorg-local-Oglaptop-8'; status = 'online' }
        ) | ForEach-Object { [pscustomobject]$_ }
        $c = Get-RunnerPoolCounts -Runners $runners
        Write-Output ("DESK=" + $c['Desktop'].online + "/" + $c['Desktop'].total)
        Write-Output ("SSD=" + $c['ControlTower-SSD'].online + "/" + $c['ControlTower-SSD'].total)
        Write-Output ("OG=" + $c['Oglaptop'].online + "/" + $c['Oglaptop'].total)
        """
    )
    result = _run_ps(driver)
    assert result.returncode == 0, result.stderr
    assert "DESK=1/2" in result.stdout
    assert "SSD=1/1" in result.stdout
    assert "OG=1/1" in result.stdout


@PWSH_REQUIRED
def test_pools_below_floor_detected() -> None:
    driver = _prefix() + textwrap.dedent(
        """
        $counts = @{
            'Desktop' = @{ online = 2; total = 8 }
            'ControlTower-SSD' = @{ online = 15; total = 17 }
            'Oglaptop' = @{ online = 8; total = 8 }
        }
        $floors = @{ 'Desktop' = 6; 'ControlTower-SSD' = 12; 'Oglaptop' = 4 }
        $below = Get-PoolsBelowFloor -Counts $counts -Floors $floors
        Write-Output ("BELOW=" + ($below -join ','))
        """
    )
    result = _run_ps(driver)
    assert result.returncode == 0, result.stderr
    assert "BELOW=Desktop" in result.stdout


@PWSH_REQUIRED
def test_github_runner_json_parses_to_pool_counts() -> None:
    sample = (
        '{"total_count": 3, "runners": ['
        '{"name": "d-sorg-local-Desktop-1", "status": "online", "busy": true},'
        '{"name": "d-sorg-local-Desktop-2", "status": "offline", "busy": false},'
        '{"name": "d-sorg-local-Oglaptop-8", "status": "online", "busy": false}'
        "]}"
    )
    driver = _prefix() + textwrap.dedent(
        f"""
        $runners = ConvertFrom-GitHubRunnerJson -Json '{sample}'
        $c = Get-RunnerPoolCounts -Runners $runners
        Write-Output ("DESK=" + $c['Desktop'].online + "/" + $c['Desktop'].total)
        Write-Output ("OG=" + $c['Oglaptop'].online + "/" + $c['Oglaptop'].total)
        Write-Output ("BADJSON=" + ($null -eq (ConvertFrom-GitHubRunnerJson -Json 'not json')))
        Write-Output ("EMPTY=" + ($null -eq (ConvertFrom-GitHubRunnerJson -Json '')))
        """
    )
    result = _run_ps(driver)
    assert result.returncode == 0, result.stderr
    assert "DESK=1/2" in result.stdout
    assert "OG=1/1" in result.stdout
    assert "BADJSON=True" in result.stdout
    assert "EMPTY=True" in result.stdout


@PWSH_REQUIRED
def test_disk_floor_helper_is_pure_and_inclusive() -> None:
    """Below-floor is strict (< floor); exactly-at-floor is still healthy.
    Unknown/unparsable free space must NOT report a false breach."""
    driver = _prefix() + textwrap.dedent(
        """
        Write-Output ("R1=" + (Test-DiskBelowFloor -FreeGb 2.05 -FloorGb 40))
        Write-Output ("R2=" + (Test-DiskBelowFloor -FreeGb 289.2 -FloorGb 40))
        Write-Output ("R3=" + (Test-DiskBelowFloor -FreeGb 40 -FloorGb 40))
        Write-Output ("R4=" + (Test-DiskBelowFloor -FreeGb $null -FloorGb 40))
        """
    )
    result = _run_ps(driver)
    assert result.returncode == 0, result.stderr
    assert "R1=True" in result.stdout
    assert "R2=False" in result.stdout
    assert "R3=False" in result.stdout
    assert "R4=False" in result.stdout


@PWSH_REQUIRED
def test_purge_signature_requires_zero_online_and_local_activity() -> None:
    driver = _prefix() + textwrap.dedent(
        """
        Write-Output ("R1=" + (Test-PurgeSuspected -PoolOnline 0 -LocalUnitsActive 8))
        Write-Output ("R2=" + (Test-PurgeSuspected -PoolOnline 3 -LocalUnitsActive 8))
        Write-Output ("R3=" + (Test-PurgeSuspected -PoolOnline 0 -LocalUnitsActive 0))
        """
    )
    result = _run_ps(driver)
    assert result.returncode == 0, result.stderr
    assert "R1=True" in result.stdout
    assert "R2=False" in result.stdout
    assert "R3=False" in result.stdout


@PWSH_REQUIRED
def test_scheduler_state_parser_accepts_governed_target() -> None:
    driver = _prefix() + textwrap.dedent(
        """
        $state = '{"desired":2,"reason":"weekday-day","online":2}'
        Write-Output ("TARGET=" + (ConvertFrom-RunnerSchedulerState -Json $state))
        Write-Output ("OVERNIGHT=" + (ConvertFrom-RunnerSchedulerState -Json '{"desired":4}'))
        Write-Output ("ZERO=" + (ConvertFrom-RunnerSchedulerState -Json '{"desired":0}'))
        """
    )
    result = _run_ps(driver)
    assert result.returncode == 0, result.stderr
    assert "TARGET=2" in result.stdout
    assert "OVERNIGHT=4" in result.stdout
    assert "ZERO=0" in result.stdout


@PWSH_REQUIRED
def test_scheduler_state_parser_fails_closed() -> None:
    driver = _prefix() + textwrap.dedent(
        """
        Write-Output ("EMPTY=" + ($null -eq (ConvertFrom-RunnerSchedulerState -Json '')))
        Write-Output ("BAD=" + ($null -eq (ConvertFrom-RunnerSchedulerState -Json 'not-json')))
        Write-Output ("MISSING=" + ($null -eq (ConvertFrom-RunnerSchedulerState -Json '{}')))
        Write-Output ("NEGATIVE=" + ($null -eq (ConvertFrom-RunnerSchedulerState -Json '{"desired":-1}')))
        """
    )
    result = _run_ps(driver)
    assert result.returncode == 0, result.stderr
    assert "EMPTY=True" in result.stdout
    assert "BAD=True" in result.stdout
    assert "MISSING=True" in result.stdout
    assert "NEGATIVE=True" in result.stdout
