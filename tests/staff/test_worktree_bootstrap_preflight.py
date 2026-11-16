from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from staff.store import RunRecord, RunStore

from staff import runner_ops


def _cp(argv: list[str], code: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr=stderr)


_STUB_VERIFY_GATES = (
    "import argparse\n"
    "if __name__ == '__main__':\n"
    "    argparse.ArgumentParser().parse_args()\n"
)

_STUB_PRE_PR = (
    "import argparse\n"
    "if __name__ == '__main__':\n"
    "    argparse.ArgumentParser().parse_args()\n"
)


@pytest.mark.unit
def test_bootstrap_preflight_initializes_submodules_installs_pytest_and_resolves_repo_entrypoint(
    tmp_path: Path,
) -> None:
    (tmp_path / ".gitmodules").write_text(
        '[submodule "vendor/ud-tools"]\n\tpath = vendor/ud-tools\n',
        encoding="utf-8",
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify_merge_gates.py").write_text(_STUB_VERIFY_GATES, encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "--help" in command:
            return _cp(command, 0, "usage\n")
        if command[:2] == ["git", "--version"]:
            return _cp(command, 0, "git version 2.47.1\n")
        if command[:3] == ["git", "submodule", "status"]:
            return _cp(command, 0, "-abc123 vendor/ud-tools\n")
        if command[:2] == ["git", "worktree"]:
            return _cp(command, 0, f"worktree {tmp_path}\n")
        if command[:3] == ["python3.11", "-c", runner_ops._PYTHON_VERSION_SNIPPET]:
            return _cp(command, 0, "3.11\n")
        if command[:3] == ["python3.11", "-m", "pytest"] and "--version" in command:
            return _cp(command, 1, stderr="No module named pytest")
        if command[:3] == ["python3.11", "-m", "venv"]:
            return _cp(command, 0)
        if "-m" in command and "pip" in command and "install" in command:
            return _cp(command, 0, "installed")
        if "-m" in command and "pytest" in command and "--version" in command:
            return _cp(command, 0, "pytest 9.0.3\n")
        if "submodule" in command and "update" in command:
            return _cp(command, 0)
        raise AssertionError(f"unexpected command: {command}")

    result = runner_ops.run_worktree_bootstrap_preflight(
        tmp_path,
        run=fake_run,
        which=lambda name: "python3.11" if name == "python3.11" else None,
    )

    assert result.ok is True
    assert "python" in result.python_executable
    assert "scripts/verify_merge_gates.py" in result.pre_pr_entrypoint
    submodule_calls = [cmd for cmd in calls if "submodule" in cmd and "update" in cmd]
    assert len(submodule_calls) == 1
    assert "--depth" in submodule_calls[0] and "1" in submodule_calls[0]
    assert "--filter=blob:none" in submodule_calls[0]
    assert any(cmd[:3] == ["python3.11", "-m", "venv"] for cmd in calls)
    assert any("-m" in cmd and "pip" in cmd and "install" in cmd for cmd in calls)
    assert not any("--user" in cmd for cmd in calls)


@pytest.mark.unit
def test_bootstrap_preflight_skips_submodule_update_when_already_populated(tmp_path: Path) -> None:
    (tmp_path / ".gitmodules").write_text(
        '[submodule "vendor/ud-tools"]\n\tpath = vendor/ud-tools\n',
        encoding="utf-8",
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify_merge_gates.py").write_text(_STUB_VERIFY_GATES, encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "--help" in command:
            return _cp(command, 0, "usage\n")
        if command[:2] == ["git", "--version"]:
            return _cp(command, 0, "git version 2.47.1\n")
        if command[:3] == ["git", "submodule", "status"]:
            return _cp(command, 0, " abc123 vendor/ud-tools (heads/main)\n")
        if command[:3] == ["python3.11", "-c", runner_ops._PYTHON_VERSION_SNIPPET]:
            return _cp(command, 0, "3.11\n")
        if command[:3] == ["python3.11", "-m", "pytest"] and "--version" in command:
            return _cp(command, 0, "pytest 9.0.3\n")
        raise AssertionError(f"unexpected command: {command}")

    result = runner_ops.run_worktree_bootstrap_preflight(
        tmp_path,
        run=fake_run,
        which=lambda name: "python3.11" if name == "python3.11" else None,
    )

    assert result.ok is True
    assert not any("update" in cmd and "submodule" in cmd for cmd in calls)
    assert any(cmd[:3] == ["git", "submodule", "status"] for cmd in calls)


@pytest.mark.unit
def test_bootstrap_preflight_reuses_main_checkout_reference_when_available(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    main_checkout = tmp_path / "repo"
    (main_checkout / ".git" / "modules").mkdir(parents=True)
    (worktree / ".gitmodules").write_text(
        '[submodule "vendor/ud-tools"]\n\tpath = vendor/ud-tools\n',
        encoding="utf-8",
    )
    (worktree / "scripts").mkdir()
    (worktree / "scripts" / "verify_merge_gates.py").write_text(_STUB_VERIFY_GATES, encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "--help" in command:
            return _cp(command, 0, "usage\n")
        if command[:2] == ["git", "--version"]:
            return _cp(command, 0, "git version 2.47.1\n")
        if command[:3] == ["git", "submodule", "status"]:
            return _cp(command, 0, "-abc123 vendor/ud-tools\n")
        if command[:2] == ["git", "worktree"]:
            return _cp(command, 0, f"worktree {main_checkout}\nworktree {worktree}\n")
        if command[:3] == ["python3.11", "-c", runner_ops._PYTHON_VERSION_SNIPPET]:
            return _cp(command, 0, "3.11\n")
        if command[:3] == ["python3.11", "-m", "pytest"] and "--version" in command:
            return _cp(command, 0, "pytest 9.0.3\n")
        if "submodule" in command and "update" in command:
            return _cp(command, 0)
        raise AssertionError(f"unexpected command: {command}")

    result = runner_ops.run_worktree_bootstrap_preflight(
        worktree,
        run=fake_run,
        which=lambda name: "python3.11" if name == "python3.11" else None,
    )

    assert result.ok is True
    submodule_calls = [cmd for cmd in calls if "submodule" in cmd and "update" in cmd]
    assert len(submodule_calls) == 1
    assert "--reference" in submodule_calls[0]
    assert str(main_checkout) in submodule_calls[0]
    assert "--dissociate" in submodule_calls[0]
    assert not any("--reference-if-able" in arg for arg in submodule_calls[0])


@pytest.mark.unit
def test_submodule_update_command_on_git_234_omits_filter_and_uses_reference_with_dissociate(
    tmp_path: Path,
) -> None:
    from staff.runner_ops_worktree_bootstrap import _submodule_update_command

    worktree = tmp_path / "wt"
    worktree.mkdir()
    main_checkout = tmp_path / "repo"
    (main_checkout / ".git" / "modules").mkdir(parents=True)

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["git", "--version"]:
            return _cp(command, 0, "git version 2.34.1\n")
        if command[:2] == ["git", "worktree"]:
            return _cp(command, 0, f"worktree {main_checkout}\nworktree {worktree}\n")
        raise AssertionError(f"unexpected command: {command}")

    cmd = _submodule_update_command(worktree, fake_run)
    assert not any(arg.startswith("--filter") for arg in cmd)
    assert not any("--reference-if-able" in arg for arg in cmd)
    assert "--reference" in cmd
    assert str(main_checkout) in cmd
    assert "--dissociate" in cmd


@pytest.mark.unit
def test_submodule_update_command_on_git_236_includes_filter_blob_none(
    tmp_path: Path,
) -> None:
    from staff.runner_ops_worktree_bootstrap import _submodule_update_command

    worktree = tmp_path / "wt"
    worktree.mkdir()

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["git", "--version"]:
            return _cp(command, 0, "git version 2.36.0\n")
        if command[:2] == ["git", "worktree"]:
            return _cp(command, 0, f"worktree {worktree}\n")
        raise AssertionError(f"unexpected command: {command}")

    cmd = _submodule_update_command(worktree, fake_run)
    assert "--filter=blob:none" in cmd


@pytest.mark.unit
def test_bootstrap_preflight_catches_submodule_timeout_and_retries_once(tmp_path: Path) -> None:
    (tmp_path / ".gitmodules").write_text(
        '[submodule "vendor/ud-tools"]\n\tpath = vendor/ud-tools\n',
        encoding="utf-8",
    )

    attempts = {"count": 0}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["git", "--version"]:
            return _cp(command, 0, "git version 2.47.1\n")
        if command[:3] == ["git", "submodule", "status"]:
            return _cp(command, 0, "-abc123 vendor/ud-tools\n")
        if command[:2] == ["git", "worktree"]:
            return _cp(command, 0, f"worktree {tmp_path}\n")
        if "submodule" in command and "update" in command:
            attempts["count"] += 1
            raise subprocess.TimeoutExpired(command, timeout=float(str(kwargs.get("timeout", 300))))
        raise AssertionError(f"unexpected command: {command}")

    result = runner_ops.run_worktree_bootstrap_preflight(
        tmp_path,
        run=fake_run,
        which=lambda name: None,
    )

    assert attempts["count"] == runner_ops._SUBMODULE_ATTEMPTS
    assert result.ok is False
    assert result.failure_class == "submodule_timeout"
    assert result.retryable is True
    assert "timed out" in result.attention.lower()
    assert result.remediation


@pytest.mark.unit
def test_bootstrap_preflight_fails_when_python_is_below_311(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify_merge_gates.py").write_text(_STUB_VERIFY_GATES, encoding="utf-8")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["python3", "-c", runner_ops._PYTHON_VERSION_SNIPPET]:
            return _cp(command, 0, "3.10\n")
        raise AssertionError(f"unexpected command: {command}")

    result = runner_ops.run_worktree_bootstrap_preflight(
        tmp_path,
        run=fake_run,
        which=lambda name: "python3" if name == "python3" else None,
    )

    assert result.ok is False
    assert "python 3.11+" in result.attention.lower()


@pytest.mark.unit
def test_bootstrap_preflight_fails_when_no_repo_pre_pr_entrypoint_found(tmp_path: Path) -> None:
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["python3.11", "-c", runner_ops._PYTHON_VERSION_SNIPPET]:
            return _cp(command, 0, "3.11\n")
        if command[:3] == ["python3.11", "-m", "pytest"] and "--version" in command:
            return _cp(command, 0, "pytest 8.4.0")
        raise AssertionError(f"unexpected command: {command}")

    result = runner_ops.run_worktree_bootstrap_preflight(
        tmp_path,
        run=fake_run,
        which=lambda name: "python3.11" if name == "python3.11" else None,
    )

    assert result.ok is False
    assert "pre-pr" in result.attention.lower()


@pytest.mark.unit
def test_ensure_worktree_bootstrap_ready_fails_fast_with_environment_error(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")
    rec = RunRecord(
        id="run-preflight",
        role="night-watch",
        provider="fake",
        model=None,
        machine="TestNode",
        repo="Runner_Dashboard",
        target_kind="issue",
        target_ref="42",
        prompt="x",
        status="preparing",
    )
    store.create_run(rec)
    plan = runner_ops.RunPlan(
        role="night-watch",
        provider="fake",
        model="default",
        repo="Runner_Dashboard",
        target_kind="issue",
        target_ref="42",
        operator_prompt="x",
        prompt="x",
        argv=["fake"],
        branch="staff/night-watch-42",
        lease_ritual=False,
    )

    original = runner_ops.run_worktree_bootstrap_preflight
    runner_ops.run_worktree_bootstrap_preflight = lambda _workdir: runner_ops.WorktreeBootstrapPreflight(
        ok=False, attention="python 3.11+ missing"
    )
    try:
        result = runner_ops.ensure_worktree_bootstrap_ready(store, rec, plan, tmp_path)
    finally:
        runner_ops.run_worktree_bootstrap_preflight = original

    assert result is None
    updated = store.get_run(rec.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.failure_class == "environment_error"
    assert "python 3.11+ missing" in updated.error


@pytest.mark.unit
def test_ensure_worktree_bootstrap_ready_propagates_submodule_timeout_failure(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")
    rec = RunRecord(
        id="run-preflight-timeout",
        role="night-watch",
        provider="fake",
        model=None,
        machine="TestNode",
        repo="Runner_Dashboard",
        target_kind="issue",
        target_ref="42",
        prompt="x",
        status="preparing",
    )
    store.create_run(rec)
    plan = runner_ops.RunPlan(
        role="night-watch",
        provider="fake",
        model="default",
        repo="Runner_Dashboard",
        target_kind="issue",
        target_ref="42",
        operator_prompt="x",
        prompt="x",
        argv=["fake"],
        branch="staff/night-watch-42",
        lease_ritual=False,
    )

    original = runner_ops.run_worktree_bootstrap_preflight
    runner_ops.run_worktree_bootstrap_preflight = lambda _workdir: runner_ops.WorktreeBootstrapPreflight(
        ok=False,
        attention="git submodule update timed out after 300s on 2 attempt(s)",
        failure_class="submodule_timeout",
        retryable=True,
        remediation="Submodule fetch exceeded the per-attempt timeout; re-dispatch the run.",
    )
    try:
        result = runner_ops.ensure_worktree_bootstrap_ready(store, rec, plan, tmp_path)
    finally:
        runner_ops.run_worktree_bootstrap_preflight = original

    assert result is None
    updated = store.get_run(rec.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.failure_class == "submodule_timeout"
    assert updated.retryable is True
    assert "re-dispatch" in updated.remediation.lower()


@pytest.mark.unit
def test_submodule_lock_bootstrap_failure_schedules_automatic_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import MagicMock

    from staff.retry import handle_post_execution_retry, should_retry
    from staff.roles import RoleSpec

    store = RunStore(tmp_path / "runs.sqlite3")
    rec = RunRecord(
        id="run-submodule-lock",
        role="night-watch",
        provider="fake",
        model=None,
        machine="TestNode",
        repo="Runner_Dashboard",
        target_kind="issue",
        target_ref="42",
        prompt="x",
        status="preparing",
        attempt=1,
        max_attempts=3,
    )
    store.create_run(rec)
    plan = runner_ops.RunPlan(
        role="night-watch",
        provider="fake",
        model="default",
        repo="Runner_Dashboard",
        target_kind="issue",
        target_ref="42",
        operator_prompt="x",
        prompt="x",
        argv=["fake"],
        branch="staff/night-watch-42",
        lease_ritual=False,
    )

    lock_detail = "fatal: Unable to create '.../modules/vendor/ud-tools/index.lock': File exists."
    original = runner_ops.run_worktree_bootstrap_preflight
    runner_ops.run_worktree_bootstrap_preflight = lambda _workdir: runner_ops.WorktreeBootstrapPreflight(
        ok=False,
        attention=f"submodule init failed: {lock_detail}",
        failure_class="submodule_lock",
        retryable=True,
        remediation="Concurrent staff runs or a stale submodule index.lock blocked init; re-dispatch after locks age out.",
    )
    try:
        assert runner_ops.ensure_worktree_bootstrap_ready(store, rec, plan, tmp_path) is None
    finally:
        runner_ops.run_worktree_bootstrap_preflight = original

    updated = store.get_run(rec.id)
    assert updated is not None
    assert updated.failure_class == "submodule_lock"
    assert updated.retryable is True

    role = RoleSpec(name="night-watch", title="Night Watch", providers=("fake",), max_attempts=3)
    ok, reason = should_retry(updated, role)
    assert ok is True, reason

    runner = MagicMock()
    runner.store = store
    runner.machine = "TestNode"
    runner.roles.return_value = {"night-watch": role}
    runner._cancel_flags = set()
    runner._adapters = {
        "fake": MagicMock(
            installed=lambda: True,
            unattended=True,
            build_command=lambda *a, **k: ["fake"],
        ),
    }
    scheduled: list[tuple[RunRecord, object]] = []

    def capture_schedule(_runner: object, attempt_rec: RunRecord, attempt_plan: object, _delay: float) -> None:
        scheduled.append((attempt_rec, attempt_plan))

    monkeypatch.setattr("staff.retry.automatic_retry_admission", lambda *a, **k: (True, ""))
    monkeypatch.setattr("staff.retry._schedule", capture_schedule)

    assert handle_post_execution_retry(runner, updated, plan) is True
    assert len(scheduled) == 1
    attempt_rec, _ = scheduled[0]
    assert attempt_rec.retry_of == rec.id
    assert attempt_rec.attempt == 2


@pytest.mark.unit
def test_clean_stale_git_locks_removes_stale_locks_when_no_git_process_active(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    dot_git = worktree / ".git"
    dot_git.mkdir()
    (dot_git / "index.lock").write_text("lock", encoding="utf-8")
    (dot_git / "HEAD.lock").write_text("lock", encoding="utf-8")
    sub_modules = dot_git / "modules" / "vendor" / "ud-tools"
    sub_modules.mkdir(parents=True)
    (sub_modules / "index.lock").write_text("lock", encoding="utf-8")
    (sub_modules / "config.lock").write_text("lock", encoding="utf-8")

    removed = runner_ops.clean_stale_git_locks(
        worktree,
        is_git_process_active=lambda p: False,
    )

    assert len(removed) == 4
    assert not (dot_git / "index.lock").exists()
    assert not (dot_git / "HEAD.lock").exists()
    assert not (sub_modules / "index.lock").exists()
    assert not (sub_modules / "config.lock").exists()


@pytest.mark.unit
def test_clean_stale_git_locks_preserves_live_locks_when_git_process_active(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    dot_git = worktree / ".git"
    dot_git.mkdir()
    lock_file = dot_git / "index.lock"
    lock_file.write_text("live", encoding="utf-8")

    removed = runner_ops.clean_stale_git_locks(
        worktree,
        is_git_process_active=lambda p: True,
    )

    assert removed == []
    assert lock_file.exists()
    assert "live git process" in caplog.text.lower()


@pytest.mark.unit
def test_bootstrap_preflight_cleans_stale_locks_before_submodule_status(tmp_path: Path) -> None:
    dot_git = tmp_path / ".git"
    dot_git.mkdir()
    stale_lock = dot_git / "index.lock"
    stale_lock.write_text("stale", encoding="utf-8")
    (tmp_path / ".gitmodules").write_text(
        '[submodule "vendor/ud-tools"]\n\tpath = vendor/ud-tools\n',
        encoding="utf-8",
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify_merge_gates.py").write_text(_STUB_VERIFY_GATES, encoding="utf-8")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--help" in command:
            return _cp(command, 0, "usage\n")
        if command[:2] == ["git", "--version"]:
            return _cp(command, 0, "git version 2.47.1\n")
        if command[:3] == ["git", "submodule", "status"]:
            assert not stale_lock.exists()
            return _cp(command, 0, " abc123 vendor/ud-tools (heads/main)\n")
        if command[:3] == ["python3.11", "-c", runner_ops._PYTHON_VERSION_SNIPPET]:
            return _cp(command, 0, "3.11\n")
        if command[:3] == ["python3.11", "-m", "pytest"] and "--version" in command:
            return _cp(command, 0, "pytest 9.0.3\n")
        raise AssertionError(f"unexpected command: {command}")

    result = runner_ops.run_worktree_bootstrap_preflight(
        tmp_path,
        run=fake_run,
        which=lambda name: "python3.11" if name == "python3.11" else None,
        is_git_process_active=lambda p: False,
    )

    assert result.ok is True
    assert not stale_lock.exists()


@pytest.mark.unit
def test_is_git_process_active_in_repo_matches_git_process_and_repo_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_path = tmp_path / "my-repo"
    repo_path.mkdir()

    class FakeProc:
        def __init__(self, name: str, cmdline: list[str], cwd: str):
            self.info = {"name": name, "cmdline": cmdline, "cwd": cwd}

    # Case 1: active git process with matching cwd
    fake_procs = [
        FakeProc("git", ["git", "status"], str(repo_path)),
    ]
    import psutil

    monkeypatch.setattr(psutil, "process_iter", lambda attrs: fake_procs)
    assert runner_ops.is_git_process_active_in_repo(repo_path) is True

    # Case 2: active git process with matching cmdline
    fake_procs = [
        FakeProc("git", ["git", "-C", str(repo_path), "status"], "/tmp"),
    ]
    assert runner_ops.is_git_process_active_in_repo(repo_path) is True

    # Case 3: non-git process with matching cwd
    fake_procs = [
        FakeProc("python3", ["python3", "script.py"], str(repo_path)),
    ]
    assert runner_ops.is_git_process_active_in_repo(repo_path) is False

    # Case 4: git process in completely different repo
    fake_procs = [
        FakeProc("git", ["git", "status"], "/other/path"),
    ]
    assert runner_ops.is_git_process_active_in_repo(repo_path) is False

    # Case 5: exception during iteration is handled gracefully
    class BrokenProc:
        @property
        def info(self) -> dict[str, object]:
            raise psutil.NoSuchProcess(pid=1234)

    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [BrokenProc()])
    assert runner_ops.is_git_process_active_in_repo(repo_path) is False


@pytest.mark.unit
def test_clean_stale_git_locks_handles_linked_worktree_and_main_checkout(tmp_path: Path) -> None:
    main_repo = tmp_path / "main-repo"
    main_git = main_repo / ".git"
    main_modules = main_git / "modules" / "vendor" / "ud-tools"
    main_modules.mkdir(parents=True)
    (main_modules / "index.lock").write_text("lock", encoding="utf-8")

    real_wt_gitdir = main_git / "worktrees" / "staff-123"
    real_wt_gitdir.mkdir(parents=True)
    (real_wt_gitdir / "index.lock").write_text("lock", encoding="utf-8")

    wt_path = tmp_path / "staff-123"
    wt_path.mkdir()
    (wt_path / ".git").write_text(f"gitdir: {real_wt_gitdir}\n", encoding="utf-8")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:4] == ["git", "worktree", "list", "--porcelain"]:
            return _cp(command, 0, f"worktree {main_repo}\nworktree {wt_path}\n")
        raise AssertionError(f"unexpected command: {command}")

    removed = runner_ops.clean_stale_git_locks(
        wt_path,
        run=fake_run,
        is_git_process_active=lambda p: False,
    )

    assert len(removed) == 2
    assert not (real_wt_gitdir / "index.lock").exists()
    assert not (main_modules / "index.lock").exists()


@pytest.mark.unit
def test_clean_stale_git_locks_clears_main_module_lock_when_git_active_only_on_worktree(
    tmp_path: Path,
) -> None:
    main_repo = tmp_path / "UpstreamDrift"
    main_modules = main_repo / ".git" / "modules" / "vendor" / "ud-tools"
    main_modules.mkdir(parents=True)
    module_lock = main_modules / "index.lock"
    module_lock.write_text("lock", encoding="utf-8")

    wt_path = tmp_path / "staff-wt"
    wt_path.mkdir()
    wt_gitdir = main_repo / ".git" / "worktrees" / "staff-wt"
    wt_gitdir.mkdir(parents=True)
    (wt_path / ".git").write_text(f"gitdir: {wt_gitdir}\n", encoding="utf-8")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:4] == ["git", "worktree", "list", "--porcelain"]:
            return _cp(command, 0, f"worktree {main_repo}\nworktree {wt_path}\n")
        raise AssertionError(f"unexpected command: {command}")

    def active_only_worktree(repo_path: Path) -> bool:
        return repo_path == wt_path

    removed = runner_ops.clean_stale_git_locks(
        wt_path,
        run=fake_run,
        main_root=main_repo,
        is_git_process_active=active_only_worktree,
    )

    assert module_lock in removed
    assert not module_lock.exists()


@pytest.mark.unit
def test_main_modules_lock_scoped_to_main_when_checkout_path_contains_worktrees_component(
    tmp_path: Path,
) -> None:
    """Regression: main at ``.../worktrees/UpstreamDrift/.git/modules`` is not a linked worktree modules lock."""
    main_repo = tmp_path / "worktrees" / "UpstreamDrift"
    main_modules = main_repo / ".git" / "modules" / "vendor" / "ud-tools"
    main_modules.mkdir(parents=True)
    module_lock = main_modules / "index.lock"
    module_lock.write_text("lock", encoding="utf-8")

    wt_path = tmp_path / "staff-wt"
    wt_path.mkdir()
    wt_gitdir = main_repo / ".git" / "worktrees" / "staff-wt"
    wt_gitdir.mkdir(parents=True)
    (wt_path / ".git").write_text(f"gitdir: {wt_gitdir}\n", encoding="utf-8")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:4] == ["git", "worktree", "list", "--porcelain"]:
            return _cp(command, 0, f"worktree {main_repo}\nworktree {wt_path}\n")
        raise AssertionError(f"unexpected command: {command}")

    removed = runner_ops.clean_stale_git_locks(
        wt_path,
        run=fake_run,
        main_root=main_repo,
        is_git_process_active=lambda p: p == wt_path,
    )

    assert module_lock in removed
    assert not module_lock.exists()


@pytest.mark.unit
def test_clean_stale_git_locks_clears_per_worktree_modules_index_lock_when_main_git_busy(
    tmp_path: Path,
) -> None:
    main_repo = tmp_path / "UpstreamDrift"
    wt_name = "UpstreamDrift-run-7afeaa164e94"
    wt_gitdir = main_repo / ".git" / "worktrees" / wt_name
    wt_modules = wt_gitdir / "modules" / "vendor" / "ud-tools"
    wt_modules.mkdir(parents=True)
    stale_lock = wt_modules / "index.lock"
    stale_lock.write_text("stale", encoding="utf-8")

    wt_path = tmp_path / "staff-wt"
    wt_path.mkdir()
    (wt_path / ".git").write_text(f"gitdir: {wt_gitdir}\n", encoding="utf-8")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:4] == ["git", "worktree", "list", "--porcelain"]:
            return _cp(command, 0, f"worktree {main_repo}\nworktree {wt_path}\n")
        raise AssertionError(f"unexpected command: {command}")

    removed = runner_ops.clean_stale_git_locks(
        wt_path,
        run=fake_run,
        main_root=main_repo,
        is_git_process_active=lambda p: p == main_repo,
    )

    assert stale_lock in removed
    assert not stale_lock.exists()


@pytest.mark.unit
def test_bootstrap_submodules_retries_after_per_worktree_index_lock(tmp_path: Path) -> None:
    from staff import runner_ops_worktree_bootstrap as boot

    worktree = tmp_path / "wt"
    worktree.mkdir()
    main_repo = tmp_path / "UpstreamDrift"
    wt_gitdir = main_repo / ".git" / "worktrees" / "wt"
    wt_gitdir.mkdir(parents=True)
    wt_modules = wt_gitdir / "modules" / "vendor" / "ud-tools"
    (worktree / ".git").write_text(f"gitdir: {wt_gitdir}\n", encoding="utf-8")
    stale_lock = wt_modules / "index.lock"

    submodule_calls = 0

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal submodule_calls
        if command[:2] == ["git", "--version"]:
            return _cp(command, 0, "git version 2.47.1\n")
        if command[:4] == ["git", "worktree", "list", "--porcelain"]:
            return _cp(command, 0, f"worktree {main_repo}\nworktree {worktree}\n")
        if "submodule" in command and "update" in command:
            submodule_calls += 1
            if submodule_calls == 1:
                wt_modules.mkdir(parents=True, exist_ok=True)
                stale_lock.write_text("leftover", encoding="utf-8")
                return _cp(
                    command,
                    1,
                    stderr=(
                        "fatal: Unable to create "
                        f"'{stale_lock}': File exists.\n"
                        "fatal: Unable to checkout '0cf7f77a' in submodule path 'vendor/ud-tools'\n"
                    ),
                )
            return _cp(command, 0)
        raise AssertionError(f"unexpected command: {command}")

    failure = boot._bootstrap_submodules(worktree, fake_run, is_git_process_active=lambda _p: False)

    assert failure is None
    assert submodule_calls == 2
    assert not stale_lock.exists()


@pytest.mark.unit
def test_resolve_pre_pr_entrypoint_falls_back_when_module_import_missing(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "__init__.py").write_text("", encoding="utf-8")
    (scripts / "pre_pr.py").write_text(_STUB_PRE_PR, encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:4] == ["python3.11", "-m", "scripts.pre_pr", "--help"]:
            return _cp(command, 1, stderr="No module named scripts.pre_pr\n")
        if command[:4] == ["python3.11", "scripts/pre_pr.py", "--help"]:
            return _cp(command, 0, "usage\n")
        raise AssertionError(f"unexpected command: {command}")

    resolved = runner_ops.resolve_repo_pre_pr_entrypoint(
        tmp_path, python_executable="python3.11", run=fake_run
    )

    assert resolved == "python3.11 scripts/pre_pr.py"
    assert any(cmd[:3] == ["python3.11", "-m", "scripts.pre_pr"] for cmd in calls)


@pytest.mark.unit
def test_bootstrap_preflight_pre_pr_missing_is_non_retryable_environment_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "staff.runner_ops_worktree_bootstrap.role_requires_pre_pr_entrypoint",
        lambda _role: True,
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["python3.11", "-c", runner_ops._PYTHON_VERSION_SNIPPET]:
            return _cp(command, 0, "3.11\n")
        if command[:3] == ["python3.11", "-m", "pytest"] and "--version" in command:
            return _cp(command, 0, "pytest 8.4.0")
        raise AssertionError(f"unexpected command: {command}")

    result = runner_ops.run_worktree_bootstrap_preflight(
        tmp_path,
        role="issue-remediator",
        run=fake_run,
        which=lambda name: "python3.11" if name == "python3.11" else None,
    )

    assert result.ok is False
    assert result.failure_class == "environment_error"
    assert result.retryable is False
    assert "shared_scripts" in result.remediation


@pytest.mark.unit
def test_bootstrap_preflight_optional_pre_pr_for_non_open_pr_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "staff.runner_ops_worktree_bootstrap.role_requires_pre_pr_entrypoint",
        lambda role: role == "issue-remediator",
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["python3.11", "-c", runner_ops._PYTHON_VERSION_SNIPPET]:
            return _cp(command, 0, "3.11\n")
        if command[:3] == ["python3.11", "-m", "pytest"] and "--version" in command:
            return _cp(command, 0, "pytest 8.4.0")
        raise AssertionError(f"unexpected command: {command}")

    result = runner_ops.run_worktree_bootstrap_preflight(
        tmp_path,
        role="code-reviewer",
        run=fake_run,
        which=lambda name: "python3.11" if name == "python3.11" else None,
    )

    assert result.ok is True
    assert result.pre_pr_entrypoint == ""
    assert "optional" in result.pre_pr_advisory
    assert "code-reviewer" in result.pre_pr_advisory


@pytest.mark.unit
def test_role_requires_pre_pr_entrypoint_honors_open_pr_and_code_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from staff.role_spec import RoleSpec

    roster = {
        "code-reviewer": RoleSpec(name="code-reviewer", title="CR", permissions={"open_pr": False, "lease": False}),
        "issue-remediator": RoleSpec(
            name="issue-remediator", title="IR", permissions={"open_pr": True, "push_branch": True}
        ),
        "research-scout": RoleSpec(
            name="research-scout", title="RS", permissions={"open_pr": False, "code_read_only": True}
        ),
    }
    monkeypatch.setattr("staff.roles.load_roles", lambda: roster)

    from staff.runner_ops_worktree_bootstrap import role_requires_pre_pr_entrypoint

    assert role_requires_pre_pr_entrypoint("code-reviewer") is False
    assert role_requires_pre_pr_entrypoint("issue-remediator") is True
    assert role_requires_pre_pr_entrypoint("research-scout") is False
    assert role_requires_pre_pr_entrypoint("unknown-role") is True


@pytest.mark.unit
def test_clean_stale_git_locks_removes_aged_main_module_lock_when_git_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from staff import runner_ops_worktree_bootstrap as boot

    monkeypatch.setattr(boot, "STALE_GIT_LOCK_MAX_AGE_S", 60.0)

    main_repo = tmp_path / "UpstreamDrift"
    main_modules = main_repo / ".git" / "modules" / "vendor" / "ud-tools"
    main_modules.mkdir(parents=True)
    module_lock = main_modules / "index.lock"
    module_lock.write_text("lock", encoding="utf-8")
    old_mtime = time.time() - 120
    os.utime(module_lock, (old_mtime, old_mtime))

    wt_path = tmp_path / "staff-wt"
    wt_path.mkdir()
    wt_gitdir = main_repo / ".git" / "worktrees" / "staff-wt"
    wt_gitdir.mkdir(parents=True)
    (wt_path / ".git").write_text(f"gitdir: {wt_gitdir}\n", encoding="utf-8")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:4] == ["git", "worktree", "list", "--porcelain"]:
            return _cp(command, 0, f"worktree {main_repo}\nworktree {wt_path}\n")
        raise AssertionError(f"unexpected command: {command}")

    removed = runner_ops.clean_stale_git_locks(
        wt_path,
        run=fake_run,
        main_root=main_repo,
        is_git_process_active=lambda _p: True,
    )

    assert module_lock in removed
    assert not module_lock.exists()


@pytest.mark.unit
def test_clean_stale_git_locks_preserves_fresh_main_module_lock_when_git_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from staff import runner_ops_worktree_bootstrap as boot

    monkeypatch.setattr(boot, "STALE_GIT_LOCK_MAX_AGE_S", 300.0)

    main_repo = tmp_path / "UpstreamDrift"
    main_modules = main_repo / ".git" / "modules" / "vendor" / "ud-tools"
    main_modules.mkdir(parents=True)
    module_lock = main_modules / "index.lock"
    module_lock.write_text("lock", encoding="utf-8")

    wt_path = tmp_path / "staff-wt"
    wt_path.mkdir()
    wt_gitdir = main_repo / ".git" / "worktrees" / "staff-wt"
    wt_gitdir.mkdir(parents=True)
    (wt_path / ".git").write_text(f"gitdir: {wt_gitdir}\n", encoding="utf-8")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:4] == ["git", "worktree", "list", "--porcelain"]:
            return _cp(command, 0, f"worktree {main_repo}\nworktree {wt_path}\n")
        raise AssertionError(f"unexpected command: {command}")

    removed = runner_ops.clean_stale_git_locks(
        wt_path,
        run=fake_run,
        main_root=main_repo,
        is_git_process_active=lambda _p: True,
    )

    assert removed == []
    assert module_lock.exists()


@pytest.mark.unit
def test_bootstrap_submodules_exhausted_lock_error_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from staff import runner_ops_worktree_bootstrap as boot

    monkeypatch.setattr(boot, "_SUBMODULE_RETRY_BACKOFF_S", (0.0, 0.0))

    worktree = tmp_path / "wt"
    worktree.mkdir()
    main_repo = tmp_path / "UpstreamDrift"
    wt_gitdir = main_repo / ".git" / "worktrees" / "wt"
    wt_gitdir.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {wt_gitdir}\n", encoding="utf-8")
    stale_lock = wt_gitdir / "modules" / "vendor" / "ud-tools" / "index.lock"

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["git", "--version"]:
            return _cp(command, 0, "git version 2.47.1\n")
        if command[:4] == ["git", "worktree", "list", "--porcelain"]:
            return _cp(command, 0, f"worktree {main_repo}\nworktree {worktree}\n")
        if "submodule" in command and "update" in command:
            stale_lock.parent.mkdir(parents=True, exist_ok=True)
            stale_lock.write_text("leftover", encoding="utf-8")
            return _cp(
                command,
                1,
                stderr=f"fatal: Unable to create '{stale_lock}': File exists.\n",
            )
        raise AssertionError(f"unexpected command: {command}")

    failure = boot._bootstrap_submodules(worktree, fake_run, is_git_process_active=lambda _p: False)

    assert failure is not None
    assert failure.retryable is True
    assert failure.failure_class == "submodule_lock"
    assert "submodule init failed" in failure.attention


@pytest.mark.unit
def test_submodule_clone_lock_git_dir_resolves_main_checkout(tmp_path: Path) -> None:
    from staff import runner_ops_worktree_bootstrap as boot

    main_repo = tmp_path / "UpstreamDrift"
    (main_repo / ".git").mkdir(parents=True)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    wt_gitdir = main_repo / ".git" / "worktrees" / "wt"
    wt_gitdir.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {wt_gitdir}\n", encoding="utf-8")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:4] == ["git", "worktree", "list", "--porcelain"]:
            return _cp(command, 0, f"worktree {main_repo}\nworktree {worktree}\n")
        raise AssertionError(f"unexpected command: {command}")

    git_dir = boot._submodule_clone_lock_git_dir(worktree, fake_run)

    assert git_dir == main_repo / ".git"


@pytest.mark.unit
def test_bootstrap_submodules_holds_clone_file_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from staff import runner_ops_worktree_bootstrap as boot

    monkeypatch.setattr(boot, "_SUBMODULE_RETRY_BACKOFF_S", (0.0, 0.0))

    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".gitmodules").write_text('[submodule "x"]\n\tpath = vendor/x\n', encoding="utf-8")
    git_dir = worktree / ".git"
    git_dir.mkdir()
    held: dict[str, bool] = {"locked": False, "flock_calls": 0}

    class FakeFcntl:
        LOCK_EX = 2
        LOCK_UN = 8

        @staticmethod
        def flock(fd: int, flag: int) -> None:
            held["flock_calls"] += 1
            if flag == FakeFcntl.LOCK_EX:
                held["locked"] = True
            elif flag == FakeFcntl.LOCK_UN:
                held["locked"] = False

    monkeypatch.setitem(sys.modules, "fcntl", FakeFcntl)
    monkeypatch.setattr(boot, "_SUBMODULE_ATTEMPTS", 1)

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["git", "--version"]:
            return _cp(command, 0, "git version 2.47.1\n")
        if command[:3] == ["git", "submodule", "status"]:
            return _cp(command, 0, "-abc vendor/x\n")
        if command[:2] == ["git", "worktree"]:
            return _cp(command, 0, f"worktree {worktree}\n")
        if "submodule" in command and "update" in command:
            assert held["locked"], "submodule update must run under clone lock"
            return _cp(command, 0)
        raise AssertionError(f"unexpected command: {command}")

    boot._bootstrap_submodules(worktree, fake_run, is_git_process_active=lambda _p: False)

    assert held["flock_calls"] >= 2
    assert held["locked"] is False
