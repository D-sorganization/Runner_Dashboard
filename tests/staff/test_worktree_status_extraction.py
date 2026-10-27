"""Guard the workspace -> worktree_status move (#2806): callers and patch targets unchanged."""

from __future__ import annotations

from pathlib import Path

from staff import workspace, worktree_status


def test_workspace_reexports_the_moved_probe() -> None:
    assert workspace.worktree_has_unpushed_commits is worktree_status.worktree_has_unpushed_commits


def test_missing_worktree_is_not_reported_as_unpushed(tmp_path: Path) -> None:
    assert worktree_status.worktree_has_unpushed_commits(tmp_path / "absent") is False
