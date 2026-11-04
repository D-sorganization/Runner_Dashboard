"""Tests for the default fleet-policy hooks (#3933 S3)."""

from __future__ import annotations

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
        if point != "pre_tool":
            assert hooks.get(point, ()) == ()


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
    assert [spec.id for spec in merged.hooks["on_start"]] == ["greet"]
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
