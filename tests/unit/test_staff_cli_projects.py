"""Unit tests for Claude CLI project folder management and cleanup (#1683)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from staff.cli_projects import (
    CHAT_SCRATCH_PREFIX,
    DEFAULT_CHAT_IDLE_SECONDS,
    PANEL_SCRATCH_PREFIX,
    _is_panel_project,
    chat_project_prefix,
    encode_project_name,
    panel_project_prefix,
    projects_root,
    remove_turn_project,
    remove_worktree_project,
    run_project_prefix,
    sweep_idle_chat_scratch,
    sweep_orphan_run_projects,
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


@pytest.mark.unit
def test_run_project_prefix(tmp_path: Path) -> None:
    """run_project_prefix encodes worktrees_root path with trailing '-'."""
    wt_root = tmp_path / "staff-worktrees"
    expected = encode_project_name(str(wt_root)) + "-"
    assert run_project_prefix(wt_root) == expected


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX root path test on non-Windows")
def test_run_project_prefix_posix() -> None:
    """run_project_prefix on POSIX path returns expected dash-separated string."""
    assert run_project_prefix(Path("/home/u/staff-worktrees")) == "-home-u-staff-worktrees-"


@pytest.mark.unit
def test_remove_worktree_project_removes_folder_and_keeps_siblings(tmp_path: Path) -> None:
    """remove_worktree_project removes the matching folder and keeps siblings."""
    cfg_dir = tmp_path / "cfg"
    projects_dir = cfg_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    env = {"CLAUDE_CONFIG_DIR": str(cfg_dir)}

    wt_root = tmp_path / "staff-worktrees"
    wt_root.mkdir(parents=True, exist_ok=True)

    wt1 = wt_root / "run-ab12"
    wt1_folder = projects_dir / encode_project_name(str(wt1))
    wt1_folder.mkdir()
    (wt1_folder / "session.json").write_text("{}")

    wt2 = wt_root / "run-cd34"
    wt2_folder = projects_dir / encode_project_name(str(wt2))
    wt2_folder.mkdir()
    (wt2_folder / "session.json").write_text("{}")

    panel_folder = projects_dir / "-tmp-staff-panel-xyz"
    panel_folder.mkdir()
    (panel_folder / "session.json").write_text("{}")

    removed = remove_worktree_project(wt1, wt_root, env)
    assert removed is True
    assert not wt1_folder.exists()
    assert wt2_folder.exists()
    assert panel_folder.exists()


@pytest.mark.unit
def test_remove_worktree_project_refuses_non_direct_child(tmp_path: Path) -> None:
    """remove_worktree_project raises AssertionError if worktree is not a direct child of worktrees_root."""
    cfg_dir = tmp_path / "cfg"
    env = {"CLAUDE_CONFIG_DIR": str(cfg_dir)}

    wt_root = tmp_path / "staff-worktrees"
    wt_root.mkdir(parents=True, exist_ok=True)

    nested = wt_root / "nested" / "run-1"
    with pytest.raises(AssertionError):
        remove_worktree_project(nested, wt_root, env)

    outside = tmp_path / "other" / "run-1"
    with pytest.raises(AssertionError):
        remove_worktree_project(outside, wt_root, env)


@pytest.mark.unit
def test_remove_worktree_project_does_not_follow_symlink(tmp_path: Path) -> None:
    """remove_worktree_project does not follow a symlink pointing outside projects root."""
    cfg_dir = tmp_path / "cfg"
    projects_dir = cfg_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    env = {"CLAUDE_CONFIG_DIR": str(cfg_dir)}

    wt_root = tmp_path / "staff-worktrees"
    wt_root.mkdir(parents=True, exist_ok=True)

    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir(parents=True, exist_ok=True)
    canary = outside_dir / "target_file.txt"
    canary.write_text("keep me safe")

    wt = wt_root / "run-symlink"
    symlink_path = projects_dir / encode_project_name(str(wt))

    try:
        symlink_path.symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks not supported in this environment")

    removed = remove_worktree_project(wt, wt_root, env)
    assert removed is False
    assert outside_dir.exists()
    assert canary.exists()


@pytest.mark.unit
def test_sweep_orphan_run_projects(tmp_path: Path) -> None:
    """sweep_orphan_run_projects removes old orphan and keeps live, fresh, and non-run folders."""
    cfg_dir = tmp_path / "cfg"
    projects_dir = cfg_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    env = {"CLAUDE_CONFIG_DIR": str(cfg_dir)}

    wt_root = tmp_path / "staff-worktrees"
    wt_root.mkdir(parents=True, exist_ok=True)

    # Old orphan (worktree does not exist, mtime old)
    wt_old_orphan = wt_root / "run-old-orphan"
    old_orphan_folder = projects_dir / encode_project_name(str(wt_old_orphan))
    old_orphan_folder.mkdir()
    (old_orphan_folder / "file.json").write_text("{}")

    # Old live (worktree exists, mtime old)
    wt_old_live = wt_root / "run-old-live"
    wt_old_live.mkdir()
    old_live_folder = projects_dir / encode_project_name(str(wt_old_live))
    old_live_folder.mkdir()
    (old_live_folder / "file.json").write_text("{}")

    # Fresh orphan (worktree does not exist, mtime fresh)
    wt_fresh_orphan = wt_root / "run-fresh-orphan"
    fresh_orphan_folder = projects_dir / encode_project_name(str(wt_fresh_orphan))
    fresh_orphan_folder.mkdir()
    (fresh_orphan_folder / "file.json").write_text("{}")

    # Old non-run folders (e.g. panel folder, chat folder)
    old_panel_folder = projects_dir / "-tmp-staff-panel-x"
    old_panel_folder.mkdir()
    (old_panel_folder / "file.json").write_text("{}")

    old_chat_folder = projects_dir / "-tmp-staff-chat-th-1"
    old_chat_folder.mkdir()
    (old_chat_folder / "file.json").write_text("{}")

    now = 1_000_000.0
    min_age = 3600.0  # 1 hour
    old_time = now - 7200.0  # 2 hours old
    fresh_time = now - 600.0  # 10 minutes old

    os.utime(old_orphan_folder, (old_time, old_time))
    os.utime(old_live_folder, (old_time, old_time))
    os.utime(fresh_orphan_folder, (fresh_time, fresh_time))
    os.utime(old_panel_folder, (old_time, old_time))
    os.utime(old_chat_folder, (old_time, old_time))

    removed = sweep_orphan_run_projects(env, wt_root, min_age_seconds=min_age, now=now)
    assert removed == [old_orphan_folder.name]

    assert not old_orphan_folder.exists()
    assert old_live_folder.exists()
    assert fresh_orphan_folder.exists()
    assert old_panel_folder.exists()
    assert old_chat_folder.exists()


@pytest.mark.unit
def test_sweep_orphan_run_projects_missing_root(tmp_path: Path) -> None:
    """sweep_orphan_run_projects returns [] when projects root does not exist."""
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "nonexistent")}
    wt_root = tmp_path / "staff-worktrees"
    removed = sweep_orphan_run_projects(env, wt_root)
    assert removed == []


@pytest.mark.unit
def test_sweep_orphan_run_projects_requires_positive_min_age(tmp_path: Path) -> None:
    """sweep_orphan_run_projects raises AssertionError when min_age_seconds <= 0."""
    wt_root = tmp_path / "staff-worktrees"
    with pytest.raises(AssertionError):
        sweep_orphan_run_projects({}, wt_root, min_age_seconds=0)
    with pytest.raises(AssertionError):
        sweep_orphan_run_projects({}, wt_root, min_age_seconds=-10)


@pytest.mark.unit
def test_chat_project_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """chat_project_prefix encodes os.path.join(tmpdir, CHAT_SCRATCH_PREFIX)."""
    assert CHAT_SCRATCH_PREFIX == "staff_chat_"
    assert DEFAULT_CHAT_IDLE_SECONDS == 14 * 24 * 3600
    assert chat_project_prefix("/tmp") == "-tmp-staff-chat-"

    monkeypatch.setattr("tempfile.gettempdir", lambda: "/tmp")
    assert chat_project_prefix() == "-tmp-staff-chat-"


@pytest.mark.unit
def test_sweep_idle_chat_scratch(tmp_path: Path) -> None:
    """sweep_idle_chat_scratch sweeps old scratch dirs, old orphan projects, and keeps fresh / non-chat."""
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    cfg_dir = tmp_path / "cfg"
    projects_dir = cfg_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    env = {"CLAUDE_CONFIG_DIR": str(cfg_dir)}

    now = 2_000_000.0
    idle = 14 * 24 * 3600.0
    old_time = now - idle - 3600.0
    fresh_time = now - 600.0

    # 1. staff_chat_old: scratch + project folder both older than idle
    d_old = tmpdir / "staff_chat_old"
    d_old.mkdir()
    p_old = projects_dir / encode_project_name(str(d_old))
    p_old.mkdir()
    (p_old / "session.json").write_text("{}")
    os.utime(d_old, (old_time, old_time))
    os.utime(p_old, (old_time, old_time))
    os.utime(p_old / "session.json", (old_time, old_time))

    # 2. staff_chat_fresh: project folder has a fresh file even though scratch dir mtime is old — must be KEPT
    d_fresh = tmpdir / "staff_chat_fresh"
    d_fresh.mkdir()
    p_fresh = projects_dir / encode_project_name(str(d_fresh))
    p_fresh.mkdir()
    (p_fresh / "session.json").write_text("{}")
    os.utime(d_fresh, (old_time, old_time))
    os.utime(p_fresh, (old_time, old_time))
    os.utime(p_fresh / "session.json", (fresh_time, fresh_time))

    # 3. staff_chat_noproj_old: no project folder, old dir -> removed
    d_noproj_old = tmpdir / "staff_chat_noproj_old"
    d_noproj_old.mkdir()
    os.utime(d_noproj_old, (old_time, old_time))

    # 4. staff_panel_x dir (kept)
    d_panel_x = tmpdir / "staff_panel_x"
    d_panel_x.mkdir()
    os.utime(d_panel_x, (old_time, old_time))

    # 5. orphan project folder for <tmpdir>/staff_chat_gone that is old (removed)
    gone_scratch = tmpdir / "staff_chat_gone"
    p_orphan_old = projects_dir / encode_project_name(str(gone_scratch))
    p_orphan_old.mkdir()
    (p_orphan_old / "session.json").write_text("{}")
    os.utime(p_orphan_old, (old_time, old_time))
    os.utime(p_orphan_old / "session.json", (old_time, old_time))

    # 6. orphan project folder for <tmpdir>/staff_chat_gone_fresh that is fresh (kept)
    gone_fresh_scratch = tmpdir / "staff_chat_gone_fresh"
    p_orphan_fresh = projects_dir / encode_project_name(str(gone_fresh_scratch))
    p_orphan_fresh.mkdir()
    (p_orphan_fresh / "session.json").write_text("{}")
    os.utime(p_orphan_fresh, (fresh_time, fresh_time))
    os.utime(p_orphan_fresh / "session.json", (fresh_time, fresh_time))

    # 7. non-chat project folder (kept)
    p_nonchat = projects_dir / "-tmp-other-project"
    p_nonchat.mkdir()
    (p_nonchat / "session.json").write_text("{}")
    os.utime(p_nonchat, (old_time, old_time))

    removed = sweep_idle_chat_scratch(env, idle_seconds=idle, now=now, tmpdir=str(tmpdir))

    expected_removed = sorted(["staff_chat_noproj_old", "staff_chat_old", p_orphan_old.name])
    assert removed == expected_removed

    # Assert exact filesystem state
    assert not d_old.exists()
    assert not p_old.exists()
    assert d_fresh.exists()
    assert p_fresh.exists()
    assert not d_noproj_old.exists()
    assert d_panel_x.exists()
    assert not p_orphan_old.exists()
    assert p_orphan_fresh.exists()
    assert p_nonchat.exists()


@pytest.mark.unit
def test_sweep_idle_chat_scratch_symlink_not_followed(tmp_path: Path) -> None:
    """A symlink named staff_chat_link pointing outside tmpdir is not followed and target survives."""
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    outside_dir = tmp_path / "outside_directory"
    outside_dir.mkdir(parents=True, exist_ok=True)
    canary = outside_dir / "target_file.txt"
    canary.write_text("keep me safe")

    symlink_path = tmpdir / "staff_chat_link"
    try:
        symlink_path.symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks not supported in this environment")

    now = 1_000_000.0
    idle = 3600.0
    old_time = now - 7200.0
    os.utime(outside_dir, (old_time, old_time))
    os.utime(canary, (old_time, old_time))

    cfg_dir = tmp_path / "cfg"
    (cfg_dir / "projects").mkdir(parents=True, exist_ok=True)
    env = {"CLAUDE_CONFIG_DIR": str(cfg_dir)}

    removed = sweep_idle_chat_scratch(env, idle_seconds=idle, now=now, tmpdir=str(tmpdir))
    assert symlink_path.name not in removed
    assert outside_dir.exists()
    assert canary.exists()


@pytest.mark.unit
def test_sweep_idle_chat_scratch_requires_positive_idle_seconds() -> None:
    """sweep_idle_chat_scratch raises AssertionError when idle_seconds <= 0."""
    with pytest.raises(AssertionError):
        sweep_idle_chat_scratch({}, idle_seconds=0)
    with pytest.raises(AssertionError):
        sweep_idle_chat_scratch({}, idle_seconds=-100)


@pytest.mark.unit
def test_sweep_idle_chat_scratch_missing_projects_root(tmp_path: Path) -> None:
    """A missing projects root still sweeps scratch dirs."""
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    d_old = tmpdir / "staff_chat_old"
    d_old.mkdir()
    now = 1_000_000.0
    idle = 3600.0
    old_time = now - 7200.0
    os.utime(d_old, (old_time, old_time))

    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "nonexistent")}
    removed = sweep_idle_chat_scratch(env, idle_seconds=idle, now=now, tmpdir=str(tmpdir))
    assert removed == ["staff_chat_old"]
    assert not d_old.exists()


@pytest.mark.unit
def test_sweep_keeps_a_just_touched_chat_dir_whose_project_folder_is_old(tmp_path: Path) -> None:
    """#1688: a resumed idle thread is not swept before its CLI writes a new session."""
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "cfg")}
    scratch = tmpdir / "staff_chat_resumed"
    scratch.mkdir()
    project = tmp_path / "cfg" / "projects" / encode_project_name(str(scratch))
    project.mkdir(parents=True)
    (project / "session.jsonl").write_text("{}")
    now = 10_000_000.0
    old = now - 30 * 24 * 3600
    os.utime(project / "session.jsonl", (old, old))
    os.utime(project, (old, old))
    os.utime(scratch, (now, now))

    removed = sweep_idle_chat_scratch(env, idle_seconds=14 * 24 * 3600, now=now, tmpdir=str(tmpdir))

    assert removed == []
    assert scratch.exists() and project.exists()
