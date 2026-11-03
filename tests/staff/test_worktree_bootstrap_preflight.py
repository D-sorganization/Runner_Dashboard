from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from staff import runner_ops
from staff.store import RunRecord, RunStore


def _cp(argv: list[str], code: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr=stderr)


@pytest.mark.unit
def test_bootstrap_preflight_initializes_submodules_installs_pytest_and_resolves_repo_entrypoint(
    tmp_path: Path,
) -> None:
    (tmp_path / ".gitmodules").write_text(
        '[submodule "vendor/ud-tools"]\n\tpath = vendor/ud-tools\n',
        encoding="utf-8",
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify_merge_gates.py").write_text("# marker\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
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
    (tmp_path / "scripts" / "verify_merge_gates.py").write_text("# marker\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
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
    (worktree / "scripts" / "verify_merge_gates.py").write_text("# marker\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
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
    assert f"--reference-if-able={main_checkout}" in submodule_calls[0]


@pytest.mark.unit
def test_bootstrap_preflight_catches_submodule_timeout_and_retries_once(tmp_path: Path) -> None:
    (tmp_path / ".gitmodules").write_text(
        '[submodule "vendor/ud-tools"]\n\tpath = vendor/ud-tools\n',
        encoding="utf-8",
    )

    attempts = {"count": 0}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["git", "submodule", "status"]:
            return _cp(command, 0, "-abc123 vendor/ud-tools\n")
        if command[:2] == ["git", "worktree"]:
            return _cp(command, 0, f"worktree {tmp_path}\n")
        if "submodule" in command and "update" in command:
            attempts["count"] += 1
            raise subprocess.TimeoutExpired(command, timeout=kwargs.get("timeout", 300))
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
    (tmp_path / "scripts" / "verify_merge_gates.py").write_text("# marker\n", encoding="utf-8")

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
