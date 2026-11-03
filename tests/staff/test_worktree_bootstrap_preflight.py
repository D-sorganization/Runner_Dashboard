from __future__ import annotations

import subprocess
from collections.abc import Callable
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
            return _cp(command, 1, stderr="No module named pytest")
        if command[:3] == ["python3.11", "-m", "venv"]:
            return _cp(command, 0)
        if "-m" in command and "pip" in command and "install" in command:
            return _cp(command, 0, "installed")
        if "-m" in command and "pytest" in command and "--version" in command:
            return _cp(command, 0, "pytest 9.0.3\n")
        if command[:3] == ["git", "submodule", "status"]:
            return _cp(command, 0, "-deadbeef vendor/ud-tools\n")
        if command[:2] == ["git", "-c"] and "submodule" in command:
            return _cp(command, 0)
        if command[:3] == ["git", "config", "--get"]:
            return _cp(command, 0, "https://github.com/D-sorganization/Runner_Dashboard.git\n")
        if command[:3] == ["git", "clone", "--mirror"]:
            return _cp(command, 0)
        if command[:3] == ["git", "fetch", "--prune"]:
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
    assert any(cmd[:2] == ["git", "-c"] and "--depth" in cmd for cmd in calls)
    assert any(cmd[:3] == ["python3.11", "-m", "venv"] for cmd in calls)
    assert any("-m" in cmd and "pip" in cmd and "install" in cmd for cmd in calls)
    assert not any("--user" in cmd for cmd in calls)


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


def _make_submodule_preflight_run(
    tmp_path: Path,
    *,
    submodule_status: str = "-deadbeef vendor/ud-tools\n",
    on_submodule_update: Callable[[list[str], int], subprocess.CompletedProcess[str]] | None = None,
    origin_url: str = "https://github.com/D-sorganization/UpstreamDrift.git",
) -> tuple[list[list[str]], dict[str, int], Callable[..., subprocess.CompletedProcess[str]]]:
    calls: list[list[str]] = []
    update_attempts = {"count": 0}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:3] == ["python3.11", "-c", runner_ops._PYTHON_VERSION_SNIPPET]:
            return _cp(command, 0, "3.11\n")
        if command[:3] == ["python3.11", "-m", "pytest"] and "--version" in command:
            return _cp(command, 0, "pytest 8.4.0\n")
        if command[:3] == ["git", "submodule", "status"]:
            return _cp(command, 0, submodule_status)
        if command[:3] == ["git", "config", "--get"]:
            return _cp(command, 0, f"{origin_url}\n")
        if command[:2] == ["git", "-c"] and "submodule" in command:
            update_attempts["count"] += 1
            if on_submodule_update is not None:
                return on_submodule_update(command, update_attempts["count"])
            return _cp(command, 0)
        if command[:3] == ["git", "clone", "--mirror"]:
            return _cp(command, 0)
        if command[:3] == ["git", "fetch", "--prune"]:
            return _cp(command, 0)
        raise AssertionError(f"unexpected command: {command}")

    return calls, update_attempts, fake_run


@pytest.mark.unit
def test_bootstrap_preflight_skips_submodule_update_when_populated(tmp_path: Path) -> None:
    (tmp_path / ".gitmodules").write_text(
        '[submodule "vendor/ud-tools"]\n\tpath = vendor/ud-tools\n',
        encoding="utf-8",
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify_merge_gates.py").write_text("# marker\n", encoding="utf-8")
    calls, update_attempts, fake_run = _make_submodule_preflight_run(
        tmp_path,
        submodule_status=" deadbeef vendor/ud-tools (heads/main)\n",
    )

    result = runner_ops.run_worktree_bootstrap_preflight(
        tmp_path,
        run=fake_run,
        which=lambda name: "python3.11" if name == "python3.11" else None,
    )

    assert result.ok is True
    assert update_attempts["count"] == 0
    assert any(cmd[:3] == ["git", "submodule", "status"] for cmd in calls)
    assert not any(cmd[:2] == ["git", "-c"] and "submodule" in cmd for cmd in calls)


@pytest.mark.unit
def test_bootstrap_preflight_submodule_update_uses_shallow_flags_and_cache_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".gitmodules").write_text(
        '[submodule "vendor/ud-tools"]\n\tpath = vendor/ud-tools\n',
        encoding="utf-8",
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify_merge_gates.py").write_text("# marker\n", encoding="utf-8")
    cache_root = tmp_path / "cache"
    monkeypatch.setattr(
        runner_ops,
        "_submodule_cache_path",
        lambda _worktree, _run: cache_root / "UpstreamDrift.git",
    )
    cache_root.mkdir(parents=True)
    (cache_root / "UpstreamDrift.git").mkdir()
    calls, _update_attempts, fake_run = _make_submodule_preflight_run(tmp_path)

    result = runner_ops.run_worktree_bootstrap_preflight(
        tmp_path,
        run=fake_run,
        which=lambda name: "python3.11" if name == "python3.11" else None,
    )

    assert result.ok is True
    update_cmds = [cmd for cmd in calls if cmd[:2] == ["git", "-c"] and "submodule" in cmd]
    assert len(update_cmds) == 1
    cmd = update_cmds[0]
    assert cmd[1:3] == ["-c", "protocol.version=2"]
    assert "--depth" in cmd and "1" in cmd
    assert "--filter=blob:none" in cmd
    assert "--jobs" in cmd and "4" in cmd
    assert "--reference-if-able" in cmd
    assert str(cache_root / "UpstreamDrift.git") in cmd


@pytest.mark.unit
def test_bootstrap_preflight_submodule_update_falls_back_when_shallow_unavailable(tmp_path: Path) -> None:
    (tmp_path / ".gitmodules").write_text(
        '[submodule "vendor/ud-tools"]\n\tpath = vendor/ud-tools\n',
        encoding="utf-8",
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify_merge_gates.py").write_text("# marker\n", encoding="utf-8")

    def on_update(command: list[str], attempt: int) -> subprocess.CompletedProcess[str]:
        if "--depth" in command:
            return _cp(command, 1, stderr="fatal: remote error: does not allow request for unadvertised object")
        return _cp(command, 0)

    calls, _update_attempts, fake_run = _make_submodule_preflight_run(
        tmp_path,
        on_submodule_update=on_update,
    )

    result = runner_ops.run_worktree_bootstrap_preflight(
        tmp_path,
        run=fake_run,
        which=lambda name: "python3.11" if name == "python3.11" else None,
    )

    assert result.ok is True
    update_cmds = [cmd for cmd in calls if cmd[:2] == ["git", "-c"] and "submodule" in cmd]
    assert len(update_cmds) == 2
    assert "--depth" in update_cmds[0]
    assert "--depth" not in update_cmds[1]


@pytest.mark.unit
def test_bootstrap_preflight_submodule_timeout_is_retryable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".gitmodules").write_text(
        '[submodule "vendor/ud-tools"]\n\tpath = vendor/ud-tools\n',
        encoding="utf-8",
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify_merge_gates.py").write_text("# marker\n", encoding="utf-8")
    monkeypatch.setenv("STAFF_SUBMODULE_UPDATE_TIMEOUT", "42")

    def on_update(command: list[str], _attempt: int) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 42)

    _calls, _update_attempts, fake_run = _make_submodule_preflight_run(
        tmp_path,
        on_submodule_update=on_update,
    )

    result = runner_ops.run_worktree_bootstrap_preflight(
        tmp_path,
        run=fake_run,
        which=lambda name: "python3.11" if name == "python3.11" else None,
    )

    assert result.ok is False
    assert result.failure_class == "submodule_timeout"
    assert result.retryable is True
    assert result.remediation
    assert "timed out" in result.attention.lower()


@pytest.mark.unit
def test_ensure_worktree_bootstrap_ready_propagates_submodule_timeout(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")
    rec = RunRecord(
        id="run-submodule-timeout",
        role="rd-barb",
        provider="fake",
        model=None,
        machine="TestNode",
        repo="UpstreamDrift",
        target_kind="issue",
        target_ref="42",
        prompt="x",
        status="preparing",
    )
    store.create_run(rec)
    plan = runner_ops.RunPlan(
        role="rd-barb",
        provider="fake",
        model="default",
        repo="UpstreamDrift",
        target_kind="issue",
        target_ref="42",
        operator_prompt="x",
        prompt="x",
        argv=["fake"],
        branch="staff/rd-barb-42",
        lease_ritual=False,
    )

    original = runner_ops.run_worktree_bootstrap_preflight
    runner_ops.run_worktree_bootstrap_preflight = lambda _workdir, **_: runner_ops.WorktreeBootstrapPreflight(
        ok=False,
        attention="submodule update timed out after 42s (2 attempts)",
        failure_class="submodule_timeout",
        retryable=True,
        remediation="warm cache",
    )
    try:
        result = runner_ops.ensure_worktree_bootstrap_ready(store, rec, plan, tmp_path)
    finally:
        runner_ops.run_worktree_bootstrap_preflight = original

    assert result is None
    updated = store.get_run(rec.id)
    assert updated is not None
    assert updated.failure_class == "submodule_timeout"
    assert updated.retryable is True
    assert updated.remediation == "warm cache"
