"""Ignored non-regenerable files must block agent worktree removal (#4540)."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

import agent_worktree_hygiene as hygiene
import pytest
from worktree_ignored_policy import IGNORED_FILES_REASON

_FILE_PROTOCOL = ("-c", "protocol.file.allow=always")


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
        ["git", *_FILE_PROTOCOL, "-C", str(cwd), *args],  # noqa: S607
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert res.returncode == 0, f"git {' '.join(args)}: {res.stderr}"
    return res.stdout


def _commit_file(repo: Path, name: str, message: str) -> None:
    (repo / name).write_text(f"{message}\n", encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)


def _repo(root: Path, origins: Path, name: str) -> Path:
    origin = origins / f"{name}.git"
    subprocess.run(  # noqa: S603
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)],
        check=True,
        capture_output=True,
    )
    checkout = root / name
    checkout.mkdir(parents=True)
    _git(checkout, "init", "-q")
    (checkout / ".gitignore").write_text(".env\n__pycache__/\n", encoding="utf-8")
    _commit_file(checkout, "README.md", "init")
    _git(checkout, "add", ".gitignore")
    _git(checkout, "commit", "-q", "-m", "gitignore")
    _git(checkout, "branch", "-M", "main")
    _git(checkout, "remote", "add", "origin", str(origin))
    _git(checkout, "push", "-q", "-u", "origin", "main")
    return checkout


def _worktree(checkout: Path, name: str) -> Path:
    path = checkout.parent / f"{checkout.name}-worktrees" / name
    _git(checkout, "worktree", "add", "-q", "-b", f"agent/{name}", str(path))
    old = time.time() - 96 * 3600
    os.utime(path, (old, old))
    return path


@pytest.fixture(autouse=True)
def _isolated_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hygiene, "repos_roots", lambda: [])
    monkeypatch.setattr(hygiene, "active_staff_workdirs", lambda: set())
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "protocol.file.allow")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "always")


def _sweep(checkout: Path, wt: Path, *, apply: bool) -> dict[str, Any]:
    return hygiene.plan_agent_worktree_prune([checkout], apply=apply, min_age_hours=0)


def test_ignored_env_blocks_removal(tmp_path: Path) -> None:
    root, origins = tmp_path / "Repositories", tmp_path / "origins"
    origins.mkdir()
    checkout = _repo(root, origins, "Plain")
    wt = _worktree(checkout, "env-only")
    (wt / ".env").write_text("SECRET=1\n", encoding="utf-8")

    report = _sweep(checkout, wt, apply=True)

    assert wt.exists()
    assert report["removed"] == 0
    reasons = {Path(item["path"]).name: item["reason"] for item in report["skipped"]}
    assert reasons["env-only"] == IGNORED_FILES_REASON


def _repo_with_submodule(root: Path, origins: Path) -> tuple[Path, Path]:
    library = _repo(root, origins, "Lib4540")
    origin_lib = origins / "Lib4540.git"
    parent = _repo(root, origins, "SubParent4540")
    _git(parent, "submodule", "add", "-q", str(origin_lib), "vendor")
    _git(parent, "commit", "-q", "-m", "vendor")
    _git(parent, "push", "-q", "origin", "main")
    return parent, library


def test_submodule_ignored_env_blocks_removal(tmp_path: Path) -> None:
    root, origins = tmp_path / "Repositories", tmp_path / "origins"
    origins.mkdir()
    checkout, _library = _repo_with_submodule(root, origins)
    wt = _worktree(checkout, "sub-env")
    vendor = wt / "vendor"
    _git(wt, "submodule", "update", "-q", "--init")
    (vendor / ".env").write_text("TOKEN=1\n", encoding="utf-8")

    report = _sweep(checkout, wt, apply=True)

    assert wt.exists()
    assert report["removed"] == 0
    reasons = {Path(item["path"]).name: item["reason"] for item in report["skipped"]}
    assert reasons["sub-env"] == IGNORED_FILES_REASON


def test_regenerable_ignored_cache_allows_removal(tmp_path: Path) -> None:
    root, origins = tmp_path / "Repositories", tmp_path / "origins"
    origins.mkdir()
    checkout = _repo(root, origins, "Plain2")
    wt = _worktree(checkout, "cache-only")
    cache = wt / "__pycache__"
    cache.mkdir()
    (cache / "mod.pyc").write_bytes(b"\0")

    report = _sweep(checkout, wt, apply=True)

    assert not wt.exists()
    assert report["removed"] == 1
