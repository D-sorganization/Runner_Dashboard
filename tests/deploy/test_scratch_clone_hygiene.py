"""Accounting for git clones and aged task folders in a scratch root such as ``/tmp`` (#4440).

Report only: the functions under test never delete. Real git clones under ``tmp_path``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
import scratch_clone_hygiene as scratch

HOUR = 3600.0
_SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "prune_agent_worktrees.py"


def _git(cwd: Path, *args: str) -> str:
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX"):
        env.pop(key, None)
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    res = subprocess.run(  # noqa: S603
        ["git", "-c", "protocol.file.allow=always", "-C", str(cwd), *args],  # noqa: S607
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert res.returncode == 0, f"git {' '.join(args)}: {res.stderr}"
    return res.stdout


def _commit(repo: Path, name: str) -> None:
    (repo / name).write_text(f"{name}\n", encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)


def _origin(origins: Path, name: str) -> Path:
    """A bare origin ``name.git`` with one commit on ``main``."""
    bare = origins / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(bare)], check=True)  # noqa: S603, S607
    seed = origins / f"{name}-seed"
    seed.mkdir()
    _git(seed, "init", "-q", "--initial-branch=main")
    _commit(seed, "README.md")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "-q", "-u", "origin", "main")
    return bare


def _clone(origin: Path, dest: Path) -> Path:
    _git(dest.parent, "clone", "-q", str(origin), str(dest))
    return dest


@pytest.fixture
def scratch_root(tmp_path: Path) -> dict[str, Path]:
    """One scratch root holding a clone of each kind the sweep must tell apart."""
    origins, root = tmp_path / "origins", tmp_path / "scratch"
    origins.mkdir()
    root.mkdir()
    public, private = _origin(origins, "Plain_Repo"), _origin(origins, "work_private")

    clean = _clone(public, root / "review-clean")
    dirty = _clone(public, root / "review-dirty")
    (dirty / "notes.txt").write_text("not committed\n", encoding="utf-8")
    unpushed = _clone(public, root / "review-unpushed")
    _commit(unpushed, "local-only.txt")
    stashed = _clone(public, root / "review-stashed")
    (stashed / "README.md").write_text("changed\n", encoding="utf-8")
    _git(stashed, "stash", "push", "-q")
    secret = _clone(private, root / "renamed-clone")
    busy = _clone(public, root / "review-busy")
    young = _clone(public, root / "review-young")
    (root / "plain-folder").mkdir()
    (root / "plain-folder" / "file.txt").write_text("x\n", encoding="utf-8")
    return {
        "root": root,
        "clean": clean,
        "dirty": dirty,
        "unpushed": unpushed,
        "stashed": stashed,
        "private": secret,
        "busy": busy,
        "young": young,
    }


def _plan(paths: dict[str, Path], **kwargs: Any) -> dict[str, Any]:
    """Plan as seen 48 h from now, with ``review-young`` touched 1 h before that."""
    now = time.time() + 48 * HOUR
    os.utime(paths["young"] / ".git" / "HEAD", (now - HOUR, now - HOUR))
    defaults: dict[str, Any] = {"min_age_hours": 24.0, "now": now, "busy_dirs": lambda: {paths["busy"] / "sub"}}
    return scratch.plan_scratch_clones([paths["root"]], **{**defaults, **kwargs})


def _reasons(report: dict[str, Any]) -> dict[str, str]:
    return {Path(row["path"]).name: row["reason"] for row in report["kept"]}


def test_only_the_clean_pushed_idle_public_clone_is_reclaimable(scratch_root: dict[str, Path]) -> None:
    report = _plan(scratch_root)

    assert [Path(row["path"]).name for row in report["reclaimable"]] == ["review-clean"]
    assert _reasons(report) == {
        "review-dirty": "dirty",
        "review-unpushed": "unpushed",
        "review-stashed": "stash",
        "renamed-clone": "private_repo",
        "review-busy": "in_use",
        "review-young": "too_young",
    }
    assert report["kept_by_reason"]["dirty"] == 1


def test_the_report_gives_size_and_age_and_deletes_nothing(scratch_root: dict[str, Path]) -> None:
    before = sorted(p.name for p in scratch_root["root"].iterdir())

    report = _plan(scratch_root)

    (row,) = report["reclaimable"]
    assert row["size_bytes"] > 0 and row["age_hours"] >= 24.0
    assert report["reclaimable_bytes"] == row["size_bytes"]
    assert report["mode"] == "report-only"
    assert sorted(p.name for p in scratch_root["root"].iterdir()) == before


def test_unknown_working_directories_keep_every_clone(scratch_root: dict[str, Path]) -> None:
    """When the host cannot say which folders are in use, nothing is offered for removal."""
    report = _plan(scratch_root, busy_dirs=lambda: None)

    assert report["reclaimable"] == []
    assert _reasons(report)["review-clean"] == "in_use_unknown"


def test_a_clone_with_no_remote_is_kept(tmp_path: Path) -> None:
    root = tmp_path / "scratch"
    local = root / "local-only"
    local.mkdir(parents=True)
    _git(local, "init", "-q", "--initial-branch=main")
    _commit(local, "README.md")

    report = scratch.plan_scratch_clones([root], min_age_hours=0.0, busy_dirs=set)

    assert report["reclaimable"] == [] and _reasons(report) == {"local-only": "unpushed"}


def test_linked_worktrees_and_symlinks_are_left_to_the_worktree_sweep(scratch_root: dict[str, Path]) -> None:
    root = scratch_root["root"]
    _git(scratch_root["clean"], "worktree", "add", "-q", "-b", "wt", str(root / "linked"))
    try:
        (root / "link").symlink_to(scratch_root["dirty"], target_is_directory=True)
    except OSError:
        pass  # no symlink privilege on this host; the linked-worktree half still runs

    names = {Path(row["path"]).name for row in _plan(scratch_root)["reclaimable"]}
    reasons = _reasons(_plan(scratch_root))

    assert "linked" not in names and "link" not in names
    assert reasons["linked"] == "linked_worktree"
    assert reasons["review-clean"] == "has_worktrees", "its linked worktree would be orphaned"


def test_aged_task_folders_are_listed_by_newest_file(tmp_path: Path) -> None:
    root = tmp_path / "scratch"
    old, fresh, other = root / "claude-1000", root / "claude-1001", root / "keep-me"
    for folder in (old, fresh, other):
        (folder / "nested").mkdir(parents=True)
        (folder / "nested" / "out.log").write_text("output\n", encoding="utf-8")
    now = time.time() + 72 * HOUR
    os.utime(fresh / "nested" / "out.log", (now - HOUR, now - HOUR))

    report = scratch.plan_aged_task_folders([root], patterns=("claude-*",), min_age_hours=48.0, now=now)

    assert [Path(row["path"]).name for row in report["aged"]] == ["claude-1000"]
    assert report["aged"][0]["size_bytes"] > 0 and report["aged_bytes"] == report["aged"][0]["size_bytes"]
    assert old.exists() and fresh.exists() and other.exists()


def test_a_missing_scratch_root_is_an_empty_report(tmp_path: Path) -> None:
    report = scratch.plan_scratch_clones([tmp_path / "absent"], busy_dirs=set)

    assert report["reclaimable"] == [] and report["kept"] == []


def test_cli_reports_scratch_roots_only_when_asked(
    scratch_root: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = importlib.util.spec_from_file_location("prune_agent_worktrees_scratch", _SCRIPT)
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "discover_repo_checkouts", lambda _extra: [])
    before = sorted(p.name for p in scratch_root["root"].iterdir())

    assert cli.main([]) == 0
    assert "scratch" not in json.loads(capsys.readouterr().out)

    assert cli.main(["--apply", "--scratch-root", str(scratch_root["root"]), "--scratch-min-age-hours", "0"]) == 0
    report = json.loads(capsys.readouterr().out)["scratch"]

    assert report["clones"]["mode"] == "report-only"
    assert sorted(p.name for p in scratch_root["root"].iterdir()) == before, "--apply never removes a scratch clone"
