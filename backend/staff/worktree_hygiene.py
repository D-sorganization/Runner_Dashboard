"""Worktree and merged-branch lifecycle hygiene for Staff runs (#2269).

Persists run transcripts outside ephemeral git worktrees, removes finished clean
worktrees on run exit, and prunes ancestor-merged local/remote branches.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from staff import cli_projects

log = logging.getLogger("dashboard.staff.worktree_hygiene")


def persist_run_transcript(store: Any, rec_id: str, workdir: Path) -> Path | None:
    """Atomically persist bytes and verify their run association before cleanup."""
    src = workdir / ".staff" / "transcript.log"
    if not src.is_file():
        return None
    db_path = getattr(store, "path", None) or getattr(store, "db_path", None)
    base_dir = Path(db_path).parent if db_path else (Path.home() / ".config" / "runner-dashboard")
    dst_dir = base_dir / "transcripts"
    temporary: Path | None = None
    try:
        if store.get_run(rec_id) is None or Path(rec_id).name != rec_id:
            return None
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"{rec_id}.log"
        if dst.resolve().is_relative_to(workdir.resolve()):
            return None
        with tempfile.NamedTemporaryFile(dir=dst_dir, delete=False) as output:
            temporary = Path(output.name)
            with src.open("rb") as source:
                shutil.copyfileobj(source, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, dst)
        if os.name != "nt":
            directory = os.open(dst_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        if not _same_content(src, dst):
            return None
        store.update_run(rec_id, transcript_path=str(dst))
        record = store.get_run(rec_id)
        return dst if record is not None and record.transcript_path == str(dst) else None
    except Exception as exc:  # noqa: BLE001 — any persistence failure must preserve the source
        log.warning("failed to persist transcript for %s: %s", rec_id, exc)
        return None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("temporary transcript retained at %s: %s", temporary, exc)


def _same_content(source: Path, destination: Path) -> bool:
    with source.open("rb") as left, destination.open("rb") as right:
        return hashlib.file_digest(left, "sha256").digest() == hashlib.file_digest(right, "sha256").digest()


def _path_key(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _has_links(path: Path) -> bool:
    def linked(candidate: Path) -> bool:
        return candidate.is_symlink() or bool(
            getattr(candidate.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )

    if any(linked(candidate) for candidate in (path, *path.parents)):
        return True
    pending = [path]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                if linked(Path(entry.path)):
                    return True
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
    return False


def _pid_is_running(pid: int) -> bool:
    import psutil  # noqa: PLC0415 — minimal tooling imports must not require runtime probes

    return psutil.pid_exists(pid)


def remove_worktree_checked(
    worktree: Path, checkout: Path | None, branch: str, preserved_transcript: Path | None = None
) -> bool:
    """Never force Git removal or recursively erase a refused/unknown checkout."""
    from staff import workspace  # noqa: PLC0415

    try:
        root = workspace.staff_worktrees_root()
        if _has_links(worktree):
            return False
        if (worktree / ".git").exists():
            if _path_key(worktree.parent) != _path_key(root):
                return False
            source = worktree / ".staff" / "transcript.log"
            if source.exists() and (
                preserved_transcript is None
                or preserved_transcript.resolve().is_relative_to(worktree.resolve())
                or not _same_content(source, preserved_transcript)
            ):
                return False
            result = _git_out(checkout or worktree, "worktree", "remove", str(worktree), timeout=60)
            if result.returncode != 0 or worktree.exists():
                return False
        else:
            worktree.rmdir()  # Only empty non-Git scratch directories are disposable.
        try:
            workspace.prune_worktree_branch(checkout, branch)
            if _path_key(worktree.parent) == _path_key(root):
                cli_projects.remove_worktree_project(worktree, root, os.environ)
        except Exception as exc:  # noqa: BLE001 — secondary cleanup cannot undo confirmed removal
            log.warning("worktree removed but administrative cleanup failed for %s: %s", worktree, exc)
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("worktree removal preserved %s: %s", worktree, exc)
        return False


def _cleanup_finished_worktree(store: Any, rec_id: str, workdir: Path, repo: str | None, branch: str = "") -> bool:
    """Pre: terminal owned run, clean direct child, no live PID. Post: evidence survives refusal."""
    from staff import workspace  # noqa: PLC0415

    try:
        record = store.get_run(rec_id)
        if record is None or _path_key(record.workdir) != _path_key(workdir):
            return False
        if record.status not in {"succeeded", "failed", "cancelled"} or (record.pid and _pid_is_running(record.pid)):
            return False
        if record.failure_class == "orphaned":
            return False  # Attempted termination is not proof that all descendants stopped.
        root = workspace.staff_worktrees_root()
        if (
            _path_key(workdir.parent) != _path_key(root)
            or workdir.name == "_scratch"
            or not (workdir / ".git").is_file()
        ):
            if not _has_links(workdir / ".staff"):
                persist_run_transcript(store, rec_id, workdir)
            return False  # Owned cloud/scratch runs retain logging without becoming cleanup candidates.
        if _has_links(workdir):
            return False
        if workspace.worktree_has_unpushed_commits(workdir):
            store.append_event(rec_id, "worktree", f"unpushed worktree preserved at {workdir}")
            return False
        saved = persist_run_transcript(store, rec_id, workdir)
        if saved is None or not _same_content(workdir / ".staff" / "transcript.log", saved):
            return False
        workspace.remove_worktree(
            workdir,
            checkout=workspace.find_repo_checkout(repo) if repo else None,
            branch=branch,
            preserved_transcript=saved,
        )
        return not workdir.exists()
    except Exception as exc:  # noqa: BLE001 — uncertainty at the destructive boundary preserves data
        log.warning("finished worktree preserved at %s: %s", workdir, exc)
        return False


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
    if workdir is not None and workdir.exists():
        _cleanup_finished_worktree(store, rec_id, workdir, repo, branch)


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

    empty = {"removed": 0, "preserved_unpushed": 0, "skipped_active": 0}
    try:
        active_norm = {_path_key(p) for p in (active_workdirs or set()) if p}
        active_norm.update(_path_key(rec.workdir) for rec in store.active_runs() if rec.workdir)
    except Exception as exc:  # noqa: BLE001 — no authoritative active exclusion means no cleanup
        log.warning("worktree sweep preserved all candidates: %s", exc)
        return empty

    removed = 0
    preserved_unpushed = 0
    skipped_active = 0
    for child in sorted(wt_root.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name == "_scratch":
            continue
        if not (child / ".git").exists():
            continue
        if _path_key(child) in active_norm:
            skipped_active += 1
            continue
        if "-run-" not in child.name:
            continue
        try:
            if _has_links(child):
                continue
        except OSError as exc:
            log.warning("worktree inspection preserved %s: %s", child, exc)
            continue
        if workspace.worktree_has_unpushed_commits(child):
            preserved_unpushed += 1
            continue
        repo_prefix, suffix = child.name.rsplit("-run-", 1)
        removed += int(_cleanup_finished_worktree(store, "run-" + suffix, child, repo_prefix))
    return {"removed": removed, "preserved_unpushed": preserved_unpushed, "skipped_active": skipped_active}
