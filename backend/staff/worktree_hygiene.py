"""Worktree and merged-branch lifecycle hygiene for Staff runs (#2269).

Persists run transcripts outside ephemeral git worktrees, removes finished clean
worktrees on run exit, and prunes ancestor-merged local/remote branches.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger("dashboard.staff.worktree_hygiene")


def persist_run_transcript(store: Any, rec_id: str, workdir: Path) -> Path | None:
    """Copy ``workdir/.staff/transcript.log`` into the config transcripts dir and update ``store``."""
    src = workdir / ".staff" / "transcript.log"
    if not src.is_file():
        return None
    db_path = getattr(store, "db_path", None)
    base_dir = Path(db_path).parent if db_path else (Path.home() / ".config" / "runner-dashboard")
    dst_dir = base_dir / "transcripts"
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"{rec_id}.log"
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        store.update_run(rec_id, transcript_path=str(dst))
        return dst
    except OSError as exc:
        log.warning("failed to persist transcript for %s: %s", rec_id, exc)
        return None


def _git_out(cwd: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(cwd), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _active_worktree_branches(checkout: Path) -> set[str]:
    res = _git_out(checkout, "worktree", "list", "--porcelain")
    if res.returncode != 0:
        return set()
    branches: set[str] = set()
    for line in res.stdout.splitlines():
        if line.startswith("branch refs/heads/"):
            branches.add(line.removeprefix("branch refs/heads/").strip())
    return branches


def prune_merged_branches(checkout: Path, *, delete_remote_merged: bool = False) -> dict[str, int]:
    """Prune stale worktree refs, local merged/gone branches, and optionally merged remote staff/* branches."""
    if not checkout.exists() or not (checkout / ".git").exists():
        return {"local_deleted": 0, "remote_deleted": 0}
    try:
        _git_out(checkout, "fetch", "origin", "--prune", "--quiet", timeout=60)
        _git_out(checkout, "worktree", "prune")
    except Exception as exc:  # noqa: BLE001
        log.debug("fetch/worktree prune skipped on %s: %s", checkout, exc)

    head_res = _git_out(checkout, "symbolic-ref", "--quiet", "--short", "HEAD")
    head_branch = head_res.stdout.strip() if head_res.returncode == 0 else ""
    protected = {"main", "master", "HEAD", ""} | _active_worktree_branches(checkout)
    if head_branch:
        protected.add(head_branch)

    to_delete: set[str] = set()
    merged_res = _git_out(checkout, "branch", "--merged", "origin/main", "--format=%(refname:short)")
    if merged_res.returncode == 0:
        for line in merged_res.stdout.splitlines():
            b = line.strip()
            if b and b not in protected:
                to_delete.add(b)

    refs_res = _git_out(checkout, "for-each-ref", "--format=%(refname:short)|%(upstream:track)", "refs/heads")
    if refs_res.returncode == 0:
        for line in refs_res.stdout.splitlines():
            if "|" not in line:
                continue
            b, track = line.split("|", 1)
            b = b.strip()
            if b in protected or b in to_delete:
                continue
            if ": gone]" in track:
                ahead = _git_out(checkout, "rev-list", f"origin/main..{b}", "--count")
                if ahead.returncode == 0 and ahead.stdout.strip() == "0":
                    to_delete.add(b)

    local_deleted = 0
    for b in sorted(to_delete):
        del_res = _git_out(checkout, "branch", "-D", b)
        if del_res.returncode == 0:
            local_deleted += 1

    remote_deleted = 0
    if delete_remote_merged:
        rm_res = _git_out(checkout, "branch", "-r", "--merged", "origin/main", "--format=%(refname:short)")
        if rm_res.returncode == 0:
            for line in rm_res.stdout.splitlines():
                rb = line.strip()
                if not rb.startswith("origin/staff/"):
                    continue
                short_rb = rb.removeprefix("origin/")
                push_res = _git_out(checkout, "push", "origin", "--delete", short_rb, timeout=60)
                if push_res.returncode == 0:
                    remote_deleted += 1

    return {"local_deleted": local_deleted, "remote_deleted": remote_deleted}


def finalize_run_worktree(
    store: Any,
    rec_id: str,
    workdir: Path | None,
    repo: str | None,
    *,
    branch: str = "",
) -> None:
    """Persist transcript and remove clean finished git worktrees under ``staff_worktrees_root``."""
    if workdir is None or not workdir.exists():
        return
    from staff import workspace  # noqa: PLC0415

    persist_run_transcript(store, rec_id, workdir)
    if not (workdir / ".git").exists():
        return

    shutil.rmtree(workdir / ".staff", ignore_errors=True)
    wt_root = workspace.staff_worktrees_root()
    if os.path.normpath(str(workdir.parent)) != os.path.normpath(str(wt_root)) or workdir.name == "_scratch":
        return

    if workspace.worktree_has_unpushed_commits(workdir):
        store.append_event(rec_id, "worktree", f"unpushed worktree preserved at {workdir}")
        return

    checkout = workspace.find_repo_checkout(repo) if repo else None
    workspace.remove_worktree(workdir, checkout=checkout, branch=branch)


def prune_worktree_branch(checkout: Path | None, branch: str = "") -> None:
    """Prune stale worktree administrative entries and delete ``branch`` if merged into origin/main."""
    if checkout is None or not checkout.exists():
        return
    try:
        _git_out(checkout, "worktree", "prune")
        if branch and branch not in ("main", "master", "HEAD"):
            ahead = _git_out(checkout, "rev-list", f"origin/main..{branch}", "--count")
            if ahead.returncode == 0 and ahead.stdout.strip() == "0":
                _git_out(checkout, "branch", "-D", branch)
    except Exception as exc:  # noqa: BLE001
        log.debug("prune_worktree_branch skipped on %s: %s", checkout, exc)


def sweep_stale_worktrees(
    store: Any = None,
    *,
    active_workdirs: set[str] | None = None,
) -> dict[str, int]:
    """Prune finished clean git worktrees under ``staff_worktrees_root`` (#2538)."""
    from staff import workspace  # noqa: PLC0415

    wt_root = workspace.staff_worktrees_root()
    if not wt_root.is_dir():
        return {"removed": 0, "preserved_unpushed": 0, "skipped_active": 0}

    active_norm: set[str] = {os.path.normpath(p) for p in (active_workdirs or set()) if p}
    if store is not None and hasattr(store, "active_runs"):
        try:
            for rec in store.active_runs():
                if getattr(rec, "workdir", None):
                    active_norm.add(os.path.normpath(str(rec.workdir)))
        except Exception as exc:  # noqa: BLE001
            log.debug("active_runs lookup skipped during worktree sweep: %s", exc)

    removed = 0
    preserved_unpushed = 0
    skipped_active = 0
    for child in sorted(wt_root.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name == "_scratch":
            continue
        if not (child / ".git").exists():
            continue
        if os.path.normpath(str(child)) in active_norm:
            skipped_active += 1
            continue
        if store is not None and "-run-" in child.name:
            rec_id = "run-" + child.name.rsplit("-run-", 1)[-1]
            persist_run_transcript(store, rec_id, child)
        shutil.rmtree(child / ".staff", ignore_errors=True)
        if workspace.worktree_has_unpushed_commits(child):
            preserved_unpushed += 1
            continue
        repo_prefix = child.name.split("-run-", 1)[0] if "-run-" in child.name else ""
        checkout = workspace.find_repo_checkout(repo_prefix) if repo_prefix else None
        workspace.remove_worktree(child, checkout=checkout)
        removed += 1
    return {"removed": removed, "preserved_unpushed": preserved_unpushed, "skipped_active": skipped_active}
