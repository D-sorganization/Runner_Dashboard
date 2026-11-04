"""TDD for scheduled agent repo worktree hygiene (Refs #2550)."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from agent_worktree_hygiene import (
    DEFAULT_PROTECTED_WORKTREES,
    WorktreeEntry,
    plan_agent_worktree_prune,
    worktree_entry_is_eligible,
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX"):
        env.pop(key, None)
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(cwd), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture
def repo_with_worktrees(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)  # noqa: S603
    main = tmp_path / "main"
    main.mkdir()
    subprocess.run(["git", "init"], cwd=main, check=True, capture_output=True)  # noqa: S603
    (main / "README.md").write_text("base\n", encoding="utf-8")
    _git(main, "add", "README.md")
    _git(main, "commit", "-m", "init")
    _git(main, "branch", "-M", "main")
    _git(main, "remote", "add", "origin", str(origin))
    _git(main, "push", "-u", "origin", "main")
    return main


def test_protected_worktree_never_eligible(repo_with_worktrees: Path) -> None:
    wt = repo_with_worktrees.parent / "pr-10507"
    _git(repo_with_worktrees, "worktree", "add", "-b", "feat/pr-10507", str(wt))
    entry = {"path": wt, "branch": "feat/pr-10507"}
    ok, reason = worktree_entry_is_eligible(
        repo_with_worktrees,
        entry,
        min_age_hours=0,
        protected=DEFAULT_PROTECTED_WORKTREES,
        now=time.time() + 86400,
    )
    assert not ok and reason == "protected"


def test_dirty_worktree_is_not_eligible(repo_with_worktrees: Path) -> None:
    wt = repo_with_worktrees.parent / "stale-wt"
    _git(repo_with_worktrees, "worktree", "add", "-b", "stale/branch", str(wt))
    (wt / "dirty.txt").write_text("x", encoding="utf-8")
    old = time.time() - 100 * 3600
    os.utime(wt, (old, old))
    entry = {"path": wt, "branch": "stale/branch"}
    ok, reason = worktree_entry_is_eligible(
        repo_with_worktrees,
        entry,
        min_age_hours=72,
        protected=DEFAULT_PROTECTED_WORKTREES,
        now=time.time(),
    )
    assert not ok and reason == "dirty"


def test_merged_linked_worktree_is_eligible(repo_with_worktrees: Path) -> None:
    wt = repo_with_worktrees.parent / "merged-wt"
    _git(repo_with_worktrees, "worktree", "add", "-b", "merged/slice", str(wt))
    _git(wt, "commit", "--allow-empty", "-m", "done")
    _git(repo_with_worktrees, "checkout", "main")
    _git(repo_with_worktrees, "merge", "--ff-only", "merged/slice")
    _git(repo_with_worktrees, "push", "origin", "main")
    _git(wt, "fetch", "origin")
    old = time.time() - 100 * 3600
    os.utime(wt, (old, old))
    ok, reason = worktree_entry_is_eligible(
        repo_with_worktrees,
        {"path": wt, "branch": "merged/slice"},
        min_age_hours=72,
        protected=DEFAULT_PROTECTED_WORKTREES,
    )
    assert ok, reason


def test_dry_run_logs_without_removing(repo_with_worktrees: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wt = repo_with_worktrees.parent / "old-clean"
    entry = WorktreeEntry(path=wt, branch="merged/slice")
    monkeypatch.setattr(
        "agent_worktree_hygiene._parse_worktree_list",
        lambda checkout: [entry],
    )
    monkeypatch.setattr(
        "agent_worktree_hygiene.worktree_entry_is_eligible",
        lambda checkout, ent, **kw: (True, "eligible"),
    )
    removed: list[str] = []
    monkeypatch.setattr(
        "agent_worktree_hygiene._git_worktree_remove",
        lambda checkout, path: removed.append(str(path)) or True,
    )
    report = plan_agent_worktree_prune(
        [repo_with_worktrees],
        apply=False,
        min_age_hours=72,
        protected=DEFAULT_PROTECTED_WORKTREES,
    )
    assert report["dry_run"] is True
    assert report["would_remove"], report.get("skipped", removed)
    assert removed == []


def test_apply_uses_worktree_remove_without_force(repo_with_worktrees: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wt = repo_with_worktrees.parent / "old-clean-2"
    entry = WorktreeEntry(path=wt, branch="merged/two")
    monkeypatch.setattr(
        "agent_worktree_hygiene._parse_worktree_list",
        lambda checkout: [entry],
    )
    monkeypatch.setattr(
        "agent_worktree_hygiene.worktree_entry_is_eligible",
        lambda checkout, ent, **kw: (True, "eligible"),
    )
    calls: list[list[str]] = []

    def fake_remove(checkout: Path, path: Path) -> bool:
        calls.append(["worktree", "remove", str(path)])
        return True

    monkeypatch.setattr("agent_worktree_hygiene._git_worktree_remove", fake_remove)
    report = plan_agent_worktree_prune([repo_with_worktrees], apply=True, min_age_hours=72)
    assert report["removed"] == 1, report.get("skipped")
    assert calls and "remove" in calls[0] and "--force" not in calls[0]
