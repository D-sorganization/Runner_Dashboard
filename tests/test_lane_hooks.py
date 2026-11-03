"""Tests for the lane hook runner and profile schema (#3933 S2)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from scripts import lane_hooks


def _write_profile(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _profile_data(**hooks: list[dict]) -> dict:
    return {"name": "claude-k", "hooks": hooks}


def test_load_minimal_profile(tmp_path: Path) -> None:
    path = _write_profile(tmp_path, _profile_data())
    profile = lane_hooks.LaneProfile.load(path)
    assert profile.name == "claude-k"
    for point in lane_hooks.HOOK_POINTS:
        assert profile.hooks[point] == ()


def test_profile_requires_name(tmp_path: Path) -> None:
    path = _write_profile(tmp_path, {"hooks": {}})
    with pytest.raises(ValueError, match="name"):
        lane_hooks.LaneProfile.load(path)


def test_profile_rejects_unknown_hook_point(tmp_path: Path) -> None:
    path = _write_profile(tmp_path, _profile_data(on_launch=[]))
    with pytest.raises(ValueError, match="unknown hook point"):
        lane_hooks.LaneProfile.load(path)


def test_hook_entry_requires_id_and_callable(tmp_path: Path) -> None:
    path = _write_profile(tmp_path, _profile_data(on_start=[{"callable": "x:y"}]))
    with pytest.raises(ValueError, match="id"):
        lane_hooks.LaneProfile.load(path)

    path = _write_profile(tmp_path, _profile_data(on_start=[{"id": "h1"}]))
    with pytest.raises(ValueError, match="callable"):
        lane_hooks.LaneProfile.load(path)


def test_runner_runs_hooks_in_declared_order(tmp_path: Path) -> None:
    data = _profile_data(
        pre_tool=[
            {"id": "first", "callable": "test_lane_hooks:_record_first"},
            {"id": "second", "callable": "test_lane_hooks:_record_second"},
        ]
    )
    path = _write_profile(tmp_path, data)
    profile = lane_hooks.LaneProfile.load(path)
    runner = lane_hooks.HookRunner(profile)

    _CALL_LOG.clear()
    results = runner.run("pre_tool", {"tool": "git push"})

    assert _CALL_LOG == ["first", "second"]
    assert [r.ok for r in results] == [True, True]
    assert [r.hook_id for r in results] == ["first", "second"]


_CALL_LOG: list[str] = []


def _record_first(context: dict) -> None:
    _CALL_LOG.append("first")


def _record_second(context: dict) -> None:
    _CALL_LOG.append("second")


def _always_fail(context: dict) -> None:
    raise RuntimeError("no --no-verify allowed")


def test_blocking_hook_failure_raises_and_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    data = _profile_data(pre_tool=[{"id": "guard", "callable": "test_lane_hooks:_always_fail"}])
    path = _write_profile(tmp_path, data)
    profile = lane_hooks.LaneProfile.load(path)
    runner = lane_hooks.HookRunner(profile)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(lane_hooks.HookBlockedError) as excinfo:
            runner.run("pre_tool", {"tool": "git push --force"})

    assert excinfo.value.point == "pre_tool"
    assert excinfo.value.hook_id == "guard"
    assert any("guard" in record.message for record in caplog.records)


def test_non_blocking_hook_failure_is_recorded_not_raised(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    data = _profile_data(
        post_tool=[
            {
                "id": "advisory",
                "callable": "test_lane_hooks:_always_fail",
                "blocking": False,
            }
        ]
    )
    path = _write_profile(tmp_path, data)
    profile = lane_hooks.LaneProfile.load(path)
    runner = lane_hooks.HookRunner(profile)

    with caplog.at_level(logging.ERROR):
        results = runner.run("post_tool", {"tool": "git commit"})

    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].hook_id == "advisory"
    assert any("advisory" in record.message for record in caplog.records)


def test_runner_rejects_unknown_point() -> None:
    profile = lane_hooks.LaneProfile.from_dict(_profile_data())
    runner = lane_hooks.HookRunner(profile)
    with pytest.raises(ValueError, match="unknown hook point"):
        runner.run("on_launch", {})


def test_unresolvable_callable_blocks_by_default(tmp_path: Path) -> None:
    data = _profile_data(on_start=[{"id": "missing", "callable": "scripts.lane_hooks:does_not_exist"}])
    path = _write_profile(tmp_path, data)
    profile = lane_hooks.LaneProfile.load(path)
    runner = lane_hooks.HookRunner(profile)
    with pytest.raises(lane_hooks.HookBlockedError):
        runner.run("on_start", {})
