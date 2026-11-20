"""Active staff sessions and default agent worktree roots (#4440)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import time
from pathlib import Path

import agent_active_workdirs as active_dirs
import agent_worktree_hygiene as hygiene
import pytest


def test_active_staff_workdir_blocks_worktree_eligibility(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wt = tmp_path / "Tools-run-abc"
    wt.mkdir()
    old = time.time() - 48 * 3600
    os.utime(wt, (old, old))
    active = {wt.resolve()}
    monkeypatch.setattr(hygiene, "active_staff_workdirs", lambda: active)
    entry = hygiene.WorktreeEntry(path=wt, branch="agent/slice")
    ok, reason = hygiene.worktree_entry_is_eligible(
        tmp_path / "Tools",
        entry,
        min_age_hours=0,
        active_workdirs=active,
    )
    assert not ok and reason == "active_session"


def test_unreadable_staff_db_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hygiene, "_worktree_clean", lambda _path: True)
    monkeypatch.setattr(hygiene, "_branch_merged_or_closed", lambda *_args: True)
    monkeypatch.setattr(hygiene, "_submodule_problem", lambda _path: "")
    wt = tmp_path / "held-wt"
    wt.mkdir()
    old = time.time() - 48 * 3600
    os.utime(wt, (old, old))
    entry = hygiene.WorktreeEntry(path=wt, branch="agent/x")
    ok, reason = hygiene.worktree_entry_is_eligible(
        Path("/tmp/repo"),
        entry,
        min_age_hours=0,
        active_workdirs=None,
    )
    assert not ok and reason == "active_session_unknown"


def test_staff_db_lists_running_workdirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "staff_runs.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE runs (
            id TEXT PRIMARY KEY, role TEXT, provider TEXT, model TEXT, machine TEXT,
            repo TEXT, target_kind TEXT, target_ref TEXT, prompt TEXT, status TEXT,
            requested_by TEXT, created_at TEXT, workdir TEXT
        );
        INSERT INTO runs VALUES (
            'run-1','r','claude',NULL,'node','Tools','issue','#1','p','running','','now',
            '/home/test/staff-worktrees/Tools-run-1'
        );
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(active_dirs, "staff_runs_db_path", lambda: db)
    found = active_dirs.active_staff_workdirs()
    assert found == {Path("/home/test/staff-worktrees/Tools-run-1").resolve()}


def test_linked_worktree_under_staff_root_registers_owner_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``~/staff-worktrees`` linked trees must pull in their owning primary checkout."""
    home = tmp_path / "home"
    staff_root = home / "staff-worktrees"
    staff_root.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(hygiene, "repos_roots", lambda: [])
    main = tmp_path / "checkouts" / "Runner_Dashboard"
    main.mkdir(parents=True)
    (main / ".git").mkdir()
    linked = staff_root / "Runner_Dashboard-run-xyz"
    _git = hygiene._git

    def fake_git(cwd: Path, *args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args == ("rev-parse", "--git-common-dir") and cwd == linked:
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=str(main / ".git") + "\n", stderr="")
        return _git(cwd, *args, **kwargs)

    monkeypatch.setattr(hygiene, "_git", fake_git)
    monkeypatch.setattr(
        hygiene,
        "default_agent_worktree_roots",
        lambda _home=None: [staff_root],
    )
    linked.mkdir(parents=True)
    (linked / ".git").write_text("gitdir: linked\n", encoding="utf-8")
    checkouts = hygiene.enrich_checkouts_from_agent_roots([])
    assert checkouts == [main]