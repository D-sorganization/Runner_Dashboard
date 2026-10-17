"""Tests for deploy/refresh-maintenance-assets.sh (#1927): hosts must not drift onto stale schedulers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPO_ROOT / "deploy"
SCRIPT = DEPLOY / "refresh-maintenance-assets.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)


class _Prefix:
    """A throwaway install prefix standing in for /usr/local and /etc/systemd/system."""

    def __init__(self, root: Path) -> None:
        self.bin = root / "bin"
        self.sbin = root / "sbin"
        self.units = root / "units"
        for d in (self.bin, self.sbin, self.units):
            d.mkdir()
        self.log = root / "systemctl.log"
        self.systemctl = root / "fake-systemctl"
        self.systemctl.write_text(f'#!/usr/bin/env bash\necho "$*" >> "{as_bash_path(self.log)}"\n', encoding="utf-8")
        self.systemctl.chmod(0o755)

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        assert BASH
        env_prefix = {
            "MAINT_BIN_DIR": as_bash_path(self.bin),
            "MAINT_SBIN_DIR": as_bash_path(self.sbin),
            "MAINT_UNIT_DIR": as_bash_path(self.units),
            "MAINT_SUDO": "",
            "MAINT_SYSTEMCTL": as_bash_path(self.systemctl),
        }
        exports = "; ".join(f"export {k}='{v}'" for k, v in env_prefix.items())
        cmd = f'{exports}; bash "{as_bash_path(SCRIPT)}" {" ".join(args)}'
        return subprocess.run([BASH, "-c", cmd], capture_output=True, text=True, check=False, timeout=60)

    def systemctl_calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []


@pytest.fixture
def prefix(tmp_path: Path) -> _Prefix:
    return _Prefix(tmp_path)


def _provision(prefix: _Prefix, scheduler_text: str = "# stale scheduler\n") -> Path:
    """A host provisioned by install-runner-maintenance.sh with an old scheduler."""
    installed = prefix.bin / "runner-scheduler"
    installed.write_text(scheduler_text, encoding="utf-8", newline="")
    (prefix.units / "runner-scheduler.service").write_text(
        f"[Service]\nExecStart=/venv/bin/python {as_bash_path(installed)} --apply\n", encoding="utf-8"
    )
    return installed


def test_script_exists_with_lf_endings() -> None:
    assert SCRIPT.exists()
    assert b"\r" not in SCRIPT.read_bytes()


def test_stale_installed_scheduler_is_refreshed_from_repo(prefix: _Prefix) -> None:
    installed = _provision(prefix)

    result = prefix.run()

    assert result.returncode == 0, result.stderr
    assert installed.read_bytes() == (DEPLOY / "runner-scheduler.py").read_bytes()


def test_current_scheduler_is_left_untouched(prefix: _Prefix) -> None:
    installed = _provision(prefix, (DEPLOY / "runner-scheduler.py").read_text(encoding="utf-8"))
    before = installed.stat().st_mtime_ns

    result = prefix.run()

    assert result.returncode == 0, result.stderr
    assert installed.stat().st_mtime_ns == before


def test_qualified_release_host_is_skipped(prefix: _Prefix) -> None:
    """Qualified-release hosts run the scheduler from the release dir, not /usr/local/bin."""
    (prefix.units / "runner-scheduler.service").write_text(
        "[Service]\nExecStart=/opt/rel/4.10-abc/runtime/bin/python /opt/rel/4.10-abc/runner-scheduler.py --apply\n",
        encoding="utf-8",
    )

    result = prefix.run()

    assert result.returncode == 0, result.stderr
    assert not (prefix.bin / "runner-scheduler").exists()


def test_unprovisioned_host_is_not_provisioned_by_update(prefix: _Prefix) -> None:
    result = prefix.run()

    assert result.returncode == 0, result.stderr
    assert not (prefix.bin / "runner-scheduler").exists()


def test_cache_trim_assets_refreshed_and_systemd_reloaded(prefix: _Prefix) -> None:
    _provision(prefix, (DEPLOY / "runner-scheduler.py").read_text(encoding="utf-8"))
    (prefix.sbin / "wsl-cache-trim").write_text("old\n", encoding="utf-8")
    (prefix.units / "wsl-cache-trim.timer").write_text("old\n", encoding="utf-8")
    (prefix.units / "wsl-cache-trim.service").write_text("old\n", encoding="utf-8")

    result = prefix.run()

    assert result.returncode == 0, result.stderr
    assert (prefix.sbin / "wsl-cache-trim").read_bytes() == (DEPLOY / "wsl-cache-trim").read_bytes()
    assert (prefix.units / "wsl-cache-trim.timer").read_bytes() == (DEPLOY / "wsl-cache-trim.timer").read_bytes()
    assert "daemon-reload" in prefix.systemctl_calls()


def test_no_daemon_reload_when_units_unchanged(prefix: _Prefix) -> None:
    _provision(prefix, (DEPLOY / "runner-scheduler.py").read_text(encoding="utf-8"))

    prefix.run()

    assert prefix.systemctl_calls() == []


def test_check_mode_reports_drift_without_writing(prefix: _Prefix) -> None:
    installed = _provision(prefix)

    result = prefix.run("--check")

    assert result.returncode == 1
    assert "runner-scheduler" in result.stdout + result.stderr
    assert installed.read_text(encoding="utf-8") == "# stale scheduler\n"


def test_check_mode_passes_when_current_or_skipped(prefix: _Prefix) -> None:
    _provision(prefix, (DEPLOY / "runner-scheduler.py").read_text(encoding="utf-8"))
    assert prefix.run("--check").returncode == 0


def test_update_deployed_and_deploy_check_wire_the_refresh() -> None:
    update = (DEPLOY / "update-deployed.sh").read_text(encoding="utf-8")
    check = (DEPLOY / "deploy-check.sh").read_text(encoding="utf-8")
    assert "refresh-maintenance-assets.sh" in update
    assert "refresh-maintenance-assets.sh" in check and "--check" in check


# --- runner hooks (2026-10-17): the restore timer only restores MISSING hooks, so
# content drift must be fixed here, from the deployed repo copy. -------------------

HOOKS = ("job-started.sh", "job-completed.sh", "force-drain.sh")


def _install_hooks(prefix: _Prefix, text: str = "# stale 3,697-byte hook\n") -> Path:
    hook_dir = prefix.bin / "runner-hooks"
    hook_dir.mkdir()
    for hook in HOOKS:
        (hook_dir / hook).write_text(text, encoding="utf-8")
    return hook_dir


def test_stale_runner_hooks_are_refreshed_from_repo(prefix: _Prefix) -> None:
    hook_dir = _install_hooks(prefix)

    result = prefix.run()

    assert result.returncode == 0, result.stderr
    for hook in HOOKS:
        assert (hook_dir / hook).read_bytes() == (DEPLOY / "runner-hooks" / hook).read_bytes()


def test_missing_runner_hooks_are_not_provisioned(prefix: _Prefix) -> None:
    """An absent hook is runner-hooks-restore.sh's job; refresh only fixes drift."""
    result = prefix.run()

    assert result.returncode == 0, result.stderr
    assert not (prefix.bin / "runner-hooks").exists()


def test_check_mode_reports_runner_hook_drift_without_writing(prefix: _Prefix) -> None:
    hook_dir = _install_hooks(prefix)

    result = prefix.run("--check")

    assert result.returncode == 1
    assert "job-started.sh" in result.stdout
    assert (hook_dir / "job-started.sh").read_text(encoding="utf-8") == "# stale 3,697-byte hook\n"


def test_current_runner_hooks_pass_check(prefix: _Prefix) -> None:
    hook_dir = prefix.bin / "runner-hooks"
    hook_dir.mkdir()
    for hook in HOOKS:
        (hook_dir / hook).write_bytes((DEPLOY / "runner-hooks" / hook).read_bytes())

    assert prefix.run("--check").returncode == 0


# --- runner-hooks-restore itself (#1975) --------------------------------------
# DeskComputer 2026-10-17: refresh installed the #1971 hooks at 13:39Z and the
# installed pre-#1971 restore script ("differs from source -> reinstall")
# downgraded them from a stale /opt checkout at 13:43Z, on every 5-min tick.


def test_stale_installed_restore_script_is_refreshed(prefix: _Prefix) -> None:
    installed = prefix.bin / "runner-hooks-restore"
    installed.write_text("# pre-#1971 restore that rewrites hook content\n", encoding="utf-8")

    result = prefix.run()

    assert result.returncode == 0, result.stderr
    assert installed.read_bytes() == (DEPLOY / "runner-hooks-restore.sh").read_bytes()


def test_check_mode_reports_restore_script_drift(prefix: _Prefix) -> None:
    installed = prefix.bin / "runner-hooks-restore"
    installed.write_text("# old\n", encoding="utf-8")

    result = prefix.run("--check")

    assert result.returncode == 1
    assert "runner-hooks-restore" in result.stdout
    assert installed.read_text(encoding="utf-8") == "# old\n"


def test_missing_restore_script_is_not_provisioned(prefix: _Prefix) -> None:
    assert prefix.run().returncode == 0
    assert not (prefix.bin / "runner-hooks-restore").exists()
