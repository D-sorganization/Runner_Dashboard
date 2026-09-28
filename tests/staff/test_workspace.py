"""Tests for staff workspace management and worktree cleanup (#1688)."""

from __future__ import annotations

from pathlib import Path

import pytest
from staff import workspace
from staff.cli_projects import encode_project_name


@pytest.mark.unit
def test_remove_worktree_cleans_project_folder_when_under_worktrees_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """remove_worktree removes matching project folder when worktree is under STAFF_WORKTREES_ROOT."""
    wt_root = tmp_path / "staff-worktrees"
    wt_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STAFF_WORKTREES_ROOT", str(wt_root))

    cfg_dir = tmp_path / "cfg"
    projects_dir = cfg_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_dir))

    worktree = wt_root / "run-test123"
    worktree.mkdir(parents=True, exist_ok=True)

    project_folder = projects_dir / encode_project_name(str(worktree))
    project_folder.mkdir(parents=True, exist_ok=True)
    (project_folder / "session.json").write_text("{}")

    assert worktree.exists()
    assert project_folder.exists()

    workspace.remove_worktree(worktree, checkout=None)

    assert not worktree.exists()
    assert not project_folder.exists()


@pytest.mark.unit
def test_remove_worktree_outside_root_leaves_project_folders_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """remove_worktree on a worktree outside STAFF_WORKTREES_ROOT leaves project folders untouched."""
    wt_root = tmp_path / "staff-worktrees"
    wt_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STAFF_WORKTREES_ROOT", str(wt_root))

    cfg_dir = tmp_path / "cfg"
    projects_dir = cfg_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_dir))

    outside_root = tmp_path / "other-worktrees"
    outside_root.mkdir(parents=True, exist_ok=True)
    outside_worktree = outside_root / "run-outside"
    outside_worktree.mkdir(parents=True, exist_ok=True)

    project_folder = projects_dir / encode_project_name(str(outside_worktree))
    project_folder.mkdir(parents=True, exist_ok=True)
    (project_folder / "session.json").write_text("{}")

    workspace.remove_worktree(outside_worktree, checkout=None)

    assert not outside_worktree.exists()
    assert project_folder.exists()


def test_default_repos_roots_use_the_windows_profile_that_holds_repositories(monkeypatch, tmp_path):
    """The WSL login is not the Windows profile name; don't guess from $USERNAME (#1718)."""
    from staff import workspace as ws

    profile_root = tmp_path / "Users" / "winprofile" / "Repositories"
    monkeypatch.setenv("USERNAME", "wsl-login")
    monkeypatch.setattr(ws, "windows_repositories_root", lambda: profile_root)

    roots = ws._default_repos_roots(tmp_path / "home")

    assert profile_root in roots
    assert all("wsl-login" not in str(r) for r in roots)
