"""Behavioural tests for canonical runner-hook wiring (2026-10-17).

Fleet survey findings these pin:

* DeskComputer: every ``<runner>/.env`` carried a legacy
  ``ACTIONS_RUNNER_HOOK_JOB_COMPLETED=.../fleet/runner-post-job.sh``. The runner
  ``.env`` wins over the unit's ``Environment=``, so the canonical hooks never
  ran. ``runner-hooks-restore.sh`` removes such overrides (with a backup), and
  ``job-completed.sh`` took over the legacy script's only duty: clearing
  ``_work/_temp``.
* ControlTower: the 8 runner units had no ``ACTIONS_RUNNER_HOOK_*`` at all.
  ``runner-hooks-restore.sh`` installs a ``15-runner-hooks.conf`` drop-in and
  daemon-reloads; it never restarts a unit.
* DeskComputer's ``/opt/runner-dashboard`` checkout was months stale, so the
  restore timer must never overwrite an installed hook's content -- only restore
  a missing one or re-chmod a non-executable one.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
RESTORE = DEPLOY / "runner-hooks-restore.sh"
JOB_COMPLETED = DEPLOY / "runner-hooks" / "job-completed.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)

_FAKE_SYSTEMCTL = r"""#!/usr/bin/env bash
# Minimal systemctl stand-in: one dir per unit under $FAKE_UNITS holding
# Environment and WorkingDirectory files.
case "$1" in
    list-unit-files)
        for d in "$FAKE_UNITS"/*/; do [ -d "$d" ] && echo "$(basename "$d") enabled enabled"; done ;;
    show)
        unit="$2"; prop="${3#--property=}"
        cat "$FAKE_UNITS/$unit/$prop" 2>/dev/null || true ;;
    daemon-reload|restart|stop|start)
        echo "$*" >> "$FAKE_LOG" ;;
esac
exit 0
"""


class _Host:
    """A throwaway host: hook dir, unit dir, runner dirs and a fake systemctl."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.hook_dir = root / "hooks"
        self.src_dir = root / "src-hooks"
        self.unit_dir = root / "systemd"
        self.units = root / "fake-units"
        self.prom = root / "prom"
        for d in (self.hook_dir, self.src_dir, self.unit_dir, self.units, self.prom):
            d.mkdir()
        for hook in ("job-started.sh", "job-completed.sh", "force-drain.sh"):
            (self.src_dir / hook).write_text(f"# stale source copy of {hook}\n", encoding="utf-8")
        self.log = root / "systemctl.log"
        self.systemctl = root / "fake-systemctl"
        self.systemctl.write_text(_FAKE_SYSTEMCTL, encoding="utf-8", newline="\n")
        self.systemctl.chmod(0o755)

    def canonical(self, hook: str) -> str:
        return f"{as_bash_path(self.hook_dir)}/{hook}"

    def wired_env(self) -> str:
        return (
            f"ACTIONS_RUNNER_HOOK_JOB_STARTED={self.canonical('job-started.sh')} "
            f"ACTIONS_RUNNER_HOOK_JOB_COMPLETED={self.canonical('job-completed.sh')} RUNNER_NAME=r1"
        )

    def add_unit(self, name: str, environment: str = "", env_file: str | None = None) -> Path:
        unit = f"actions.runner.D-sorganization.{name}.service"
        runner = self.root / "runners" / name
        (runner / "bin").mkdir(parents=True)
        (runner / ".runner").write_text("{}", encoding="utf-8")
        if env_file is not None:
            (runner / ".env").write_text(env_file, encoding="utf-8", newline="\n")
        meta = self.units / unit
        meta.mkdir()
        (meta / "Environment").write_text(environment + "\n", encoding="utf-8")
        (meta / "WorkingDirectory").write_text(as_bash_path(runner) + "\n", encoding="utf-8")
        return runner

    def dropin(self, name: str) -> Path:
        return self.unit_dir / f"actions.runner.D-sorganization.{name}.service.d" / "15-runner-hooks.conf"

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        assert BASH
        env = {
            "HOOK_SRC_DIR": as_bash_path(self.src_dir),
            "HOOK_DIR": as_bash_path(self.hook_dir),
            "TEXTFILE_COLLECTOR_DIR": as_bash_path(self.prom),
            "SYSTEMCTL": as_bash_path(self.systemctl),
            "SYSTEMD_UNIT_DIR": as_bash_path(self.unit_dir),
            "FAKE_UNITS": as_bash_path(self.units),
            "FAKE_LOG": as_bash_path(self.log),
        }
        exports = "; ".join(f"export {k}='{v}'" for k, v in env.items())
        cmd = f'{exports}; bash "{as_bash_path(RESTORE)}" {" ".join(args)}'
        return subprocess.run([BASH, "-c", cmd], capture_output=True, text=True, check=False, timeout=60)

    def systemctl_calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def metrics(self) -> str:
        return (self.prom / "runner_hooks.prom").read_text(encoding="utf-8")


@pytest.fixture
def host(tmp_path: Path) -> _Host:
    return _Host(tmp_path)


def _install_current_hooks(host: _Host) -> None:
    for hook in ("job-started.sh", "job-completed.sh", "force-drain.sh"):
        dst = host.hook_dir / hook
        dst.write_text(f"# newer installed {hook}\n", encoding="utf-8")
        dst.chmod(0o755)


def test_restore_scripts_have_lf_endings() -> None:
    for script in (RESTORE, JOB_COMPLETED):
        assert b"\r" not in script.read_bytes()


# --- hook presence: never downgrade ------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="exec bits are POSIX-only")
def test_restore_never_overwrites_an_installed_hook_with_a_stale_source(host: _Host) -> None:
    _install_current_hooks(host)

    result = host.run()

    assert result.returncode == 0, result.stderr
    for hook in ("job-started.sh", "job-completed.sh", "force-drain.sh"):
        assert (host.hook_dir / hook).read_text(encoding="utf-8") == f"# newer installed {hook}\n"
    assert "runner_hooks_restore_total 0" in host.metrics()


@pytest.mark.skipif(os.name != "posix", reason="exec bits are POSIX-only")
def test_restore_installs_missing_hooks_and_rechmods_without_rewriting(host: _Host) -> None:
    _install_current_hooks(host)
    (host.hook_dir / "job-completed.sh").unlink()
    (host.hook_dir / "force-drain.sh").chmod(0o644)

    result = host.run()

    assert result.returncode == 0, result.stderr
    assert (host.hook_dir / "job-completed.sh").read_text(
        encoding="utf-8"
    ) == "# stale source copy of job-completed.sh\n"
    assert os.access(host.hook_dir / "force-drain.sh", os.X_OK)
    assert (host.hook_dir / "force-drain.sh").read_text(encoding="utf-8") == "# newer installed force-drain.sh\n"
    assert "runner_hooks_restore_total 2" in host.metrics()


# --- unit wiring ----------------------------------------------------------------


def test_unwired_unit_gets_a_dropin_and_one_daemon_reload(host: _Host) -> None:
    _install_current_hooks(host)
    host.add_unit("ct-1")
    host.add_unit("ct-2", environment="RUNNER_NAME=ct-2-registered")
    host.add_unit("wired", environment=host.wired_env())

    result = host.run()

    assert result.returncode == 0, result.stderr
    dropin = host.dropin("ct-1").read_text(encoding="utf-8")
    assert f"Environment=ACTIONS_RUNNER_HOOK_JOB_STARTED={host.canonical('job-started.sh')}" in dropin
    assert f"Environment=ACTIONS_RUNNER_HOOK_JOB_COMPLETED={host.canonical('job-completed.sh')}" in dropin
    assert "Environment=RUNNER_NAME=ct-1" in dropin
    assert "RUNNER_NAME" not in host.dropin("ct-2").read_text(encoding="utf-8"), "an existing RUNNER_NAME wins"
    assert not host.dropin("wired").exists()
    assert host.systemctl_calls() == ["daemon-reload"], "never restart a unit; one reload only"
    assert "runner_hooks_unit_wiring_missing 2" in host.metrics()
    assert "runner_hooks_unit_dropins_written 2" in host.metrics()


def test_unit_pointing_at_a_foreign_hook_is_rewired(host: _Host) -> None:
    _install_current_hooks(host)
    host.add_unit(
        "old",
        environment="ACTIONS_RUNNER_HOOK_JOB_STARTED=/opt/runner-dashboard/deploy/runner-hooks/job-started.sh "
        "ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/opt/runner-dashboard/deploy/runner-hooks/job-completed.sh",
    )

    assert host.run().returncode == 0
    assert host.dropin("old").exists()


@pytest.mark.skipif(os.name != "posix", reason="--check reads exec bits, which are POSIX-only")
def test_fully_wired_host_is_a_noop(host: _Host) -> None:
    _install_current_hooks(host)
    host.add_unit("r1", environment=host.wired_env())

    assert host.run().returncode == 0
    assert host.systemctl_calls() == []
    assert not host.dropin("r1").exists()
    assert host.run("--check").returncode == 0


# --- runner .env overrides --------------------------------------------------------


_LEGACY_ENV = (
    "LANG=C.UTF-8\n"
    "ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/home/ci/actions-runners/fleet/runner-post-job.sh\n"
    'ACTIONS_RUNNER_HOOK_JOB_STARTED="/home/ci/legacy-start.sh"\n'
    "TMPDIR=/home/ci/actions-runners/runner-1/_work/_tmp\n"
)


def test_env_override_is_removed_with_a_backup(host: _Host) -> None:
    _install_current_hooks(host)
    runner = host.add_unit("r1", environment=host.wired_env(), env_file=_LEGACY_ENV)

    result = host.run()

    assert result.returncode == 0, result.stderr
    env = (runner / ".env").read_text(encoding="utf-8")
    assert "ACTIONS_RUNNER_HOOK_" not in env
    assert "LANG=C.UTF-8" in env and "TMPDIR=" in env, "unrelated lines must survive"
    backups = list(runner.glob(".env.bak.*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == _LEGACY_ENV
    assert "runner_hooks_env_overrides_removed 2" in host.metrics()

    # Idempotent: a second pass changes nothing and takes no new backup.
    assert host.run().returncode == 0
    assert len(list(runner.glob(".env.bak.*"))) == 1
    assert "runner_hooks_env_overrides 0" in host.metrics()


def test_env_line_pointing_at_the_canonical_hook_is_kept(host: _Host) -> None:
    _install_current_hooks(host)
    canonical = f"ACTIONS_RUNNER_HOOK_JOB_COMPLETED={host.canonical('job-completed.sh')}\n"
    runner = host.add_unit("r1", environment=host.wired_env(), env_file=canonical)

    assert host.run().returncode == 0
    assert (runner / ".env").read_text(encoding="utf-8") == canonical
    assert not list(runner.glob(".env.bak.*"))


def test_env_in_a_dir_that_is_not_a_runner_install_is_untouched(host: _Host) -> None:
    _install_current_hooks(host)
    runner = host.add_unit("r1", environment=host.wired_env(), env_file=_LEGACY_ENV)
    (runner / ".runner").unlink()

    assert host.run().returncode == 0
    assert (runner / ".env").read_text(encoding="utf-8") == _LEGACY_ENV


def test_check_mode_reports_all_drift_and_writes_nothing(host: _Host) -> None:
    _install_current_hooks(host)
    (host.hook_dir / "job-started.sh").unlink()
    runner = host.add_unit("r1", env_file=_LEGACY_ENV)

    result = host.run("--check")

    assert result.returncode == 1
    out = result.stdout + result.stderr
    assert "job-started.sh" in out and "lacks the canonical job hooks" in out and "overrides" in out
    assert not (host.hook_dir / "job-started.sh").exists()
    assert not host.dropin("r1").exists()
    assert (runner / ".env").read_text(encoding="utf-8") == _LEGACY_ENV
    assert host.systemctl_calls() == []
    assert not (host.prom / "runner_hooks.prom").exists()


def test_installer_ships_restore_with_a_timer() -> None:
    src = (DEPLOY / "install-runner-maintenance.sh").read_text(encoding="utf-8")
    assert "runner-hooks-restore.sh" in src and "runner-hooks-restore.timer" in src


# --- job-completed clears _work/_temp ------------------------------------------------


def _run_job_completed(tmp_path: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    assert BASH
    exports = "; ".join(f"export {k}='{v}'" for k, v in env_extra.items())
    cmd = (
        f"export RUNNER_BUSY_LOCK_DIR='{as_bash_path(tmp_path / 'locks')}' RUNNER_NAME=r1; {exports}; "
        f'bash "{as_bash_path(JOB_COMPLETED)}"'
    )
    return subprocess.run([BASH, "-c", cmd], capture_output=True, text=True, check=False, timeout=60)


def _seed_temp(temp: Path) -> None:
    (temp / "_runner_file_commands").mkdir(parents=True)
    (temp / "_runner_file_commands" / "set_env_x").write_text("", encoding="utf-8")
    (temp / "docker-ctx" / "deep").mkdir(parents=True)
    (temp / "docker-ctx" / "deep" / "f").write_text("x", encoding="utf-8")
    (temp / "step.sh").write_text("echo\n", encoding="utf-8")


def test_job_completed_clears_runner_temp_contents(tmp_path: Path) -> None:
    temp = tmp_path / "runner-1" / "_work" / "_temp"
    _seed_temp(temp)
    locks = tmp_path / "locks"
    locks.mkdir()
    (locks / "r1.lock").write_text("", encoding="utf-8")

    result = _run_job_completed(tmp_path, {"RUNNER_TEMP": as_bash_path(temp)})

    assert result.returncode == 0, result.stderr
    assert temp.is_dir(), "the _temp dir itself stays"
    assert sorted(p.name for p in temp.iterdir()) == ["_runner_file_commands"]
    assert (temp / "_runner_file_commands" / "set_env_x").exists(), "this step's file commands are still read"
    assert not (locks / "r1.lock").exists(), "the busy lockfile is still removed"


def test_job_completed_derives_temp_from_the_workspace(tmp_path: Path) -> None:
    temp = tmp_path / "runner-1" / "_work" / "_temp"
    _seed_temp(temp)
    ws = tmp_path / "runner-1" / "_work" / "Demo" / "Demo"
    ws.mkdir(parents=True)

    assert _run_job_completed(tmp_path, {"GITHUB_WORKSPACE": as_bash_path(ws)}).returncode == 0
    assert sorted(p.name for p in temp.iterdir()) == ["_runner_file_commands"]


def test_job_completed_never_clears_a_dir_that_is_not_runner_temp(tmp_path: Path) -> None:
    other = tmp_path / "somewhere" / "_temp"
    _seed_temp(other)

    assert _run_job_completed(tmp_path, {"RUNNER_TEMP": as_bash_path(other)}).returncode == 0
    assert (other / "step.sh").exists()
