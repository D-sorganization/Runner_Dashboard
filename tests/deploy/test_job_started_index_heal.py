"""Behavioural guards for the job-started workspace heal.

Context — UpstreamDrift#9443 / Repository_Management#1505.

Self-hosted jobs were failing right after checkout on files their PR never
touched, with ``actions/checkout`` itself reported as **successful**::

    error: Path 'src/config/tile_registry.py' not uptodate; \
will not remove from working tree.
    ...
    python3: can't open file '.../scripts/ci/rehydrate_docker_context.py'

Two workspace conditions produce that, and the hook clears both:

1. **Sparse-checkout left on with no patterns.** An empty pattern set matches
   nothing, so unpack-trees concludes every path belongs *outside* the working
   tree: ``git checkout --force`` empties the tree and still exits 0. The
   quoted message is git's ``WARNING_SPARSE_NOT_UPTODATE_FILE`` — a warning on
   the sparse code path, which is why the step passes and the job then dies on
   a missing file.
2. **A stale index stat cache.** A recursive ``chown -R`` over a runner's
   ``_work`` tree bumps every inode's ctime without touching content, so every
   tracked path reads as stat-dirty while ``git status`` still calls the tree
   clean. Measured live: 13,220 of 13,224 tracked paths on ControlTower
   runner-4. This selects *which* paths get the warning above.

These tests execute the real hook against seeded temporary git repositories,
so they pin behaviour rather than substrings.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
_HOOK = _REPO / "deploy" / "runner-hooks" / "job-started.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="requires bash and git",
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _init_repo(repo: Path) -> Path:
    """Seed a tiny git repo whose index stat cache is valid.

    File mtimes are backdated before ``git add`` so the entries are not
    *racily clean* (mtime equal to the index's own mtime). Racy entries are
    re-hashed by git on every comparison, which would mask the stat-only
    breakage this module is about.
    """
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "ci@example.invalid")
    _git(repo, "config", "user.name", "ci")
    backdated = time.time() - 3600
    for name in ("a.py", "b.py", "c.py"):
        path = repo / name
        path.write_text(f"# {name}\n", encoding="utf-8")
        os.utime(path, (backdated, backdated))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    return repo


def _make_repo(root: Path) -> Path:
    """Seed a repo at a realistic runner ``_work`` path."""
    return _init_repo(root / "_work" / "Demo" / "Demo")


def _poison_stat_cache(repo: Path) -> None:
    """Invalidate the index's cached stat data while leaving content intact.

    In production the field that moved was **ctime**, bumped on every inode of
    a runner's tree by a recursive ``chown -R``. ctime cannot be set directly
    and an unprivileged same-uid ``chown`` is a kernel no-op, so the fixture
    diverges ``mtime`` instead: the git-level condition it produces is the one
    that matters and the one the hook must heal -- ``git diff-files`` reports
    every path, while content (and therefore ``git status``) is unchanged.
    """
    backdated = time.time() - 86400
    for path in sorted(repo.rglob("*.py")):
        os.utime(path, (backdated, backdated))


def _stat_dirty_paths(repo: Path) -> list[str]:
    return [
        line
        for line in _git(repo, "diff-files", "--name-only").stdout.splitlines()
        if line
    ]


def _run_hook(workspace: Path, env_extra: dict[str, str] | None = None) -> int:
    env = dict(os.environ)
    env.update(
        {
            "GITHUB_WORKSPACE": str(workspace),
            "RUNNER_BUSY_LOCK_DIR": str(workspace.parent / "_locks"),
            "RUNNER_NAME": "test-runner",
            "HOME": str(workspace.parent),
        }
    )
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", str(_HOOK)], env=env, capture_output=True, text=True, check=False
    ).returncode


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_hook_restores_index_stat_cache_after_ctime_bump(tmp_path: Path) -> None:
    """The exact #9443 failure mode: stat-dirty tree, clean content."""
    repo = _make_repo(tmp_path)
    assert _stat_dirty_paths(repo) == [], "fixture must start with a valid stat cache"

    _poison_stat_cache(repo)
    assert _stat_dirty_paths(repo), (
        "fixture failed to invalidate the stat cache; the rest of this test is vacuous"
    )
    # Content is untouched, which is why `git status` never repairs the index.
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""

    assert _run_hook(repo) == 0
    assert _stat_dirty_paths(repo) == [], (
        "job-started.sh must refresh the index stat cache so a subsequent "
        "`git checkout --force` can delete tracked files"
    )


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_hook_preserves_real_local_modifications(tmp_path: Path) -> None:
    """The heal must never masquerade edited content as unchanged."""
    repo = _make_repo(tmp_path)
    (repo / "a.py").write_text("# edited\n", encoding="utf-8")

    assert _run_hook(repo) == 0
    assert "a.py" in _git(repo, "status", "--porcelain").stdout


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_hook_ignores_workspaces_outside_a_runner_work_tree(tmp_path: Path) -> None:
    """Guard: the heal is confined to paths under a runner ``_work`` dir."""
    outside = _init_repo(tmp_path / "not_a_runner" / "Demo")
    _poison_stat_cache(outside)
    assert _stat_dirty_paths(outside), "fixture failed to invalidate the stat cache"

    assert _run_hook(outside) == 0
    assert _stat_dirty_paths(outside), (
        "hook must not touch git state outside a runner _work tree"
    )


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_hook_survives_a_missing_workspace(tmp_path: Path) -> None:
    """First job on a fresh runner: the workspace does not exist yet."""
    assert _run_hook(tmp_path / "_work" / "Demo" / "Demo") == 0


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_hook_clears_sparse_checkout_left_on_with_no_patterns(tmp_path: Path) -> None:
    """``core.sparseCheckout=true`` + empty pattern file empties the tree.

    An empty pattern set matches nothing, so unpack-trees treats every path as
    belonging outside the working tree and ``git checkout --force`` deletes it
    all while still exiting 0.
    """
    repo = _make_repo(tmp_path)
    _git(repo, "config", "core.sparseCheckout", "true")
    (repo / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (repo / ".git" / "info" / "sparse-checkout").write_text("", encoding="utf-8")

    assert _run_hook(repo) == 0
    assert _git(repo, "config", "--get", "core.sparseCheckout").stdout.strip() == ""
    assert not (repo / ".git" / "info" / "sparse-checkout").exists()


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_hook_leaves_a_genuine_sparse_checkout_alone(tmp_path: Path) -> None:
    """Config on *and* patterns present is a real sparse checkout, not damage."""
    repo = _make_repo(tmp_path)
    _git(repo, "config", "core.sparseCheckout", "true")
    patterns = repo / ".git" / "info" / "sparse-checkout"
    patterns.parent.mkdir(parents=True, exist_ok=True)
    patterns.write_text("/a.py\n", encoding="utf-8")

    assert _run_hook(repo) == 0
    assert _git(repo, "config", "--get", "core.sparseCheckout").stdout.strip() == "true"
    assert patterns.read_text(encoding="utf-8") == "/a.py\n"


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_incoherent_sparse_state_empties_the_tree_without_the_hook(
    tmp_path: Path,
) -> None:
    """Pin the upstream behaviour the hook exists to prevent.

    If git ever stops emptying the tree in this state the guard can be
    retired -- but that must be a deliberate decision, not a silent drift.
    """
    repo = _make_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "other")
    (repo / "added.py").write_text("# added\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "next")
    _git(repo, "checkout", "-q", "-f", "main")

    _git(repo, "config", "core.sparseCheckout", "true")
    (repo / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (repo / ".git" / "info" / "sparse-checkout").write_text("", encoding="utf-8")

    result = _git(repo, "checkout", "--progress", "--force", "other")
    assert result.returncode == 0, "the damage is silent: checkout still exits 0"
    assert not (repo / "a.py").exists(), "expected the tree to be emptied"


def test_hook_never_chowns_or_chmods_the_workspace() -> None:
    """Regression guard on the *inverted* fix.

    A recursive ownership/permission sweep is the cause of #9443, not a
    remedy: doing it here would invalidate the index stat cache on every
    job instead of once in a while.
    """
    src = _HOOK.read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    for forbidden in ("chown -R", "chmod -R", "chown --recursive", "chmod --recursive"):
        assert forbidden not in executable, (
            f"job-started.sh must never run `{forbidden}` on a workspace (#9443)"
        )
