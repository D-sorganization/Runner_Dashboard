"""Tests for staff workspace management and worktree cleanup (#1688)."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

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


@pytest.mark.unit
def test_finalize_run_worktree_persists_transcript_and_removes_clean_git_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """finalize_run_worktree copies .staff/transcript.log to transcripts/ and removes clean worktrees."""
    wt_root = tmp_path / "staff-worktrees"
    wt_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STAFF_WORKTREES_ROOT", str(wt_root))

    worktree = wt_root / "Runner_Dashboard-run-abc123"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: synthetic-fixture\n")
    (worktree / ".staff").mkdir(parents=True, exist_ok=True)
    (worktree / ".staff" / "transcript.log").write_text("hello transcript\n", encoding="utf-8")

    monkeypatch.setattr(workspace, "worktree_has_unpushed_commits", lambda wt: False)
    monkeypatch.setattr(workspace, "find_repo_checkout", lambda repo: None)
    monkeypatch.setattr(
        workspace, "remove_worktree", lambda path, **kwargs: shutil.rmtree(path) if path == worktree else None
    )

    updated: dict[str, str] = {}
    events: list[tuple[str, str, str]] = []

    class _DummyStore:
        db_path = tmp_path / "staff_runs.sqlite3"

        def update_run(self, run_id: str, **kwargs: str) -> None:
            updated.update(kwargs)

        def get_run(self, run_id: str) -> object:
            return SimpleNamespace(workdir=str(worktree), status="succeeded", pid=None, failure_class="", **updated)

        def append_event(self, run_id: str, kind: str, msg: str) -> None:
            events.append((run_id, kind, msg))

    workspace.finalize_run_worktree(_DummyStore(), "run-abc123", worktree, "Runner_Dashboard", branch="staff/test")

    persisted = tmp_path / "transcripts" / "run-abc123.log"
    assert persisted.is_file()
    assert persisted.read_text(encoding="utf-8") == "hello transcript\n"
    assert updated.get("transcript_path") == str(persisted)
    assert not worktree.exists()
    assert events == []


@pytest.mark.unit
def test_finalize_run_worktree_preserves_unpushed_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """finalize_run_worktree preserves worktrees with unpushed commits and emits a worktree event."""
    wt_root = tmp_path / "staff-worktrees"
    wt_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STAFF_WORKTREES_ROOT", str(wt_root))

    worktree = wt_root / "Runner_Dashboard-run-dirty1"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: synthetic-fixture\n")
    (worktree / ".staff").mkdir(parents=True, exist_ok=True)
    (worktree / ".staff" / "transcript.log").write_text("dirty transcript\n", encoding="utf-8")

    monkeypatch.setattr(workspace, "worktree_has_unpushed_commits", lambda wt: True)

    events: list[tuple[str, str, str]] = []

    class _DummyStore:
        db_path = tmp_path / "staff_runs.sqlite3"

        def get_run(self, run_id: str) -> object:
            return SimpleNamespace(workdir=str(worktree), status="succeeded", pid=None, failure_class="")

        def update_run(self, run_id: str, **kwargs: str) -> None:
            pass

        def append_event(self, run_id: str, kind: str, msg: str) -> None:
            events.append((run_id, kind, msg))

    workspace.finalize_run_worktree(_DummyStore(), "run-dirty1", worktree, "Runner_Dashboard", branch="staff/dirty")

    assert worktree.exists()
    assert any(kind == "worktree" and "unpushed worktree preserved" in msg for _, kind, msg in events)


@pytest.mark.unit
def test_sweep_stale_worktrees_removes_clean_preserves_unpushed_and_skips_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt_root = tmp_path / "staff-worktrees"
    wt_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STAFF_WORKTREES_ROOT", str(wt_root))

    clean_wt = wt_root / "Runner_Dashboard-run-clean1"
    clean_wt.mkdir(parents=True)
    (clean_wt / ".git").write_text("gitdir: synthetic-fixture\n")
    (clean_wt / ".staff").mkdir(parents=True)
    (clean_wt / ".staff" / "transcript.log").write_text("clean log\n", encoding="utf-8")

    dirty_wt = wt_root / "Runner_Dashboard-run-dirty1"
    (dirty_wt / ".git").mkdir(parents=True)

    active_wt = wt_root / "Runner_Dashboard-run-active1"
    (active_wt / ".git").mkdir(parents=True)

    monkeypatch.setattr(
        workspace,
        "worktree_has_unpushed_commits",
        lambda wt: Path(wt).name.endswith("dirty1"),
    )
    monkeypatch.setattr(workspace, "find_repo_checkout", lambda repo: None)
    monkeypatch.setattr(
        workspace, "remove_worktree", lambda path, **kwargs: shutil.rmtree(path) if path == clean_wt else None
    )
    updated: dict[str, str] = {}

    class _DummyStore:
        db_path = tmp_path / "staff_runs.sqlite3"

        def active_runs(self) -> list[object]:
            return [type("R", (), {"workdir": str(active_wt)})()]

        def update_run(self, run_id: str, **kwargs: str) -> None:
            updated.update(kwargs)

        def get_run(self, run_id: str) -> object:
            return SimpleNamespace(workdir=str(clean_wt), status="succeeded", pid=None, failure_class="", **updated)

    stats = workspace.sweep_stale_worktrees(_DummyStore())
    assert stats == {"removed": 1, "preserved_unpushed": 1, "skipped_active": 1}
    assert not clean_wt.exists()
    assert (tmp_path / "transcripts" / "run-clean1.log").is_file()
    assert dirty_wt.exists()
    assert active_wt.exists()
