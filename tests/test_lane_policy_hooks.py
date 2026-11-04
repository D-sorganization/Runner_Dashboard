"""Tests for the default fleet-policy hooks (#3933 S3, #3963 S2)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import lane_hooks, lane_policy_hooks


def test_no_verify_guard_blocks_no_verify_flag() -> None:
    with pytest.raises(lane_policy_hooks.PolicyViolation, match="no-verify"):
        lane_policy_hooks.no_verify_guard({"command": "git commit --no-verify -m x"})


def test_no_verify_guard_blocks_skip_env() -> None:
    with pytest.raises(lane_policy_hooks.PolicyViolation, match="SKIP="):
        lane_policy_hooks.no_verify_guard({"command": "SKIP=ruff git commit -m x"})


def test_no_verify_guard_allows_clean_command() -> None:
    lane_policy_hooks.no_verify_guard({"command": "git commit -m 'clean commit'"})


def test_no_verify_guard_ignores_missing_command() -> None:
    lane_policy_hooks.no_verify_guard({})


def test_force_push_guard_blocks_long_force_flag() -> None:
    with pytest.raises(lane_policy_hooks.PolicyViolation, match="force-push"):
        lane_policy_hooks.no_force_push_guard({"command": "git push --force origin main"})


def test_force_push_guard_blocks_short_f_flag() -> None:
    with pytest.raises(lane_policy_hooks.PolicyViolation, match="force-push"):
        lane_policy_hooks.no_force_push_guard({"command": "git push -f origin my-branch"})


def test_force_push_guard_allows_force_with_lease() -> None:
    lane_policy_hooks.no_force_push_guard({"command": "git push --force-with-lease origin my-branch"})


def test_force_push_guard_allows_plain_push() -> None:
    lane_policy_hooks.no_force_push_guard({"command": "git push origin my-branch"})


def test_force_push_guard_ignores_non_push_command() -> None:
    lane_policy_hooks.no_force_push_guard({"command": "git status --force"})


def test_diff_size_guard_blocks_over_limit() -> None:
    with pytest.raises(lane_policy_hooks.PolicyViolation, match="500"):
        lane_policy_hooks.diff_size_guard({"changed_lines": 501})


def test_diff_size_guard_allows_exactly_limit() -> None:
    lane_policy_hooks.diff_size_guard({"changed_lines": 500})


def test_diff_size_guard_ignores_missing_count() -> None:
    lane_policy_hooks.diff_size_guard({})


def test_default_policy_hooks_cover_pre_tool_point() -> None:
    hooks = lane_policy_hooks.default_policy_hooks()
    ids = {spec.id for spec in hooks["pre_tool"]}
    assert ids == {"no-verify-guard", "no-force-push-guard", "diff-size-guard"}
    for point in lane_hooks.HOOK_POINTS:
        if point not in ("pre_tool", "on_start"):
            assert hooks.get(point, ()) == ()


def test_default_policy_hooks_cover_on_start_point() -> None:
    # #3963 S2: lane bootstrap installs the real pre-push gate at on_start.
    hooks = lane_policy_hooks.default_policy_hooks()
    ids = {spec.id for spec in hooks["on_start"]}
    assert ids == {"install-pre-push-hook"}


def test_default_policy_hooks_are_blocking() -> None:
    hooks = lane_policy_hooks.default_policy_hooks()
    assert all(spec.blocking for spec in hooks["pre_tool"])


def test_apply_default_policy_hooks_prepends_without_losing_existing() -> None:
    profile = lane_hooks.LaneProfile.from_dict(
        {
            "name": "claude-k",
            "hooks": {
                "pre_tool": [{"id": "custom", "callable": "scripts.lane_policy_hooks:no_verify_guard"}],
                "on_start": [{"id": "greet", "callable": "scripts.lane_policy_hooks:no_verify_guard"}],
            },
        }
    )
    merged = lane_policy_hooks.apply_default_policy_hooks(profile)
    pre_tool_ids = [spec.id for spec in merged.hooks["pre_tool"]]
    assert pre_tool_ids == ["no-verify-guard", "no-force-push-guard", "diff-size-guard", "custom"]
    assert [spec.id for spec in merged.hooks["on_start"]] == ["install-pre-push-hook", "greet"]
    assert merged.name == "claude-k"


def test_runner_blocks_no_verify_through_default_hooks() -> None:
    profile = lane_hooks.LaneProfile.from_dict({"name": "claude-k", "hooks": {}})
    runner = lane_hooks.HookRunner(lane_policy_hooks.apply_default_policy_hooks(profile))
    with pytest.raises(lane_hooks.HookBlockedError):
        runner.run("pre_tool", {"command": "git commit --no-verify -m x"})


def test_runner_allows_clean_command_through_default_hooks() -> None:
    profile = lane_hooks.LaneProfile.from_dict({"name": "claude-k", "hooks": {}})
    runner = lane_hooks.HookRunner(lane_policy_hooks.apply_default_policy_hooks(profile))
    results = runner.run("pre_tool", {"command": "git commit -m x", "changed_lines": 10})
    assert all(result.ok for result in results)


# --- #3963 S2: diff_size_guard wired to pr_size_gate's real counter + allowlist ---


def _numstat_runner(stdout: str) -> object:
    def run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    return run


def test_diff_size_guard_uses_pr_size_gate_counter_when_repo_root_given(tmp_path: Path) -> None:
    context = {
        "repo_root": tmp_path,
        "run": _numstat_runner("501\t0\tbackend/big_module.py\n"),
    }
    with pytest.raises(lane_policy_hooks.PolicyViolation, match="501"):
        lane_policy_hooks.diff_size_guard(context)
    # The real count is populated back into the context for observability.
    assert context["changed_lines"] == 501


def test_diff_size_guard_allows_within_limit_via_repo_root(tmp_path: Path) -> None:
    context = {
        "repo_root": tmp_path,
        "run": _numstat_runner("499\t0\tbackend/big_module.py\n"),
    }
    lane_policy_hooks.diff_size_guard(context)
    assert context["changed_lines"] == 499


def test_diff_size_guard_respects_pr_size_gate_allowlist(tmp_path: Path) -> None:
    allowlist_path = tmp_path / "config" / "pr_size_allowlist.json"
    allowlist_path.parent.mkdir(parents=True)
    allowlist_path.write_text('{"allowed_prs": [2541]}', encoding="utf-8")
    context = {
        "repo_root": tmp_path,
        "pr_number": 2541,
        "run": _numstat_runner("600\t0\tbackend/big_module.py\n"),
    }
    lane_policy_hooks.diff_size_guard(context)
    assert context["changed_lines"] == 600


def test_diff_size_guard_allowlist_does_not_cover_other_prs(tmp_path: Path) -> None:
    allowlist_path = tmp_path / "config" / "pr_size_allowlist.json"
    allowlist_path.parent.mkdir(parents=True)
    allowlist_path.write_text('{"allowed_prs": [2541]}', encoding="utf-8")
    context = {
        "repo_root": tmp_path,
        "pr_number": 9999,
        "run": _numstat_runner("600\t0\tbackend/big_module.py\n"),
    }
    with pytest.raises(lane_policy_hooks.PolicyViolation):
        lane_policy_hooks.diff_size_guard(context)


def test_diff_size_guard_falls_back_to_plain_changed_lines_without_repo_root() -> None:
    # Back-compat: hermetic callers that only set changed_lines still work.
    with pytest.raises(lane_policy_hooks.PolicyViolation, match="500"):
        lane_policy_hooks.diff_size_guard({"changed_lines": 501})


# --- #3963 S2: install_lane_pre_push_hook wires a real git pre-push hook ---


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def test_install_lane_pre_push_hook_writes_executable_hook(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    hook_path = lane_policy_hooks.install_lane_pre_push_hook(repo)

    assert hook_path == repo / ".git" / "hooks" / "pre-push"
    assert hook_path.is_file()
    content = hook_path.read_text(encoding="utf-8")
    assert "pr_size_gate.py" in content
    assert "--repo-root" in content
    assert hook_path.stat().st_mode & 0o111  # executable bits set

    hooks_path = subprocess.run(
        ["git", "config", "--worktree", "core.hooksPath"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert hooks_path == str(repo / ".git" / "hooks")


def test_install_pre_push_hook_guard_calls_installer_with_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(lane_policy_hooks, "install_lane_pre_push_hook", calls.append)

    lane_policy_hooks.install_pre_push_hook_guard({"worktree": tmp_path})

    assert calls == [tmp_path]


def test_install_pre_push_hook_guard_falls_back_to_repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(lane_policy_hooks, "install_lane_pre_push_hook", calls.append)

    lane_policy_hooks.install_pre_push_hook_guard({"repo_root": tmp_path})

    assert calls == [tmp_path]


def test_install_pre_push_hook_guard_noop_without_worktree_or_repo_root() -> None:
    # Should not raise; a lane without a concrete worktree just skips installing.
    lane_policy_hooks.install_pre_push_hook_guard({})
