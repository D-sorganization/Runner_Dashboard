"""Behavioural contract tests for deploy/install-storage-gc.sh (#1925).

The installer is the single owner of the storage-GC units. These tests run the
real script against a sandboxed filesystem root (``STORAGE_GC_ROOT``) with a
fake ``systemctl`` so every pre/postcondition is exercised without root and
without touching the host. Root-run test processes are skipped outright: with
a real root the sandbox guard is the only thing between a test bug and the
host's /etc/systemd.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
INSTALLER = DEPLOY / "install-storage-gc.sh"
CLEANUP = DEPLOY / "runner-cleanup.sh"

pytestmark = [
    pytest.mark.skipif(BASH is None, reason=SKIP_REASON),
    pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="never run installer tests as root"),
]

UNITS = (
    "runner-cleanup.service",
    "runner-cleanup.timer",
    "runner-disk-guard.service",
    "runner-disk-guard.timer",
)

# Fake systemctl: records calls; `enable` marks timers enabled in a state file
# so `is-enabled` answers like the real thing.
SYSTEMCTL_SHIM = """#!/usr/bin/env bash
echo "$*" >> "$SHIM_DIR/calls.log"
case "$1" in
  is-enabled) grep -qx "$2" "$SHIM_DIR/enabled" 2>/dev/null && { echo enabled; exit 0; }; echo disabled; exit 1 ;;
  enable) shift; for u in "$@"; do [[ "$u" == --* ]] || echo "$u" >> "$SHIM_DIR/enabled"; done ;;
esac
exit 0
"""


@pytest.fixture
def sandbox(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "root"
    root.mkdir()
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "systemctl"
    shim.write_text(SYSTEMCTL_SHIM, encoding="utf-8", newline="\n")
    shim.chmod(0o755)
    return {"root": root, "shim_dir": shim_dir, "shim": shim, "tmp": tmp_path}


def _run(sb: dict[str, Path], *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    script = "\n".join(
        [
            "set -u",
            f'export STORAGE_GC_ROOT="{as_bash_path(sb["root"])}"',
            f'export SYSTEMCTL_BIN="{as_bash_path(sb["shim"])}"',
            f'export SHIM_DIR="{as_bash_path(sb["shim_dir"])}"',
            *(f'export {k}="{v}"' for k, v in env.items()),
            'export RUNNER_USER="${RUNNER_USER_OVERRIDE:-$(id -un)}"',
            f'exec bash "{as_bash_path(INSTALLER)}" "$@"',
        ]
    )
    harness = sb["tmp"] / "run.sh"
    harness.write_text(script, encoding="utf-8", newline="\n")
    return subprocess.run([BASH, as_bash_path(harness), *args], capture_output=True, text=True, check=False)


def _unit(sb: dict[str, Path], name: str) -> str:
    return (sb["root"] / "etc/systemd/system" / name).read_text(encoding="utf-8")


def _installed_binary(sb: dict[str, Path]) -> Path:
    return sb["root"] / "usr/local/bin/runner-cleanup"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_install_lays_down_binary_units_stamp_and_enables_timers(sandbox: dict[str, Path]) -> None:
    result = _run(sandbox, RUNNER_ROOTS="/home/ci/actions-runners")
    assert result.returncode == 0, result.stdout + result.stderr

    binary = _installed_binary(sandbox)
    assert binary.read_bytes() == CLEANUP.read_bytes()
    for unit in UNITS:
        assert (sandbox["root"] / "etc/systemd/system" / unit).is_file(), unit
    assert "ExecStart=/usr/local/bin/runner-cleanup\n" in _unit(sandbox, "runner-cleanup.service")
    assert "ExecStart=/usr/local/bin/runner-cleanup --disk-guard" in _unit(sandbox, "runner-disk-guard.service")
    assert "Environment=RUNNER_ROOTS=/home/ci/actions-runners" in _unit(sandbox, "runner-cleanup.service")
    # Disk guard: hourly, so docker bloat is reclaimed long before the daily pass.
    assert "OnCalendar=hourly" in _unit(sandbox, "runner-disk-guard.timer")
    assert "Environment=DISK_PRESSURE_PERCENT=85" in _unit(sandbox, "runner-disk-guard.service")

    stamp = (sandbox["root"] / "usr/local/share/runner-cleanup/STAMP").read_text(encoding="utf-8")
    assert f"sha256={_sha(CLEANUP)}" in stamp
    assert "profile=runner" in stamp

    enabled = (sandbox["shim_dir"] / "enabled").read_text(encoding="utf-8").split()
    assert {"runner-cleanup.timer", "runner-disk-guard.timer"} <= set(enabled)
    assert result.stdout.rstrip().endswith("storage_gc_verify=ok")


def test_second_install_is_idempotent(sandbox: dict[str, Path]) -> None:
    assert _run(sandbox).returncode == 0
    mtimes = {u: (sandbox["root"] / "etc/systemd/system" / u).stat().st_mtime_ns for u in UNITS}
    again = _run(sandbox)
    assert again.returncode == 0, again.stdout + again.stderr
    assert "changed=0" in again.stdout
    assert mtimes == {u: (sandbox["root"] / "etc/systemd/system" / u).stat().st_mtime_ns for u in UNITS}


def test_execstart_override_dropin_is_neutralised_but_its_tuning_is_kept(sandbox: dict[str, Path]) -> None:
    """The DeskComputer failure: a drop-in silently swapped in a frozen fork."""
    dropin_dir = sandbox["root"] / "etc/systemd/system/runner-cleanup.service.d"
    dropin_dir.mkdir(parents=True)
    original = (
        "[Service]\nExecStart=\nExecStart=/usr/local/bin/runner-cleanup-bandwidth\n"
        "Environment=RUNNER_WORK_DAYS=7\nEnvironment=DISK_USAGE_THRESHOLD=85\n"
    )
    dropin = dropin_dir / "30-bandwidth-policy.conf"
    dropin.write_text(original, encoding="utf-8", newline="\n")

    result = _run(sandbox)
    assert result.returncode == 0, result.stdout + result.stderr

    rewritten = dropin.read_text(encoding="utf-8")
    assert "ExecStart" not in rewritten
    assert "Environment=RUNNER_WORK_DAYS=7" in rewritten
    assert "Environment=DISK_USAGE_THRESHOLD=85" in rewritten
    backup = dropin_dir / "30-bandwidth-policy.conf.bak-storage-gc"
    assert backup.read_text(encoding="utf-8") == original
    assert "neutralised ExecStart override" in result.stdout


def test_verify_detects_binary_drift(sandbox: dict[str, Path]) -> None:
    assert _run(sandbox).returncode == 0
    _installed_binary(sandbox).write_text("#!/bin/sh\necho fork\n", encoding="utf-8", newline="\n")
    result = _run(sandbox, "--verify")
    assert result.returncode == 1
    assert "check=binary_hash status=FAIL" in result.stdout
    assert result.stdout.rstrip().endswith("storage_gc_verify=fail")


def test_verify_detects_a_new_execstart_override(sandbox: dict[str, Path]) -> None:
    assert _run(sandbox).returncode == 0
    dropin_dir = sandbox["root"] / "etc/systemd/system/runner-disk-guard.service.d"
    dropin_dir.mkdir(parents=True)
    (dropin_dir / "99-x.conf").write_text("[Service]\nExecStart=\nExecStart=/bin/true\n", encoding="utf-8")
    result = _run(sandbox, "--verify")
    assert result.returncode == 1
    assert "check=no_execstart_override status=FAIL" in result.stdout


def test_verify_detects_a_disabled_timer(sandbox: dict[str, Path]) -> None:
    assert _run(sandbox).returncode == 0
    (sandbox["shim_dir"] / "enabled").write_text("runner-cleanup.timer\n", encoding="utf-8")
    result = _run(sandbox, "--verify")
    assert result.returncode == 1
    assert "check=timer_enabled:runner-disk-guard.timer status=FAIL" in result.stdout


def test_verify_is_read_only(sandbox: dict[str, Path]) -> None:
    result = _run(sandbox, "--verify")
    assert result.returncode == 1
    assert not any(sandbox["root"].iterdir()), "--verify must never write"


def test_workstation_profile_disables_ci_host_aggression(sandbox: dict[str, Path]) -> None:
    result = _run(sandbox, "--profile", "workstation")
    assert result.returncode == 0, result.stdout + result.stderr
    for unit in ("runner-cleanup.service", "runner-disk-guard.service"):
        text = _unit(sandbox, unit)
        assert "Environment=CLEANUP_TMP=0" in text
        assert "Environment=AGGRESSIVE_ON_PRESSURE=0" in text
        assert "Environment=PRUNE_DOCKER_CONTAINERS=0" in text
    assert "profile=workstation" in (sandbox["root"] / "usr/local/share/runner-cleanup/STAMP").read_text()


def test_runner_profile_keeps_ci_defaults(sandbox: dict[str, Path]) -> None:
    assert _run(sandbox).returncode == 0
    text = _unit(sandbox, "runner-cleanup.service")
    assert "CLEANUP_TMP=0" not in text
    assert "Environment=RUNNER_WORK_DAYS=3" in text


def test_dry_run_writes_nothing(sandbox: dict[str, Path]) -> None:
    result = _run(sandbox, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(sandbox["root"].iterdir())
    assert not (sandbox["shim_dir"] / "calls.log").exists()


@pytest.mark.parametrize(
    ("args", "env", "needle"),
    [
        ((), {"RUNNER_ROOTS": "relative/runners"}, "must be absolute"),
        ((), {"RUNNER_USER_OVERRIDE": "no-such-user-1925"}, "does not exist"),
        (("--profile", "laptop"), {}, "unknown profile"),
        (("--bogus",), {}, "unknown argument"),
    ],
)
def test_precondition_violations_exit_2_and_write_nothing(
    sandbox: dict[str, Path], args: tuple[str, ...], env: dict[str, str], needle: str
) -> None:
    result = _run(sandbox, *args, **env)
    assert result.returncode == 2, result.stdout + result.stderr
    assert needle in result.stderr
    assert not any(sandbox["root"].iterdir())


def test_unparseable_source_is_refused(sandbox: dict[str, Path]) -> None:
    broken = sandbox["tmp"] / "broken.sh"
    broken.write_text("#!/usr/bin/env bash\nif then fi\n", encoding="utf-8", newline="\n")
    result = _run(sandbox, RUNNER_CLEANUP_SOURCE=as_bash_path(broken))
    assert result.returncode == 2
    assert "fails bash -n" in result.stderr
    assert not any(sandbox["root"].iterdir())


def test_real_root_requires_root(tmp_path: Path) -> None:
    """Without the sandbox, a non-root caller is refused before any write."""
    assert BASH is not None
    result = subprocess.run(
        [BASH, "-c", f'STORAGE_GC_ROOT=/ RUNNER_USER=$(id -un) bash "{as_bash_path(INSTALLER)}"'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "must run as root" in result.stderr
