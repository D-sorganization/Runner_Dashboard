"""Unit tests for Claude CLI project folder management and cleanup (#1683)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from staff.cli_projects import (
    PANEL_SCRATCH_PREFIX,
    _is_panel_project,
    encode_project_name,
    panel_project_prefix,
    projects_root,
    remove_turn_project,
    sweep_stale_panel_projects,
)


@pytest.mark.unit
def test_encode_project_name_replaces_non_alphanumeric_characters() -> None:
    """encode_project_name replaces any character outside [A-Za-z0-9] with '-'."""
    assert encode_project_name("/tmp/staff_panel_ab12") == "-tmp-staff-panel-ab12"
    assert encode_project_name("/home/dieterolson/staff-worktrees/run-1") == "-home-dieterolson-staff-worktrees-run-1"


@pytest.mark.unit
def test_encode_project_name_requires_absolute_path() -> None:
    """encode_project_name raises AssertionError if cwd is relative."""
    with pytest.raises(AssertionError):
        encode_project_name("relative/path/scratch")

    with pytest.raises(AssertionError):
        encode_project_name("")


@pytest.mark.unit
def test_projects_root_honours_claude_config_dir_and_falls_back_to_home() -> None:
    """projects_root uses CLAUDE_CONFIG_DIR when set and non-empty, else HOME/.claude/projects."""
    # CLAUDE_CONFIG_DIR set
    env = {"CLAUDE_CONFIG_DIR": "/custom/claude/dir"}
    assert projects_root(env) == Path("/custom/claude/dir/projects")

    # CLAUDE_CONFIG_DIR empty string falls back to HOME
    env = {"CLAUDE_CONFIG_DIR": "", "HOME": "/custom/home"}
    assert projects_root(env) == Path("/custom/home/.claude/projects")

    # CLAUDE_CONFIG_DIR unset, HOME set
    env = {"HOME": "/custom/home"}
    assert projects_root(env) == Path("/custom/home/.claude/projects")

    # Both unset falls back to Path.home()
    env = {}
    assert projects_root(env) == Path.home() / ".claude" / "projects"


@pytest.mark.unit
def test_panel_project_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """panel_project_prefix encodes os.path.join(tmpdir, PANEL_SCRATCH_PREFIX)."""
    assert PANEL_SCRATCH_PREFIX == "staff_panel_"
    assert panel_project_prefix("/tmp") == "-tmp-staff-panel-"

    monkeypatch.setattr("tempfile.gettempdir", lambda: "/tmp")
    assert panel_project_prefix() == "-tmp-staff-panel-"


@pytest.mark.unit
def test_remove_turn_project_removes_turn_folder_and_keeps_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """remove_turn_project removes the turn project folder and keeps siblings."""
    monkeypatch.setattr("tempfile.gettempdir", lambda: "/tmp")

    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path)}

    turn_cwd = "/tmp/staff_panel_turn_123"
    turn_folder = projects_dir / encode_project_name(turn_cwd)
    turn_folder.mkdir()
    (turn_folder / "session.json").write_text("{}")

    sibling_folder = projects_dir / "-tmp-staff-chat-th-1"
    sibling_folder.mkdir()
    (sibling_folder / "session.json").write_text("{}")

    removed = remove_turn_project(turn_cwd, env)
    assert removed is True
    assert not turn_folder.exists()
    assert sibling_folder.exists()


@pytest.mark.unit
def test_remove_turn_project_returns_false_for_non_panel_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """remove_turn_project returns False and removes nothing when cwd lacks panel prefix."""
    monkeypatch.setattr("tempfile.gettempdir", lambda: "/tmp")

    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path)}

    non_panel_cwd = "/tmp/staff_chat_th_1"
    folder = projects_dir / encode_project_name(non_panel_cwd)
    folder.mkdir()

    removed = remove_turn_project(non_panel_cwd, env)
    assert removed is False
    assert folder.exists()


@pytest.mark.unit
def test_remove_turn_project_does_not_follow_symlink_outside_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symlink named with panel prefix pointing outside projects root is not followed."""
    monkeypatch.setattr("tempfile.gettempdir", lambda: "/tmp")

    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path)}

    outside_dir = tmp_path / "outside_directory"
    outside_dir.mkdir(parents=True, exist_ok=True)
    canary = outside_dir / "target_file.txt"
    canary.write_text("keep me safe")

    turn_cwd = "/tmp/staff_panel_symlink_test"
    symlink_path = projects_dir / encode_project_name(turn_cwd)

    try:
        symlink_path.symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks not supported in this environment")

    removed = remove_turn_project(turn_cwd, env)
    assert removed is False
    assert outside_dir.exists()
    assert canary.exists()


@pytest.mark.unit
def test_sweep_stale_panel_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """sweep removes only prefixed folders older than max_age and preserves fresh / non-prefixed."""
    monkeypatch.setattr("tempfile.gettempdir", lambda: "/tmp")

    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path)}

    old_panel = projects_dir / "-tmp-staff-panel-old1"
    old_panel.mkdir()
    (old_panel / "file.json").write_text("{}")

    fresh_panel = projects_dir / "-tmp-staff-panel-fresh2"
    fresh_panel.mkdir()
    (fresh_panel / "file.json").write_text("{}")

    old_other = projects_dir / "-tmp-staff-chat-th-1"
    old_other.mkdir()
    (old_other / "file.json").write_text("{}")

    now = 1_000_000.0
    max_age = 3600.0  # 1 hour
    old_time = now - 7200.0  # 2 hours old
    fresh_time = now - 600.0  # 10 minutes old

    os.utime(old_panel, (old_time, old_time))
    os.utime(fresh_panel, (fresh_time, fresh_time))
    os.utime(old_other, (old_time, old_time))

    removed = sweep_stale_panel_projects(env, max_age_seconds=max_age, now=now)
    assert removed == ["-tmp-staff-panel-old1"]

    assert not old_panel.exists()
    assert fresh_panel.exists()
    assert old_other.exists()


@pytest.mark.unit
def test_sweep_stale_panel_projects_missing_root(tmp_path: Path) -> None:
    """sweep on a missing root directory returns []."""
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "nonexistent")}
    removed = sweep_stale_panel_projects(env)
    assert removed == []


@pytest.mark.unit
def test_sweep_stale_panel_projects_requires_positive_max_age() -> None:
    """sweep raises AssertionError when max_age_seconds <= 0."""
    with pytest.raises(AssertionError):
        sweep_stale_panel_projects({}, max_age_seconds=0)
    with pytest.raises(AssertionError):
        sweep_stale_panel_projects({}, max_age_seconds=-100)


@pytest.mark.unit
def test_is_panel_project_guards(tmp_path: Path) -> None:
    """_is_panel_project correctly rejects files, exact prefix matches, and subdirs."""
    root = tmp_path / "projects"
    root.mkdir()
    prefix = "-tmp-staff-panel-"

    # Exact prefix length (no suffix)
    exact_prefix = root / prefix
    exact_prefix.mkdir()
    assert _is_panel_project(exact_prefix, root, prefix) is False

    # File instead of directory
    file_path = root / f"{prefix}somefile"
    file_path.write_text("data")
    assert _is_panel_project(file_path, root, prefix) is False

    # Subdirectory not directly under root
    nested_dir = root / "sub" / f"{prefix}nested"
    nested_dir.mkdir(parents=True)
    assert _is_panel_project(nested_dir, root, prefix) is False
