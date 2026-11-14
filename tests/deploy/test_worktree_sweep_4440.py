"""The worktree sweep removes only clean, pushed worktrees of non-private repos (#4440).

Real git repos under ``tmp_path``: a plain repo, a repo with a submodule, and a private repo.
Nothing outside ``tmp_path`` is read or removed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import agent_worktree_hygiene as hygiene
import pytest

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
    """A checkout ``root/name`` on ``main``, pushed to a bare origin."""
    origin = origins / f"{name}.git"
    bare = ["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)]
    subprocess.run(bare, check=True, capture_output=True)  # noqa: S603
    checkout = root / name
    checkout.mkdir(parents=True)
    _git(checkout, "init", "-q")
    _commit_file(checkout, "README.md", "init")
    _git(checkout, "branch", "-M", "main")
    _git(checkout, "remote", "add", "origin", str(origin))
    _git(checkout, "push", "-q", "-u", "origin", "main")
    return checkout


def _worktree(checkout: Path, name: str) -> Path:
    path = checkout.parent / f"{checkout.name}-worktrees" / name
    _git(checkout, "worktree", "add", "-q", "-b", f"agent/{name}", str(path))
    return path


def _init_submodule(worktree: Path) -> Path:
    _git(worktree, "submodule", "update", "-q", "--init")
    return worktree / "vendor"


@pytest.fixture
def fleet(tmp_path: Path) -> dict[str, Path]:
    root, origins = tmp_path / "Repositories", tmp_path / "origins"
    origins.mkdir()
    plain = _repo(root, origins, "Plain_Repo")
    private = _repo(root, origins, "work_private")
    library = _repo(tmp_path / "upstream", origins, "Library")
    with_sub = _repo(root, origins, "Sub_Repo")
    _git(with_sub, "submodule", "add", "-q", str(origins / "Library.git"), "vendor")
    _git(with_sub, "commit", "-q", "-m", "add vendor")
    _git(with_sub, "push", "-q", "origin", "main")

    trees = {
        "root": root,
        "plain": plain,
        "private": private,
        "with_sub": with_sub,
        "library": library,
        "clean": _worktree(plain, "clean"),
        "dirty": _worktree(plain, "dirty"),
        "unpushed": _worktree(plain, "unpushed"),
        "private_clean": _worktree(private, "clean"),
        "sub_clean": _worktree(with_sub, "sub-clean"),
        "sub_uninitialised": _worktree(with_sub, "sub-uninitialised"),
        "sub_dirty": _worktree(with_sub, "sub-dirty"),
        "sub_unpushed": _worktree(with_sub, "sub-unpushed"),
    }
    (trees["dirty"] / "scratch.txt").write_text("x", encoding="utf-8")
    _commit_file(trees["unpushed"], "work.txt", "local only")

    _init_submodule(trees["sub_clean"])
    (_init_submodule(trees["sub_dirty"]) / "scratch.txt").write_text("x", encoding="utf-8")
    vendor = _init_submodule(trees["sub_unpushed"])
    recorded = _git(vendor, "rev-parse", "HEAD").strip()
    _git(vendor, "checkout", "-q", "-b", "local-fix")
    _commit_file(vendor, "fix.txt", "local only")
    _git(vendor, "checkout", "-q", recorded)
    return trees


def _sweep(fleet: dict[str, Path], *, apply: bool, **kwargs: Any) -> dict[str, Any]:
    checkouts = hygiene.discover_repo_checkouts([fleet["root"]])
    return hygiene.plan_agent_worktree_prune(checkouts, apply=apply, min_age_hours=0, **kwargs)


def _reasons(report: dict[str, Any]) -> dict[str, str]:
    return {Path(item["path"]).name: item["reason"] for item in report["skipped"]}


@pytest.fixture(autouse=True)
def _only_the_fixture_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery must not look at this machine's real checkouts."""
    monkeypatch.setattr(hygiene, "repos_roots", lambda: [])
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "protocol.file.allow")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "always")


def test_only_clean_pushed_non_private_worktrees_are_removed(fleet: dict[str, Path]) -> None:
    report = _sweep(fleet, apply=True)

    removed = {name for name in ("clean", "sub_clean", "sub_uninitialised") if not fleet[name].exists()}
    assert removed == {"clean", "sub_clean", "sub_uninitialised"}
    for kept in ("dirty", "unpushed", "private_clean", "sub_dirty", "sub_unpushed"):
        assert fleet[kept].exists(), kept
    assert report["removed"] == 3
    assert _reasons(report) == {
        "dirty": "dirty",
        "unpushed": "dirty",
        "sub-dirty": "dirty",
        "sub-unpushed": "submodule_unpushed",
        "clean": "private_repo",
    }
    assert report["skipped_by_reason"] == {"dirty": 3, "private_repo": 1, "submodule_unpushed": 1}


def test_a_dry_run_removes_nothing_and_lists_each_worktree_once(fleet: dict[str, Path]) -> None:
    report = _sweep(fleet, apply=False)

    assert report["dry_run"] is True and report["removed"] == 0
    for name in ("clean", "sub_clean", "sub_uninitialised", "dirty", "private_clean"):
        assert fleet[name].exists(), name
    listed = [item["path"] for item in report["would_remove"] + report["skipped"]]
    assert len(listed) == len(set(listed)) == 8
    assert sorted((item["repo"], Path(item["path"]).name) for item in report["would_remove"]) == [
        ("Plain_Repo", "clean"),
        ("Sub_Repo", "sub-clean"),
        ("Sub_Repo", "sub-uninitialised"),
    ]


def test_a_linked_worktree_under_a_repo_root_is_not_scanned_as_a_checkout(fleet: dict[str, Path]) -> None:
    """A worktree placed beside the checkouts once listed every sibling again under its own name."""
    stray = fleet["root"] / "Plain_Repo-stray"
    _git(fleet["plain"], "worktree", "add", "-q", "-b", "agent/stray", str(stray))

    assert stray not in hygiene.discover_repo_checkouts([fleet["root"]])
    report = hygiene.plan_agent_worktree_prune([fleet["plain"], stray, fleet["plain"]], apply=False, min_age_hours=0)

    listed = report["would_remove"] + report["skipped"]
    assert {item["repo"] for item in listed} == {"Plain_Repo"}
    assert sorted(Path(item["path"]).name for item in listed) == ["Plain_Repo-stray", "clean", "dirty", "unpushed"]


def test_a_private_repo_is_recognised_by_its_origin_when_the_folder_is_renamed(fleet: dict[str, Path]) -> None:
    renamed = fleet["root"] / "wp"
    _git(fleet["private"], "worktree", "remove", str(fleet["private_clean"]))
    fleet["private"].rename(renamed)
    kept = _worktree(renamed, "kept")

    report = hygiene.plan_agent_worktree_prune([renamed], apply=True, min_age_hours=0)

    assert kept.exists() and report["removed"] == 0
    assert [item["reason"] for item in report["skipped"]] == ["private_repo"]


def test_private_repos_are_not_fetched(fleet: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    fetched: list[str] = []
    real_git = hygiene._git

    def spy(cwd: Path, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if args and args[0] == "fetch":
            fetched.append(Path(cwd).name)
        return real_git(cwd, *args, **kwargs)

    monkeypatch.setattr(hygiene, "_git", spy)
    _sweep(fleet, apply=False)

    assert "work_private" not in fetched


def test_the_default_private_repos() -> None:
    assert set(hygiene.DEFAULT_PROTECTED_REPOS) == {"work_private", "Tools_Private", "glass"}


@pytest.mark.parametrize("name", ["work_private", "Tools_Private", "tools_private", "GLASS"])
def test_private_repo_names_match_without_regard_to_case(tmp_path: Path, name: str) -> None:
    assert hygiene.is_protected_repo(tmp_path / name, "", hygiene.DEFAULT_PROTECTED_REPOS)


@pytest.mark.parametrize("name", ["Tools", "glass_models", "Runner_Dashboard"])
def test_other_repos_are_not_private(tmp_path: Path, name: str) -> None:
    assert not hygiene.is_protected_repo(
        tmp_path / name, f"https://example.invalid/org/{name}.git", hygiene.DEFAULT_PROTECTED_REPOS
    )


def test_a_detached_worktree_is_reported_not_dropped_from_the_report(fleet: dict[str, Path]) -> None:
    detached = fleet["plain"].parent / "Plain_Repo-worktrees" / "detached"
    _git(fleet["plain"], "worktree", "add", "-q", "--detach", str(detached))

    report = hygiene.plan_agent_worktree_prune([fleet["plain"]], apply=True, min_age_hours=0)

    assert detached.exists()
    assert _reasons(report)["detached"] == "detached"


def test_a_locked_worktree_is_kept(fleet: dict[str, Path]) -> None:
    _git(fleet["plain"], "worktree", "lock", str(fleet["clean"]))

    report = hygiene.plan_agent_worktree_prune([fleet["plain"]], apply=True, min_age_hours=0)

    assert fleet["clean"].exists()
    assert _reasons(report)["clean"] == "locked"


def test_force_is_used_only_for_a_tree_with_submodules(fleet: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    removals: list[tuple[str, bool]] = []
    real_git = hygiene._git

    def spy(cwd: Path, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("worktree", "remove"):
            removals.append((Path(args[-1]).name, "--force" in args))
        return real_git(cwd, *args, **kwargs)

    monkeypatch.setattr(hygiene, "_git", spy)
    _sweep(fleet, apply=True)

    assert sorted(removals) == [("clean", False), ("sub-clean", True), ("sub-uninitialised", True)]


def test_a_submodule_tree_that_turns_dirty_before_removal_is_not_forced(fleet: dict[str, Path]) -> None:
    """The forced removal re-checks the tree itself; it does not trust an earlier verdict."""
    (fleet["sub_clean"] / "vendor" / "late.txt").write_text("x", encoding="utf-8")

    assert hygiene._git_worktree_remove(fleet["with_sub"], fleet["sub_clean"]) is False
    assert fleet["sub_clean"].exists()


def test_files_in_an_uninitialised_submodule_folder_block_removal(fleet: dict[str, Path]) -> None:
    (fleet["sub_uninitialised"] / "vendor").mkdir(exist_ok=True)
    (fleet["sub_uninitialised"] / "vendor" / "notes.txt").write_text("x", encoding="utf-8")

    report = hygiene.plan_agent_worktree_prune([fleet["with_sub"]], apply=True, min_age_hours=0)

    assert fleet["sub_uninitialised"].exists()
    assert _reasons(report)["sub-uninitialised"] in {"dirty", "submodule_dirty"}


def test_the_cli_can_protect_one_more_repo(
    fleet: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = Path(__file__).resolve().parents[2] / "deploy" / "prune_agent_worktrees.py"
    spec = importlib.util.spec_from_file_location("prune_agent_worktrees_cli_4440", script)
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.delenv("AGENT_WORKTREE_HYGIENE_APPLY", raising=False)

    args = ["--repo-root", str(fleet["root"]), "--min-age-hours", "0", "--protect-repo", "plain_repo"]
    assert cli.main(args) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"] is True
    assert report["skipped_by_reason"]["private_repo"] == 4
    assert {item["repo"] for item in report["would_remove"]} == {"Sub_Repo"}
