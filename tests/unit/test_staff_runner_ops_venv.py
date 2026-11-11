"""Regression tests for staff run virtual environment isolation (#3902).

Validates setup with read-only HOME, per-role and per-worktree venv resolution,
virtualenv reuse, and error handling.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest
from staff import runner_ops
from staff.runner_venv import (
    ensure_staff_pytest_environment,
    resolve_staff_venv_dir,
)


def _cp(
    argv: list[str], code: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr=stderr)


_STUB_VERIFY_GATES = (
    "import argparse\n"
    "if __name__ == '__main__':\n"
    "    argparse.ArgumentParser().parse_args()\n"
)


@pytest.mark.unit
def test_setup_with_read_only_home_creates_writable_venv_without_user_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for #3902: when HOME is read-only, setup creates a writable venv and never uses --user."""
    readonly_home = tmp_path / "readonly_home"
    readonly_home.mkdir()
    # Make readonly_home read-only
    readonly_home.chmod(stat.S_IRUSR | stat.S_IXUSR)

    monkeypatch.setenv("HOME", str(readonly_home))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("STAFF_VENV_DIR", raising=False)

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "scripts").mkdir()
    (worktree / "scripts" / "verify_merge_gates.py").write_text(
        _STUB_VERIFY_GATES, encoding="utf-8"
    )

    calls: list[list[str]] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "--help" in command:
            return _cp(command, 0, "usage\n")
        if command[:3] == ["python3.11", "-c", runner_ops._PYTHON_VERSION_SNIPPET]:
            return _cp(command, 0, "3.11\n")
        # Base python lacks pytest (triggers venv creation and install)
        if command[:3] == ["python3.11", "-m", "pytest"] and "--version" in command:
            return _cp(command, 1, stderr="No module named pytest")
        if command[:3] == ["python3.11", "-m", "venv"]:
            return _cp(command, 0)
        if "-m" in command and "pip" in command and "install" in command:
            return _cp(command, 0, "Successfully installed pytest")
        if "-m" in command and "pytest" in command and "--version" in command:
            return _cp(command, 0, "pytest 9.0.3\n")
        raise AssertionError(f"unexpected command: {command}")

    try:
        result = runner_ops.run_worktree_bootstrap_preflight(
            worktree,
            role="rd-barb",
            run=fake_run,
            which=lambda name: "python3.11" if name == "python3.11" else None,
        )

        assert result.ok is True
        assert "python" in result.python_executable
        # Writable venv was resolved in writable per-role cache
        assert result.venv_dir.endswith("/.venvs/rd-barb")
        # Crucial invariant: pip must never receive --user
        assert not any("--user" in cmd for cmd in calls)
        # Venv was created and pip install was invoked in the venv
        assert any(cmd[:3] == ["python3.11", "-m", "venv"] for cmd in calls)
        assert any("-m" in cmd and "pip" in cmd and "install" in cmd for cmd in calls)

        # Also test without role: resolves to worktree/.venv
        calls.clear()
        res_no_role = runner_ops.run_worktree_bootstrap_preflight(
            worktree,
            run=fake_run,
            which=lambda name: "python3.11" if name == "python3.11" else None,
        )
        assert res_no_role.ok is True
        assert res_no_role.venv_dir == str(worktree / ".venv")
    finally:
        # Restore permissions for cleanup
        readonly_home.chmod(stat.S_IRWXU)


@pytest.mark.unit
def test_reusable_venv_with_existing_pytest_skips_venv_creation_and_install(
    tmp_path: Path,
) -> None:
    """When a venv already exists and has pytest, preflight reuses it without re-running venv/pip."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    venv_dir = worktree / ".venv"
    bin_dir = venv_dir / "bin"
    bin_dir.mkdir(parents=True)
    fake_python = bin_dir / "python"
    fake_python.touch()

    calls: list[list[str]] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        # venv python already has pytest
        if str(fake_python) in command[0] and "-m" in command and "pytest" in command:
            return _cp(command, 0, "pytest 9.0.3\n")
        raise AssertionError(f"unexpected command: {command}")

    ok, py_exe, attention, resolved_dir = ensure_staff_pytest_environment(
        worktree,
        "python3.11",
        run=fake_run,
    )

    assert ok is True
    assert py_exe == str(fake_python)
    assert attention == ""
    assert resolved_dir == str(venv_dir)
    # Neither venv creation nor pip install was run
    assert not any("venv" in cmd for cmd in calls)
    assert not any("pip" in cmd for cmd in calls)


@pytest.mark.unit
def test_resolve_staff_venv_dir_respects_custom_and_xdg_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    # Default fallback: worktree/.venv
    monkeypatch.delenv("STAFF_VENV_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    assert resolve_staff_venv_dir(worktree) == worktree / ".venv"

    # STAFF_VENV_DIR override
    custom_dir = tmp_path / "custom_venvs"
    monkeypatch.setenv("STAFF_VENV_DIR", str(custom_dir))
    resolved = resolve_staff_venv_dir(worktree, role="bob")
    assert resolved == custom_dir / "bob"

    # XDG_CACHE_HOME override
    monkeypatch.delenv("STAFF_VENV_DIR", raising=False)
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_dir))
    resolved_cache = resolve_staff_venv_dir(worktree, role="rd-barb")
    assert resolved_cache == cache_dir / "runner-dashboard" / "venvs" / "rd-barb"


@pytest.mark.unit
def test_venv_creation_failure_fails_preflight_cleanly(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["python3.11", "-m", "pytest"]:
            return _cp(command, 1, stderr="No module named pytest")
        if command[:3] == ["python3.11", "-m", "venv"]:
            return _cp(command, 1, stderr="disk full during venv")
        raise AssertionError(f"unexpected command: {command}")

    ok, py_exe, attention, _ = ensure_staff_pytest_environment(
        worktree,
        "python3.11",
        run=fake_run,
    )

    assert ok is False
    assert py_exe == "python3.11"
    assert "failed to create virtual environment" in attention
    assert "disk full during venv" in attention


@pytest.mark.unit
def test_pip_install_failure_fails_preflight_cleanly(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["python3.11", "-m", "pytest"]:
            return _cp(command, 1, stderr="No module named pytest")
        if command[:3] == ["python3.11", "-m", "venv"]:
            return _cp(command, 0)
        if "-m" in command and "pip" in command:
            return _cp(command, 1, stderr="network unreachable")
        raise AssertionError(f"unexpected command: {command}")

    ok, _, attention, _ = ensure_staff_pytest_environment(
        worktree,
        "python3.11",
        run=fake_run,
    )

    assert ok is False
    assert "could not be installed" in attention
    assert "network unreachable" in attention
