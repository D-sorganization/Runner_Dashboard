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
    pytest_checks = {"count": 0}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:3] == ["python3.11", "-c", runner_ops._PYTHON_VERSION_SNIPPET]:
            return _cp(command, 0, "3.11\n")
        if command[:3] == ["python3.11", "-m", "pytest"] and "--version" in command:
            pytest_checks["count"] += 1
            return _cp(command, 1, stderr="No module named pytest") if pytest_checks["count"] == 1 else _cp(command, 0)
        if command[:3] == ["python3.11", "-m", "pip"] and "install" in command:
            return _cp(command, 0, "installed")
        if command[:4] == ["git", "submodule", "update", "--init"]:
            return _cp(command, 0)
        raise AssertionError(f"unexpected command: {command}")

    result = runner_ops.run_worktree_bootstrap_preflight(
        tmp_path,
        run=fake_run,
        which=lambda name: "python3.11" if name == "python3.11" else None,
    )

    assert result.ok is True
    assert result.python_executable == "python3.11"
    assert result.pre_pr_entrypoint == "python3.11 scripts/verify_merge_gates.py --repository-root ."
    assert any(cmd[:4] == ["git", "submodule", "update", "--init"] for cmd in calls)
    assert any(cmd[:4] == ["python3.11", "-m", "pip", "install"] for cmd in calls)


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
