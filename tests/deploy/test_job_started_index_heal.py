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

Since 2026-10-17 the hook also handles residue a previous job leaves in a
shared workspace (see the second half of this module): sparse / skip-worktree
state is repaired in place when every index blob is present, a partial clone
with missing blobs is quarantined (renamed aside, replaced by an empty dir,
deleted in the background), a healthy promisor clone is left alone, and stale
``.git/**.lock`` files are removed.

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
from bash_host import find_bash

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
    return [line for line in _git(repo, "diff-files", "--name-only").stdout.splitlines() if line]


def _run_hook(workspace: Path, env_extra: dict[str, str] | None = None) -> int:
    env = dict(os.environ)
    env.update(
        {
            "GITHUB_WORKSPACE": str(workspace),
            "RUNNER_BUSY_LOCK_DIR": str(workspace.parent / "_locks"),
            "RUNNER_NAME": "test-runner",
            "HOME": str(workspace.parent),
            # Isolate the reseed sibling scan: by default it walks the parent
            # of the runner dir, which here is pytest's shared base temp dir.
            "RUNNER_HOOK_SEED_ROOT": str(workspace.parent / "_no_seed_root"),
        }
    )
    env.update(env_extra or {})
    return subprocess.run(
        [find_bash() or "bash", str(_HOOK)], env=env, capture_output=True, text=True, check=False
    ).returncode


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_hook_restores_index_stat_cache_after_ctime_bump(tmp_path: Path) -> None:
    """The exact #9443 failure mode: stat-dirty tree, clean content."""
    repo = _make_repo(tmp_path)
    assert _stat_dirty_paths(repo) == [], "fixture must start with a valid stat cache"

    _poison_stat_cache(repo)
    assert _stat_dirty_paths(repo), "fixture failed to invalidate the stat cache; the rest of this test is vacuous"
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
    assert _stat_dirty_paths(outside), "hook must not touch git state outside a runner _work tree"


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

    inode = repo.stat().st_ino

    assert _run_hook(repo) == 0
    assert _git(repo, "config", "--get", "core.sparseCheckout").stdout.strip() == ""
    assert not (repo / ".git" / "info" / "sparse-checkout").exists()
    # Every blob is present, so this is repaired in place, not re-cloned.
    assert repo.stat().st_ino == inode
    assert all((repo / n).is_file() for n in ("a.py", "b.py", "c.py"))


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
    executable = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    for forbidden in ("chown -R", "chmod -R", "chown --recursive", "chmod --recursive"):
        assert forbidden not in executable, f"job-started.sh must never run `{forbidden}` on a workspace (#9443)"


# --- Workspace poisoning: quarantine vs in-place repair (2026-10-17) ---------
#
# Diagnosed on DeskComputer (Gasification_Model): a shared ``_work/<repo>/<repo>``
# inherits a previous job's sparse / skip-worktree residue, partial-clone state
# with *missing* blobs, and stale ``.git/*.lock`` files. A partial clone with
# missing blobs makes the next checkout's pre-auth ``reset --hard`` lazy-fetch
# every one of them without credentials (minutes of "Authentication failed").
# A promisor clone whose index blobs are all present is healthy and must be
# left alone (ControlTower's slow uplink runs 14 of them).


def _blob_missing(repo: Path, oid: str) -> bool:
    """Local-only presence check that can never lazy-fetch."""
    env = {**os.environ, "GIT_ALLOW_PROTOCOL": "none", "GIT_TERMINAL_PROMPT": "0"}
    out = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch-check"],
        input=f"{oid}\n",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return out.strip().endswith("missing")


def _make_source(root: Path) -> Path:
    """A file:// remote that serves partial clones (and would serve lazy fetches)."""
    src = _init_repo(root / "src")
    (src / "sub").mkdir()
    (src / "sub" / "d.py").write_text("# d\n", encoding="utf-8")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "sub")
    _git(src, "config", "uploadpack.allowFilter", "true")
    _git(src, "config", "uploadpack.allowAnySHA1InWant", "true")
    return src


def _clone(src: Path, dest: Path, *extra: str) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "-q", *extra, f"file://{src}", str(dest)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return dest


def _remote_only_blob(src: Path) -> str:
    """A blob that exists on the remote but was never fetched by any clone."""
    return subprocess.run(
        ["git", "-C", str(src), "hash-object", "-w", "--stdin"],
        input="# only on the remote\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _reference_missing_blob(ws: Path, src: Path) -> str:
    oid = _remote_only_blob(src)
    _git(ws, "update-index", "--add", "--cacheinfo", f"100644,{oid},remote_only.py")
    assert _blob_missing(ws, oid), "fixture must reference a blob that is absent locally"
    return oid


def _quarantine_dir(ws: Path) -> Path:
    return ws.parent.parent / "_quarantine"


def _assert_quarantined(ws: Path) -> None:
    assert ws.is_dir(), "the workspace must be recreated: pre-checkout steps use it as cwd"
    assert list(ws.iterdir()) == [], "the recreated workspace must be empty"
    qdir = _quarantine_dir(ws)
    assert qdir.is_dir()
    deadline = time.time() + 15
    while any(qdir.iterdir()) and time.time() < deadline:
        time.sleep(0.1)
    assert not any(qdir.iterdir()), "the quarantined tree must be deleted in the background"


def _skip_worktree_count(repo: Path) -> int:
    return sum(1 for line in _git(repo, "ls-files", "-v").stdout.splitlines() if line[:2] in ("S ", "s "))


def _snapshot(repo: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(repo)): p.read_bytes()
        for p in sorted(repo.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(repo).parts
    }


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_clean_full_workspace_is_left_untouched(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    inode, before = repo.stat().st_ino, _snapshot(repo)

    assert _run_hook(repo) == 0
    assert repo.stat().st_ino == inode
    assert _snapshot(repo) == before
    assert (repo / ".git").is_dir()
    assert not _quarantine_dir(repo).exists() or not any(_quarantine_dir(repo).iterdir())


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_healthy_promisor_clone_is_left_untouched(tmp_path: Path) -> None:
    """blob:none clone with every index blob present: a re-clone would only cost bandwidth."""
    src = _make_source(tmp_path)
    ws = _clone(src, tmp_path / "_work" / "Demo" / "Demo", "--filter=blob:none")
    assert _git(ws, "config", "--get", "remote.origin.promisor").stdout.strip() == "true"
    inode, before = ws.stat().st_ino, _snapshot(ws)

    assert _run_hook(ws) == 0
    assert ws.stat().st_ino == inode
    assert _snapshot(ws) == before
    assert _git(ws, "config", "--get", "remote.origin.promisor").stdout.strip() == "true"


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_promisor_clone_with_a_missing_blob_is_quarantined(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    ws = _clone(src, tmp_path / "_work" / "Demo" / "Demo", "--filter=blob:none")
    _reference_missing_blob(ws, src)

    assert _run_hook(ws) == 0
    _assert_quarantined(ws)


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_sparse_partial_clone_is_quarantined(tmp_path: Path) -> None:
    """The actions/checkout ``sparse-checkout:`` shape: sparse + skip-worktree + unfetched blobs."""
    src = _make_source(tmp_path)
    ws = _clone(src, tmp_path / "_work" / "Demo" / "Demo", "--filter=blob:none", "--sparse")
    assert _skip_worktree_count(ws) > 0, "fixture must carry skip-worktree bits"
    assert not (ws / "sub" / "d.py").exists()

    assert _run_hook(ws) == 0
    _assert_quarantined(ws)


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_blob_check_never_lazy_fetches(tmp_path: Path) -> None:
    """With quarantine disabled the workspace stays, so prove nothing was fetched.

    The remote allows any-SHA1 wants, so a lazy fetch WOULD succeed if the
    check ever triggered one.
    """
    src = _make_source(tmp_path)
    ws = _clone(src, tmp_path / "_work" / "Demo" / "Demo", "--filter=blob:none")
    oid = _reference_missing_blob(ws, src)

    assert _run_hook(ws, {"RUNNER_HOOK_DISABLE_WORKSPACE_QUARANTINE": "1"}) == 0
    assert (ws / ".git").is_dir(), "quarantine must be skippable"
    assert _blob_missing(ws, oid), "the hook lazy-fetched a blob; it must never touch the network"


def _make_sparse_residue(tmp_path: Path) -> Path:
    """A FULL clone left sparse by a previous job: config, patterns and skip-worktree bits."""
    src = _make_source(tmp_path)
    ws = _clone(src, tmp_path / "_work" / "Demo" / "Demo")
    result = _git(ws, "sparse-checkout", "set", "--no-cone", "/a.py")
    assert result.returncode == 0, result.stderr
    assert _skip_worktree_count(ws) > 0
    assert not (ws / "sub" / "d.py").exists()
    return ws


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_sparse_residue_with_all_blobs_is_repaired_in_place(tmp_path: Path) -> None:
    ws = _make_sparse_residue(tmp_path)
    inode = ws.stat().st_ino

    assert _run_hook(ws) == 0
    assert ws.stat().st_ino == inode, "a repairable workspace must not be re-cloned"
    assert _skip_worktree_count(ws) == 0
    assert _git(ws, "config", "--bool", "--get", "core.sparseCheckout").stdout.strip() in ("", "false")
    assert not (ws / ".git" / "info" / "sparse-checkout").exists()
    for name in ("a.py", "b.py", "c.py", "sub/d.py"):
        assert (ws / name).is_file(), f"repair must restore the full tree ({name} missing)"
    assert _git(ws, "status", "--porcelain").stdout.strip() == ""


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_failed_sparse_repair_falls_back_to_quarantine(tmp_path: Path) -> None:
    ws = _make_sparse_residue(tmp_path)
    real_git = shutil.which("git")
    assert real_git
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "git"
    shim.write_text(
        f'#!/bin/sh\nfor a in "$@"; do [ "$a" = sparse-checkout ] && exit 1; done\nexec "{real_git}" "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    shim.chmod(0o755)

    assert _run_hook(ws, {"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"}) == 0
    _assert_quarantined(ws)


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_stale_workspace_locks_are_removed_and_fresh_ones_kept(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    stale = repo / ".git" / "index.lock"
    nested = repo / ".git" / "modules" / "lib" / "HEAD.lock"
    fresh = repo / ".git" / "config.lock"
    nested.parent.mkdir(parents=True)
    for lock in (stale, nested, fresh):
        lock.write_text("", encoding="utf-8")
    old = time.time() - 600
    os.utime(stale, (old, old))
    os.utime(nested, (old, old))

    assert _run_hook(repo) == 0
    assert not stale.exists()
    assert not nested.exists(), "locks under .git/modules/** are stale residue too"
    assert fresh.exists(), "a lock younger than a minute must be left alone"


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_poisoned_repo_outside_a_runner_work_tree_is_never_touched(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    outside = _clone(src, tmp_path / "not_a_runner" / "Demo", "--filter=blob:none", "--sparse")
    _reference_missing_blob(outside, src)
    lock = outside / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")
    old = time.time() - 600
    os.utime(lock, (old, old))
    inode, before = outside.stat().st_ino, _snapshot(outside)

    assert _run_hook(outside) == 0
    assert outside.stat().st_ino == inode
    assert _snapshot(outside) == before
    assert lock.exists()
    assert _skip_worktree_count(outside) > 0


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_orphaned_fetch_temp_packs_are_removed_and_fresh_ones_kept(tmp_path: Path) -> None:
    """A cancelled fetch leaves .git/objects/pack/tmp_pack_* behind (#1979): six on
    DeskComputer runner-3, ~2.4 GB. The lock cleanup skips objects/, so nothing reaped them."""
    repo = _make_repo(tmp_path)
    pack = repo / ".git" / "objects" / "pack"
    pack.mkdir(parents=True, exist_ok=True)
    stale_pack, stale_idx = pack / "tmp_pack_aB12cd", pack / "tmp_idx_Ef34gh"
    nested = repo / ".git" / "modules" / "lib" / "objects" / "pack" / "tmp_pack_Zz99yy"
    fresh = pack / "tmp_pack_fresh0"
    nested.parent.mkdir(parents=True)
    for f in (stale_pack, stale_idx, nested, fresh):
        f.write_bytes(b"PACK")
    old = time.time() - 600
    for f in (stale_pack, stale_idx, nested):
        os.utime(f, (old, old))

    assert _run_hook(repo) == 0
    assert not stale_pack.exists() and not stale_idx.exists()
    assert not nested.exists(), "submodule fetch residue is stale too"
    assert fresh.exists(), "a temp pack younger than a minute must be left alone"


_NEGOTIATION_REF = "refs/runner-cache/last-checkout"


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_previous_head_is_kept_as_a_negotiation_ref(tmp_path: Path) -> None:
    """actions/checkout deletes refs/heads and refs/remotes before fetching, and a
    detached HEAD is never offered as a "have", so every fetch received the full
    snapshot (#1979: 4.56 MiB vs 223 KiB with one kept ref)."""
    repo = _make_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "-q", "--detach")
    _git(repo, "branch", "-D", "main")

    assert _run_hook(repo) == 0
    assert _git(repo, "rev-parse", "--verify", "-q", _NEGOTIATION_REF).stdout.strip() == head


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_negotiation_ref_follows_the_latest_head(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    assert _run_hook(repo) == 0
    (repo / "d.py").write_text("# d\n", encoding="utf-8")
    _git(repo, "add", "d.py")
    _git(repo, "commit", "-qm", "next")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()

    assert _run_hook(repo) == 0
    assert _git(repo, "rev-parse", "--verify", "-q", _NEGOTIATION_REF).stdout.strip() == head


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_repo_without_a_commit_gets_no_negotiation_ref(tmp_path: Path) -> None:
    repo = tmp_path / "_work" / "Demo" / "Demo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")

    assert _run_hook(repo) == 0
    assert _git(repo, "rev-parse", "--verify", "-q", _NEGOTIATION_REF).returncode != 0


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_repo_outside_a_runner_work_tree_gets_no_negotiation_ref(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "not_a_runner" / "Demo")

    assert _run_hook(repo) == 0
    assert _git(repo, "rev-parse", "--verify", "-q", _NEGOTIATION_REF).returncode != 0


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_old_quarantine_entries_are_reaped(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    qdir = _quarantine_dir(repo)
    old = qdir / f"Demo-{int(time.time()) - 7200}-123"
    young = qdir / f"Demo-{int(time.time())}-456"
    for entry in (old, young):
        (entry / "x").mkdir(parents=True)

    assert _run_hook(repo) == 0
    deadline = time.time() + 15
    while old.exists() and time.time() < deadline:
        time.sleep(0.1)
    assert not old.exists(), "entries older than an hour must be reaped"
    assert young.exists(), "a fresh entry is still being deleted by its own job"


# --- Reseed a quarantined workspace from a healthy local sibling --------------
# Six DeskComputer GM workspaces quarantined at once turned into six parallel
# network clones (43 min, cancelled). Rebuilding from a sibling on local disk
# took 11-14 s and left actions/checkout only a delta to fetch.


def _runner_ws(root: Path, runner: str) -> Path:
    return root / runner / "_work" / "Demo" / "Demo"


def _poisoned_ws(root: Path, src: Path) -> Path:
    ws = _clone(src, _runner_ws(root, "runner-1"), "--filter=blob:none")
    _reference_missing_blob(ws, src)
    return ws


def _seed_env(root: Path) -> dict[str, str]:
    return {"RUNNER_HOOK_SEED_ROOT": str(root), "GITHUB_REPOSITORY": "D-sorganization/Demo"}


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_quarantined_workspace_is_reseeded_from_a_healthy_sibling(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    root = tmp_path / "runners"
    sibling = _clone(src, _runner_ws(root, "runner-2"))
    ws = _poisoned_ws(root, src)

    assert _run_hook(ws, _seed_env(root)) == 0

    head = _git(ws, "rev-parse", "--verify", "HEAD")
    assert head.returncode == 0, "the reseeded workspace must have a valid HEAD"
    assert head.stdout == _git(sibling, "rev-parse", "HEAD").stdout
    assert _git(ws, "remote", "get-url", "origin").stdout.strip() == "https://github.com/D-sorganization/Demo"
    assert _git(ws, "config", "--get", "remote.origin.promisor").stdout.strip() == ""
    for name in ("a.py", "b.py", "c.py", "sub/d.py"):
        assert (ws / name).is_file(), f"reseed must check out the tree ({name} missing)"
    assert _skip_worktree_count(ws) == 0
    assert _git(sibling, "status", "--porcelain").stdout.strip() == "", "the sibling is only read"
    assert _git(ws, "rev-parse", "--verify", "-q", _NEGOTIATION_REF).stdout == head.stdout, (
        "the reseeded HEAD must be offered to the next fetch (#1979)"
    )


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_a_sibling_running_a_job_is_not_the_preferred_seed(tmp_path: Path) -> None:
    # `bin` is a symlink to `bin.<version>`, so a live worker's argv reads
    # `<runner>/bin.2.337.0/Runner.Worker`; matching `<runner>/bin/Runner.Worker`
    # saw every runner as idle (2026-10-17, two jobs killed by a reseed).
    src = _make_source(tmp_path)
    root = tmp_path / "runners"
    busy = _clone(src, _runner_ws(root, "runner-2"))
    idle = _clone(src, _runner_ws(root, "runner-3"))
    _git(
        idle, "-c", "user.email=ci@example.invalid", "-c", "user.name=ci", "commit", "-q", "--allow-empty", "-m", "idle"
    )
    ws = _poisoned_ws(root, src)
    worker_argv0 = f"{root / 'runner-2' / 'bin.2.337.0' / 'Runner.Worker'} spawnclient 1 2"
    worker = subprocess.Popen([find_bash() or "bash", "-c", 'exec -a "$0" sleep 60', worker_argv0])
    try:
        assert _run_hook(ws, _seed_env(root)) == 0
    finally:
        worker.kill()
        worker.wait()

    assert _git(ws, "rev-parse", "HEAD").stdout == _git(idle, "rev-parse", "HEAD").stdout, "the idle sibling must seed"
    assert _git(ws, "rev-parse", "HEAD").stdout != _git(busy, "rev-parse", "HEAD").stdout


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_quarantine_without_a_sibling_leaves_an_empty_dir(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    root = tmp_path / "runners"
    ws = _poisoned_ws(root, src)

    assert _run_hook(ws, _seed_env(root)) == 0
    assert ws.is_dir() and list(ws.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_reseed_never_clones_into_the_live_workspace(tmp_path: Path) -> None:
    # #1982: a reseed clone into the workspace itself hung for 97 min after its
    # timeout's SIGTERM, its clean-up deleting everything the next four jobs'
    # checkouts wrote there. The clone must build aside and be renamed in.
    src = _make_source(tmp_path)
    root = tmp_path / "runners"
    _clone(src, _runner_ws(root, "runner-2"))
    ws = _poisoned_ws(root, src)
    real_git = shutil.which("git")
    assert real_git
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    log = tmp_path / "clone_targets.log"
    shim = shim_dir / "git"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'case " $* " in *" clone "*) printf \'%s\\n\' "${@: -1}" >> "$CLONE_LOG" ;; esac\n'
        f'exec "{real_git}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    env = _seed_env(root) | {"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}", "CLONE_LOG": str(log)}

    assert _run_hook(ws, env) == 0

    targets = [Path(t).resolve() for t in log.read_text(encoding="utf-8").split()]
    assert targets, "fixture must exercise the reseed clone; the rest of this test is vacuous"
    assert ws.resolve() not in targets, "reseed must clone into a staging dir, then rename it into place"
    assert _git(ws, "rev-parse", "--verify", "HEAD").returncode == 0, "the renamed-in workspace must be usable"
    assert not any(t.exists() for t in targets), "the staging dir must not linger after a successful reseed"


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_unhealthy_siblings_are_never_used_as_seeds(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    root = tmp_path / "runners"
    _clone(src, _runner_ws(root, "runner-2"), "--filter=blob:none")  # promisor
    _clone(src, _runner_ws(root, "runner-3"), "--filter=blob:none", "--sparse")  # sparse + promisor
    sparse_full = _clone(src, _runner_ws(root, "runner-4"))
    _git(sparse_full, "sparse-checkout", "set", "--no-cone", "/a.py")  # skip-worktree bits
    locked = _clone(src, _runner_ws(root, "runner-5"))
    (locked / ".git" / "index.lock").write_text("", encoding="utf-8")  # a writer is active
    _runner_ws(root, "runner-6").mkdir(parents=True)  # no repository at all
    ws = _poisoned_ws(root, src)

    assert _run_hook(ws, _seed_env(root)) == 0
    assert ws.is_dir() and list(ws.iterdir()) == [], "no unhealthy sibling may seed a workspace"
    assert (locked / ".git" / "index.lock").exists(), "a sibling's locks are not this job's to remove"


@pytest.mark.skipif(os.name != "posix", reason="hook is a POSIX shell script")
def test_quarantine_herd_is_logged_loudly(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    root = tmp_path / "runners"
    ws = _poisoned_ws(root, src)
    locks = ws.parent / "_locks"
    locks.mkdir()
    now = int(time.time())
    (locks / "workspace-quarantines").write_text("".join(f"{now - i}\n" for i in range(5)), encoding="utf-8")
    env = {**os.environ, **_seed_env(root)}
    env.update(
        {
            "GITHUB_WORKSPACE": str(ws),
            "RUNNER_BUSY_LOCK_DIR": str(locks),
            "RUNNER_NAME": "test-runner",
            "HOME": str(ws.parent),
        }
    )
    result = subprocess.run([find_bash() or "bash", str(_HOOK)], env=env, capture_output=True, text=True, check=False)

    assert result.returncode == 0
    assert "quarantine herd" in result.stderr
    assert ws.is_dir() and list(ws.iterdir()) == [], "a herd still quarantines"
