"""Tests for the backup_state step in scheduled-dashboard-maintenance.sh.

Issue #417: dry-run mode must list the paths that would be archived
without actually creating the tarball, and BACKUP_DIR must be overridable
via environment variable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_SCRIPT = _ROOT / "deploy" / "scheduled-dashboard-maintenance.sh"


def _find_bash() -> str | None:
    candidates = [
        os.environ.get("BASH"),
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files\Git\bin\bash.exe",
        "/usr/bin/bash",
        "/bin/bash",
        shutil.which("bash"),
    ]
    for candidate in candidates:
        if candidate:
            try:
                p = Path(candidate)
                if p.exists():
                    if candidate.lower().endswith(r"system32\bash.exe"):
                        continue
                    return str(p)
            except Exception:
                pass
    return None


def _bash_path(path: Path) -> str:
    if os.name != "nt":
        return str(path)
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    bash_exe = _find_bash()
    if bash_exe and "system32" in bash_exe.lower():
        return f"/mnt/{drive}{resolved.as_posix()[2:]}"
    return f"/{drive}{resolved.as_posix()[2:]}"


def _bash_arg(value: str | Path) -> str:
    if isinstance(value, Path):
        return _bash_path(value)
    text = str(value)
    return text.replace("\\", "/") if os.name == "nt" else text


def _run_bash_or_skip(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    bash = _find_bash()
    if bash is None:
        pytest.skip("bash unavailable")

    env = kwargs.get("env")
    if env is None:
        env = dict(os.environ)
    else:
        env = dict(env)

    if os.name == "nt" and bash:
        bash_path = Path(bash)
        usr_bin = None
        curr = bash_path.parent
        for _ in range(3):
            candidate = curr / "usr" / "bin"
            if candidate.exists():
                usr_bin = candidate
                break
            if curr.name == "Git":
                break
            curr = curr.parent
        if usr_bin:
            usr_bin_str = str(usr_bin)
            existing_path = env.get("PATH", "")
            if usr_bin_str not in existing_path:
                env["PATH"] = f"{usr_bin_str}{os.pathsep}{existing_path}"
    kwargs["env"] = env

    try:
        result = subprocess.run(
            [bash, *(_bash_arg(arg) for arg in args)],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
            **kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        # Shell availability is already established at this point (bash was
        # found above), so a hang here is a real regression, not an
        # environment gap — fail with the command context instead of
        # silently skipping.
        raise AssertionError(f"bash timed out after {exc.timeout}s running {args!r} (bash={bash})") from exc
    if "\x00" in result.stdout + result.stderr:
        pytest.skip("bash shim returned non-text output")
    return result


def test_script_exists() -> None:
    assert _SCRIPT.is_file(), f"missing {_SCRIPT}"


def test_script_passes_bash_syntax_check() -> None:
    result = _run_bash_or_skip(["-n", "deploy/scheduled-dashboard-maintenance.sh"], cwd=str(_ROOT))
    assert result.returncode == 0, result.stderr


def test_script_defines_backup_state_function() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "backup_state()" in text or "backup_state ()" in text, (
        "scheduled-dashboard-maintenance.sh must define a backup_state function"
    )


def test_script_uses_default_backup_dir() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "/var/backups/runner-dashboard" in text, "default BACKUP_DIR must be /var/backups/runner-dashboard"


def test_script_retains_thirty_backups() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    # Either +31 (skip first 30) or +$((BACKUP_RETENTION+1)) — accept the literal default.
    assert "+31" in text or "BACKUP_RETENTION" in text, "retention policy must keep the most recent 30 backups"


def test_script_calls_backup_state_in_main() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    # Backup must be invoked from main() so cron picks it up.
    main_section = text.split("main()", 1)[-1]
    assert "backup_state" in main_section, "main() must invoke backup_state so cron triggers it"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
def test_dry_run_lists_paths_and_creates_no_tarball(tmp_path: Path) -> None:
    """Run the script with --dry-run and a tmp BACKUP_DIR, assert no tarball."""

    backup_dir = tmp_path / "backups"
    fake_repo = tmp_path / "repo"
    fake_dashboard = fake_repo / "runner-dashboard"
    (fake_repo / ".git").mkdir(parents=True)
    fake_dashboard.mkdir(parents=True)
    # Copy the maintenance script into the fake dashboard so its path discovery works.
    shutil.copytree(_ROOT / "deploy", fake_dashboard / "deploy")

    env = dict(os.environ)
    env["BACKUP_DIR"] = _bash_path(backup_dir)
    env["HOME"] = _bash_path(tmp_path / "home")
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / ".config" / "runner-dashboard").mkdir(parents=True)

    script = fake_dashboard / "deploy" / "scheduled-dashboard-maintenance.sh"
    result = _run_bash_or_skip(
        [str(Path("deploy") / script.name), "--dry-run", "--backup-only"],
        env=env,
        cwd=str(fake_dashboard),
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, f"dry-run failed: {combined}"
    assert "DRY-RUN" in combined or "dry-run" in combined.lower()
    # Lists what it would archive
    assert "config" in combined
    assert "runner-dashboard" in combined
    # Mentions the would-be tarball name
    assert "runner-dashboard-state-" in combined
    assert ".tar.gz" in combined
    # No actual tarball was created
    if backup_dir.exists():
        produced = list(backup_dir.glob("runner-dashboard-state-*.tar.gz"))
        assert produced == [], f"dry-run should not create tarballs, got {produced}"


def test_run_bash_or_skip_fails_on_timeout_when_bash_available(monkeypatch) -> None:
    """A hang must fail the test with command context, not silently skip."""
    if _find_bash() is None:
        pytest.skip("bash unavailable")

    def _raise_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["bash"], timeout=20)

    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    with pytest.raises(AssertionError, match="timed out"):
        _run_bash_or_skip(["-n", "deploy/scheduled-dashboard-maintenance.sh"], cwd=str(_ROOT))


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
def test_backup_state_retention_deletes_oldest_keeps_newest_and_preserves_unrelated_files(
    tmp_path: Path,
) -> None:
    """Real (non-dry-run) backup-only invocation must prune by age, not count alone."""

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    fake_repo = tmp_path / "repo"
    fake_dashboard = fake_repo / "runner-dashboard"
    (fake_repo / ".git").mkdir(parents=True)
    fake_dashboard.mkdir(parents=True)
    shutil.copytree(_ROOT / "deploy", fake_dashboard / "deploy")

    home = tmp_path / "home"
    (home / ".config" / "runner-dashboard").mkdir(parents=True)
    (home / ".config" / "runner-dashboard" / "principals.yml").write_text("agents: []\n")

    retention = 3
    # Five pre-existing backups, oldest to newest by both name and mtime.
    old_names = [f"runner-dashboard-state-2020010{i}T000000Z.tar.gz" for i in range(1, 6)]
    for idx, name in enumerate(old_names):
        fixture = backup_dir / name
        fixture.write_bytes(b"fake-tarball")
        os.utime(fixture, (1000 + idx, 1000 + idx))

    unrelated = backup_dir / "notes.txt"
    unrelated.write_text("unrelated, must survive")

    env = dict(os.environ)
    env["BACKUP_DIR"] = _bash_path(backup_dir)
    env["HOME"] = _bash_path(home)

    script = fake_dashboard / "deploy" / "scheduled-dashboard-maintenance.sh"
    result = _run_bash_or_skip(
        [str(Path("deploy") / script.name), "--backup-only", "--backup-retention", str(retention)],
        env=env,
        cwd=str(fake_dashboard),
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, f"backup-only run failed: {combined}"

    remaining = sorted(p.name for p in backup_dir.glob("runner-dashboard-state-*.tar.gz"))
    assert len(remaining) == retention, f"expected {retention} retained backups, got {remaining}"

    # The three oldest fixtures must be pruned; the two newest must survive.
    expected_deleted = old_names[:3]
    expected_retained_old = old_names[3:]
    for name in expected_deleted:
        assert not (backup_dir / name).exists(), f"{name} should have been pruned by retention"
    for name in expected_retained_old:
        assert (backup_dir / name).exists(), f"{name} should have survived retention"

    # Exactly one new backup was written by this run, and it is the most recent.
    new_backups = [n for n in remaining if n not in expected_retained_old]
    assert len(new_backups) == 1, f"expected exactly one new backup, got {new_backups}"
    newest_by_mtime = max(backup_dir.glob("runner-dashboard-state-*.tar.gz"), key=lambda p: p.stat().st_mtime)
    assert newest_by_mtime.name == new_backups[0], "the newly written backup must be the most recent file"

    assert unrelated.exists() and unrelated.read_text() == "unrelated, must survive"
